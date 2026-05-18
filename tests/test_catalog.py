from pathlib import Path
import os
import threading

import pytest

from sv.catalog import (
    SourceSkill,
    build_source_catalog,
    build_source_catalog_from_backends,
    search_source_catalog,
    find_qualified_catalog_entry,
)
from sv.config import RepoConfig, SvPaths
from sv.errors import SvError
from sv.source import SourceBackendError, FakeSourceBackend


def make_skill(repo_path: Path, name: str, description: str) -> None:
    make_skill_at(repo_path, Path("skills") / name, name, description)


def make_skill_at(
    repo_path: Path, relative_path: Path | str, name: str, description: str
) -> None:
    skill_dir = repo_path / relative_path
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"
    )


class FailingBackend:
    name = "broken-backend"

    def read_file(self, path: str) -> bytes:
        raise AssertionError("catalog should move to the next backend")

    def read_index(self) -> bytes | None:
        return None

    def list_candidate_skill_files(self, configured_skills_paths=()):
        raise SourceBackendError(
            "listing candidate SKILL.md files",
            "simulated outage",
            hint="try again later",
        )

    def materialize_folder(self, source_path: str, destination: Path) -> None:
        raise AssertionError("catalog should not materialize folders during discovery")


class UnsafeCandidateBackend:
    name = "unsafe-backend"

    def read_file(self, path: str) -> bytes:
        raise AssertionError("unsafe candidate paths should fail before reading")

    def read_index(self) -> bytes | None:
        return None

    def list_candidate_skill_files(self, configured_skills_paths=()):
        return ["../evil/SKILL.md"]

    def materialize_folder(self, source_path: str, destination: Path) -> None:
        raise AssertionError("catalog should not materialize folders during discovery")


class IndexedBackend:
    name = "indexed-backend"

    def __init__(self, index_text: str):
        self.index_text = index_text
        self.read_file_calls: list[str] = []
        self.list_candidate_calls = 0

    def read_file(self, path: str) -> bytes:
        self.read_file_calls.append(path)
        raise AssertionError("indexed catalog discovery should not read skill files")

    def read_index(self) -> bytes | None:
        return self.index_text.encode("utf-8")

    def list_candidate_skill_files(self, configured_skills_paths=()):
        self.list_candidate_calls += 1
        raise AssertionError(
            "indexed catalog discovery should not run generic discovery"
        )

    def materialize_folder(self, source_path: str, destination: Path) -> None:
        raise AssertionError("catalog should not materialize folders during discovery")


class BackendCallSentinel:
    name = "backend-call-sentinel"

    def __init__(self, called: threading.Event):
        self.called = called

    def read_index(self) -> bytes | None:
        self.called.set()
        raise AssertionError("safe backend should not start when another repo cache path is unsafe")

    def list_candidate_skill_files(self, configured_skills_paths=()):
        raise AssertionError("safe backend should not list files")

    def read_file(self, path: str) -> bytes:
        raise AssertionError("safe backend should not read files")

    def materialize_folder(self, source_path: str, destination: Path) -> None:
        raise AssertionError("safe backend should not materialize folders")


class PeerWaitingBackend:
    name = "peer-waiting"

    def __init__(
        self, skill_name: str, started: threading.Event, peer_started: threading.Event
    ):
        self.skill_name = skill_name
        self.started = started
        self.peer_started = peer_started

    def read_index(self) -> bytes | None:
        self.started.set()
        assert self.peer_started.wait(2), (
            "independent repo discovery did not run concurrently"
        )
        return None

    def list_candidate_skill_files(self, configured_skills_paths=()):
        return [f"skills/{self.skill_name}/SKILL.md"]

    def read_file(self, path: str) -> bytes:
        return (
            "---\n"
            f"name: {self.skill_name}\n"
            f"description: {self.skill_name.title()} skill.\n"
            "---\n"
        ).encode("utf-8")

    def materialize_folder(self, source_path: str, destination: Path) -> None:
        raise AssertionError("catalog discovery must not materialize folders")


def test_build_source_catalog_from_backends_rejects_unsafe_cache_paths_before_parallel_backend_work(
    tmp_path: Path,
) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support is required")
    paths = SvPaths.from_home(tmp_path)
    unsafe_repo = RepoConfig(id="Unsafe/Repo", url="https://github.com/Unsafe/Repo.git")
    safe_repo = RepoConfig(id="Safe/Repo", url="https://github.com/Safe/Repo.git")
    outside_unsafe = tmp_path / "outside-unsafe"
    outside_unsafe.mkdir()
    paths.sources_dir.mkdir(parents=True)
    os.symlink(outside_unsafe, paths.sources_dir / "Unsafe")
    safe_backend_called = threading.Event()

    with pytest.raises(SvError, match="Source cache path must not contain symlinks"):
        build_source_catalog_from_backends(
            [unsafe_repo, safe_repo],
            paths,
            {safe_repo.id: (BackendCallSentinel(safe_backend_called),)},
            jobs=2,
        )

    assert not safe_backend_called.is_set()


def test_build_source_catalog_from_backends_refreshes_independent_repos_in_parallel(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo_a = RepoConfig(id="Org/A", url="https://github.com/Org/A.git")
    repo_b = RepoConfig(id="Org/B", url="https://github.com/Org/B.git")
    a_started = threading.Event()
    b_started = threading.Event()

    result = build_source_catalog_from_backends(
        [repo_a, repo_b],
        paths,
        {
            repo_a.id: (PeerWaitingBackend("alpha", a_started, b_started),),
            repo_b.id: (PeerWaitingBackend("beta", b_started, a_started),),
        },
        jobs=2,
    )

    assert [(entry.name, entry.repo_id) for entry in result.entries] == [
        ("alpha", "Org/A"),
        ("beta", "Org/B"),
    ]
    assert result.failures == ()
    assert result.refreshed_repo_ids == ("Org/A", "Org/B")


def test_search_source_catalog_uses_case_insensitive_ranked_text_and_fuzzy_matches(
    tmp_path: Path,
):
    catalog = [
        SourceSkill(
            name="find-docs",
            description="Retrieves current documentation.",
            repo_id="Org/Primary",
            repo_url="https://github.com/Org/Primary.git",
            repo_path=tmp_path / "primary",
            source_path=tmp_path / "primary" / "skills" / "find-docs",
        ),
        SourceSkill(
            name="docs-helper",
            description="Finds docs for release notes.",
            repo_id="Org/Secondary",
            repo_url="https://github.com/Org/Secondary.git",
            repo_path=tmp_path / "secondary",
            source_path=tmp_path / "secondary" / "skills" / "docs-helper",
        ),
        SourceSkill(
            name="deep-skill",
            description="Specialized helper.",
            repo_id="Team/Deep",
            repo_url="https://github.com/Team/Deep.git",
            repo_path=tmp_path / "deep",
            source_path=tmp_path / "deep" / "packages" / "pi" / "skills" / "deep-skill",
            source_relative_path="packages/pi/skills/deep-skill",
        ),
    ]

    assert [entry.name for entry in search_source_catalog(catalog, "DOCS")] == [
        "docs-helper",
        "find-docs",
    ]
    assert [entry.name for entry in search_source_catalog(catalog, "fd")] == [
        "find-docs"
    ]
    assert [entry.name for entry in search_source_catalog(catalog, "tmdeep")] == [
        "deep-skill"
    ]
    assert [entry.name for entry in search_source_catalog(catalog, "packages/pi")] == [
        "deep-skill"
    ]
    assert search_source_catalog(catalog, "   ") == []
    assert search_source_catalog(catalog, "q") == []


def test_build_source_catalog_from_backend_uses_index_without_reading_skill_files(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    backend = IndexedBackend(
        """
        schema_version = 1
        kind = "skill-vault"
        generated_by = "sv"
        generated_at = "2026-05-15T00:00:00Z"

        [[skills]]
        name = "alpha"
        description = "Alpha from index."
        source_path = "skills/alpha"
        content_hash = "sha256:8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8"
        skill_file_hash = "sha256:63b6db06d8ca622e5986709498b492dfc53aad421a238dba44b7146d37d934bf"

        [[skills]]
        name = "deep"
        description = "Deep from index."
        source_path = "packages/pi/skills/deep"
        content_hash = "sha256:74611c1d6455b534323a21f8133a6f43dc3a8188e7b946f96dcc28dde932fcb2"
        skill_file_hash = "sha256:0d3a78084b5d74bb21cb33cc0982b382fba1c43eabfb584a048219a23f2eef40"
        """
    )

    result = build_source_catalog_from_backends([repo], paths, {repo.id: (backend,)})

    assert [
        (entry.name, entry.description, entry.source_relative_path)
        for entry in result.entries
    ] == [
        ("alpha", "Alpha from index.", "skills/alpha"),
        ("deep", "Deep from index.", "packages/pi/skills/deep"),
    ]
    assert [entry.source_backend for entry in result.entries] == [
        "indexed-backend",
        "indexed-backend",
    ]
    assert [entry.source_content_hash for entry in result.entries] == [
        "sha256:8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8",
        "sha256:74611c1d6455b534323a21f8133a6f43dc3a8188e7b946f96dcc28dde932fcb2",
    ]
    assert [entry.source_skill_file_hash for entry in result.entries] == [
        "sha256:63b6db06d8ca622e5986709498b492dfc53aad421a238dba44b7146d37d934bf",
        "sha256:0d3a78084b5d74bb21cb33cc0982b382fba1c43eabfb584a048219a23f2eef40",
    ]
    assert backend.read_file_calls == []
    assert backend.list_candidate_calls == 0
    assert result.failures == ()


def test_build_source_catalog_from_backend_rejects_index_name_source_path_mismatch(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    backend = IndexedBackend(
        """
        schema_version = 1
        kind = "skill-vault"
        generated_by = "sv"
        generated_at = "2026-05-15T00:00:00Z"

        [[skills]]
        name = "alpha"
        description = "Alpha from index."
        source_path = "skills/beta"
        content_hash = "sha256:8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8"
        skill_file_hash = "sha256:63b6db06d8ca622e5986709498b492dfc53aad421a238dba44b7146d37d934bf"
        """
    )

    result = build_source_catalog_from_backends([repo], paths, {repo.id: (backend,)})

    assert result.entries == ()
    assert len(result.failures) == 1
    assert (
        "source_path 'skills/beta' does not match skill name 'alpha'"
        in result.failure_report()
    )
    assert backend.read_file_calls == []
    assert backend.list_candidate_calls == 0


def test_build_source_catalog_from_backend_reports_future_index_schema(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Future", url="https://github.com/Org/Future.git")
    backend = IndexedBackend(
        """
        schema_version = 999
        kind = "skill-vault"
        generated_by = "future-sv"
        generated_at = "2026-05-15T00:00:00Z"
        """
    )

    result = build_source_catalog_from_backends([repo], paths, {repo.id: (backend,)})

    assert result.entries == ()
    assert len(result.failures) == 1
    assert "update sv" in result.failure_report()
    assert "schema_version 999" in result.failure_report()
    assert backend.list_candidate_calls == 0


def test_build_source_catalog_from_backend_falls_back_when_index_missing(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    backend = FakeSourceBackend(
        {
            "skills/alpha/SKILL.md": "---\nname: alpha\ndescription: Alpha skill.\n---\n",
        }
    )

    result = build_source_catalog_from_backends([repo], paths, {repo.id: (backend,)})

    assert [(entry.name, entry.source_relative_path) for entry in result.entries] == [
        ("alpha", "skills/alpha"),
    ]
    assert result.failures == ()


def test_build_source_catalog_from_backend_discovers_valid_skills_without_git(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(
        id="Org/Skills",
        url="https://github.com/Org/Skills.git",
        skills_paths=("packages/agents/pi/skills",),
    )
    backend = FakeSourceBackend(
        {
            "skills/alpha/SKILL.md": "---\nname: alpha\ndescription: Alpha skill.\n---\n",
            "team/skills/beta/SKILL.md": "---\nname: beta\ndescription: Beta skill.\n---\n",
            "packages/agents/pi/skills/deep/SKILL.md": (
                "---\nname: deep\ndescription: Deep skill.\n---\n"
            ),
            "too/deep/skills/ignored/SKILL.md": "---\nname: ignored\ndescription: Ignored.\n---\n",
            "skills/bad/SKILL.md": "---\nname: other\ndescription: Bad.\n---\n",
        }
    )

    result = build_source_catalog_from_backends([repo], paths, {repo.id: (backend,)})

    assert [
        (entry.name, entry.source_relative_path, entry.source_backend)
        for entry in result.entries
    ] == [
        ("alpha", "skills/alpha", "fake"),
        ("beta", "team/skills/beta", "fake"),
        ("deep", "packages/agents/pi/skills/deep", "fake"),
    ]
    assert result.failures == ()


def test_build_source_catalog_from_backend_warns_and_skips_invalid_candidates(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    backend = FakeSourceBackend(
        {
            "skills/valid/SKILL.md": "---\nname: valid\ndescription: Valid skill.\n---\n",
            "skills/missing-description/SKILL.md": (
                "---\nname: missing-description\n---\n"
            ),
            "team/skills/wrong-name/SKILL.md": (
                "---\nname: other\ndescription: Wrong name.\n---\n"
            ),
            "skills/not-frontmatter/SKILL.md": "# no frontmatter\n",
        }
    )
    warnings: list[str] = []

    result = build_source_catalog_from_backends(
        [repo], paths, {repo.id: (backend,)}, warn=warnings.append
    )

    assert [(entry.name, entry.source_relative_path) for entry in result.entries] == [
        ("valid", "skills/valid"),
    ]
    assert result.failures == ()
    assert len(warnings) == 3
    assert all(
        warning.startswith("warning: skipping invalid skill at ")
        for warning in warnings
    )
    warning_text = "\n".join(warnings)
    assert "skills/missing-description" in warning_text
    assert "missing description" in warning_text
    assert "team/skills/wrong-name" in warning_text
    assert "does not match folder" in warning_text
    assert "skills/not-frontmatter" in warning_text
    assert "frontmatter" in warning_text


def test_build_source_catalog_from_backend_treats_non_utf8_skill_file_as_backend_failure(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    backend = FakeSourceBackend({"skills/not-utf8/SKILL.md": b"\xff\xfe\x00"})
    warnings: list[str] = []

    result = build_source_catalog_from_backends(
        [repo], paths, {repo.id: (backend,)}, warn=warnings.append
    )

    assert result.entries == ()
    assert warnings == []
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.backend == "fake"
    assert failure.operation == "reading candidate SKILL.md file"
    assert "not valid UTF-8" in failure.detail


def test_build_source_catalog_from_backend_collects_failures_and_tries_next(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    working_backend = FakeSourceBackend(
        {
            "skills/alpha/SKILL.md": "---\nname: alpha\ndescription: Alpha skill.\n---\n",
        }
    )

    result = build_source_catalog_from_backends(
        [repo], paths, {repo.id: (FailingBackend(), working_backend)}
    )

    assert [entry.name for entry in result.entries] == ["alpha"]
    assert len(result.failures) == 1
    assert result.failures[0].user_message == (
        "broken-backend backend failed for Org/Skills while listing candidate SKILL.md files: "
        "simulated outage. try again later"
    )


def test_build_source_catalog_from_backend_reports_all_backend_failures(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")

    result = build_source_catalog_from_backends(
        [repo], paths, {repo.id: (FailingBackend(),)}
    )

    assert result.entries == ()
    assert [failure.backend for failure in result.failures] == ["broken-backend"]
    assert "simulated outage" in result.failure_report()


def test_build_source_catalog_from_backend_reports_unsafe_candidate_path(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")

    result = build_source_catalog_from_backends(
        [repo], paths, {repo.id: (UnsafeCandidateBackend(),)}
    )

    assert result.entries == ()
    assert [failure.backend for failure in result.failures] == ["unsafe-backend"]
    assert "unsafe path components" in result.failure_report()


def test_build_source_catalog_from_backend_rejects_symlinked_source_cache_path(
    tmp_path: Path,
):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support is required")
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    outside_org = tmp_path / "outside-org"
    outside_org.mkdir()
    paths.sources_dir.mkdir(parents=True)
    os.symlink(outside_org, paths.sources_dir / "Org")

    with pytest.raises(SvError, match="Source cache path must not contain symlinks"):
        build_source_catalog_from_backends(
            [repo], paths, {repo.id: (FakeSourceBackend({}),)}
        )


def test_build_source_catalog_from_backend_rejects_symlinked_source_descendant(
    tmp_path: Path,
):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support is required")
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    repo_path.mkdir(parents=True)
    outside_skills = tmp_path / "outside-skills"
    outside_skills.mkdir()
    os.symlink(outside_skills, repo_path / "skills")
    backend = FakeSourceBackend(
        {
            "skills/alpha/SKILL.md": "---\nname: alpha\ndescription: Alpha.\n---\n",
        }
    )

    with pytest.raises(SvError, match="Source skills path must not contain symlinks"):
        build_source_catalog_from_backends([repo], paths, {repo.id: (backend,)})


def test_build_source_catalog_returns_valid_skills_with_repo_context(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    make_skill(repo_path, "beta", "Beta skill.")
    make_skill(repo_path, "alpha", "Alpha skill.")

    catalog = build_source_catalog([repo], paths)

    assert catalog == [
        SourceSkill(
            name="alpha",
            description="Alpha skill.",
            repo_id="Org/Skills",
            repo_url="https://github.com/Org/Skills.git",
            repo_path=repo_path,
            source_path=repo_path / "skills" / "alpha",
        ),
        SourceSkill(
            name="beta",
            description="Beta skill.",
            repo_id="Org/Skills",
            repo_url="https://github.com/Org/Skills.git",
            repo_path=repo_path,
            source_path=repo_path / "skills" / "beta",
        ),
    ]


def test_build_source_catalog_sorts_by_skill_name_then_repo_id(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    alpha = RepoConfig(id="A/Skills", url="https://github.com/A/Skills.git")
    beta = RepoConfig(id="B/Skills", url="https://github.com/B/Skills.git")
    make_skill(paths.source_repo_for(beta.id), "zeta", "B zeta.")
    make_skill(paths.source_repo_for(beta.id), "alpha", "B alpha.")
    make_skill(paths.source_repo_for(alpha.id), "same", "A skill.")
    make_skill(paths.source_repo_for(beta.id), "same", "B skill.")

    catalog = build_source_catalog([beta, alpha], paths)

    assert [(entry.name, entry.repo_id) for entry in catalog] == [
        ("alpha", "B/Skills"),
        ("same", "A/Skills"),
        ("same", "B/Skills"),
        ("zeta", "B/Skills"),
    ]


def test_build_source_catalog_discovers_bounded_and_configured_skill_roots(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(
        id="Org/Skills",
        url="https://github.com/Org/Skills.git",
        skills_paths=("packages/agents/pi/skills",),
    )
    repo_path = paths.source_repo_for(repo.id)
    make_skill_at(repo_path, "skills/alpha", "alpha", "Root alpha.")
    make_skill_at(repo_path, "team/skills/team-alpha", "team-alpha", "Team alpha.")
    make_skill_at(
        repo_path,
        "packages/agents/pi/skills/deep-alpha",
        "deep-alpha",
        "Deep alpha.",
    )
    make_skill_at(repo_path, "too/deep/skills/ignored", "ignored", "Ignored.")
    make_skill_at(repo_path, ".pi/skills/project-local", "project-local", "Ignored.")

    catalog = build_source_catalog([repo], paths)

    assert [(entry.name, entry.source_relative_path) for entry in catalog] == [
        ("alpha", "skills/alpha"),
        ("deep-alpha", "packages/agents/pi/skills/deep-alpha"),
        ("team-alpha", "team/skills/team-alpha"),
    ]
    assert [entry.source_path for entry in catalog] == [
        repo_path / "skills" / "alpha",
        repo_path / "packages" / "agents" / "pi" / "skills" / "deep-alpha",
        repo_path / "team" / "skills" / "team-alpha",
    ]


def test_same_repo_duplicate_skills_have_path_aware_labels_and_references(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    make_skill_at(repo_path, "skills/alpha", "alpha", "Root alpha.")
    make_skill_at(repo_path, "team/skills/alpha", "alpha", "Team alpha.")

    catalog = build_source_catalog([repo], paths)

    assert [(entry.name, entry.source_relative_path) for entry in catalog] == [
        ("alpha", "skills/alpha"),
        ("alpha", "team/skills/alpha"),
    ]
    assert catalog[0].display_label == "alpha  Org/Skills  Root alpha."
    assert catalog[1].display_label == (
        "alpha  Org/Skills:team/skills/alpha  Team alpha."
    )
    assert find_qualified_catalog_entry(catalog, "Org/Skills:alpha") == catalog[0]
    assert (
        find_qualified_catalog_entry(catalog, "Org/Skills:team/skills/alpha")
        == catalog[1]
    )


def test_build_source_catalog_ignores_exact_repeated_repo_entries(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    make_skill(paths.source_repo_for(repo.id), "alpha", "Alpha skill.")

    catalog = build_source_catalog([repo, repo], paths)

    assert [(entry.name, entry.repo_id) for entry in catalog] == [
        ("alpha", "Org/Skills")
    ]


def test_build_source_catalog_ignores_same_repo_id_with_equivalent_url(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    https_repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    ssh_repo = RepoConfig(id="Org/Skills", url="git@github.com:Org/Skills.git")
    make_skill(paths.source_repo_for(https_repo.id), "alpha", "Alpha skill.")

    catalog = build_source_catalog([https_repo, ssh_repo], paths)

    assert [(entry.name, entry.repo_id) for entry in catalog] == [
        ("alpha", "Org/Skills")
    ]


def test_build_source_catalog_rejects_same_repo_id_with_different_urls(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    make_skill(paths.source_repo_for("Org/Skills"), "alpha", "Alpha skill.")
    first_repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    conflicting_repo = RepoConfig(
        id="Org/Skills", url="https://github.com/Other/Skills.git"
    )

    with pytest.raises(
        SvError,
        match="Configured source repo id 'Org/Skills' is listed more than once",
    ):
        build_source_catalog([first_repo, conflicting_repo], paths)


def test_build_source_catalog_ignores_repeated_repo_urls(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    duplicate_url = RepoConfig(
        id="Mirror/Skills", url="https://github.com/Org/Skills.git"
    )
    make_skill(paths.source_repo_for(repo.id), "alpha", "Alpha skill.")
    make_skill(paths.source_repo_for(duplicate_url.id), "alpha", "Alpha skill.")

    catalog = build_source_catalog([repo, duplicate_url], paths)

    assert [(entry.name, entry.repo_id, entry.repo_aliases) for entry in catalog] == [
        ("alpha", "Org/Skills", ("Mirror/Skills",))
    ]


def test_build_source_catalog_ignores_equivalent_github_repo_urls(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills")
    duplicate_url = RepoConfig(id="Mirror/Skills", url="git@github.com:Org/Skills.git")
    make_skill(paths.source_repo_for(repo.id), "alpha", "Alpha skill.")
    make_skill(paths.source_repo_for(duplicate_url.id), "alpha", "Alpha skill.")

    catalog = build_source_catalog([repo, duplicate_url], paths)

    assert [(entry.name, entry.repo_id, entry.repo_aliases) for entry in catalog] == [
        ("alpha", "Org/Skills", ("Mirror/Skills",))
    ]


def test_find_qualified_catalog_entry_matches_repo_alias(tmp_path: Path):
    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=tmp_path / "source",
        source_path=tmp_path / "source" / "skills" / "alpha",
        repo_aliases=("Mirror/Skills",),
    )

    assert find_qualified_catalog_entry([entry], "Mirror/Skills:alpha") is entry


def test_source_skill_exposes_relative_path_and_display_label(tmp_path: Path):
    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=tmp_path / "source",
        source_path=tmp_path / "source" / "skills" / "alpha",
    )

    assert entry.source_relative_path == "skills/alpha"
    assert entry.display_label == "alpha  Org/Skills  Alpha skill."


def test_build_source_catalog_skips_unsafe_top_level_candidate_root_names(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    make_skill(repo_path, "valid", "Valid skill.")
    make_skill_at(repo_path, "bad:name/skills/ignored", "ignored", "Ignored.")
    make_skill_at(repo_path, "skills/skills/nested", "nested", "Nested.")

    catalog = build_source_catalog([repo], paths)

    assert [(entry.name, entry.source_relative_path) for entry in catalog] == [
        ("valid", "skills/valid"),
    ]

    configured_catalog = build_source_catalog(
        [
            RepoConfig(
                id="Org/Skills",
                url="https://github.com/Org/Skills.git",
                skills_paths=("skills/skills",),
            )
        ],
        paths,
    )

    assert ("nested", "skills/skills/nested") in [
        (entry.name, entry.source_relative_path) for entry in configured_catalog
    ]


def test_build_source_catalog_skips_invalid_skills(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    make_skill(repo_path, "valid", "Valid skill.")
    invalid = repo_path / "skills" / "invalid"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text("---\nname: other\ndescription: Bad.\n---\n")

    catalog = build_source_catalog([repo], paths)

    assert [entry.name for entry in catalog] == ["valid"]


def test_build_source_catalog_skips_missing_skills_root(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Empty", url="https://github.com/Org/Empty.git")
    paths.source_repo_for(repo.id).mkdir(parents=True)

    catalog = build_source_catalog([repo], paths)

    assert catalog == []


def test_build_source_catalog_skips_non_directories_in_skills_root(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    make_skill(repo_path, "valid", "Valid skill.")
    skills_root = repo_path / "skills"
    (skills_root / "README.md").write_text("# not a skill\n")

    catalog = build_source_catalog([repo], paths)

    assert [entry.name for entry in catalog] == ["valid"]


def test_build_source_catalog_skips_hidden_skill_directories(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    make_skill(repo_path, "valid", "Valid skill.")
    hidden = repo_path / "skills" / ".hidden"
    hidden.mkdir(parents=True)
    (hidden / "SKILL.md").write_text(
        "---\nname: .hidden\ndescription: Hidden skill.\n---\n"
    )

    catalog = build_source_catalog([repo], paths)

    assert [entry.name for entry in catalog] == ["valid"]


def test_build_source_catalog_skips_multiple_invalid_skills_without_hiding_valid_ones(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    make_skill(repo_path, "zeta", "Zeta skill.")
    make_skill(repo_path, "alpha", "Alpha skill.")
    missing_metadata = repo_path / "skills" / "missing-metadata"
    missing_metadata.mkdir(parents=True)
    mismatched = repo_path / "skills" / "mismatched"
    mismatched.mkdir()
    (mismatched / "SKILL.md").write_text(
        "---\nname: other\ndescription: Wrong folder.\n---\n"
    )
    empty_description = repo_path / "skills" / "empty-description"
    empty_description.mkdir()
    (empty_description / "SKILL.md").write_text(
        "---\nname: empty-description\ndescription:   \n---\n"
    )

    catalog = build_source_catalog([repo], paths)

    assert [entry.name for entry in catalog] == ["alpha", "zeta"]


def test_build_source_catalog_skips_symlinked_skill_directories(tmp_path: Path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support is required")
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    make_skill(repo_path, "valid", "Valid skill.")
    outside = tmp_path / "outside-skill"
    outside.mkdir()
    (outside / "SKILL.md").write_text(
        "---\nname: linked\ndescription: Linked skill.\n---\n"
    )
    os.symlink(outside, repo_path / "skills" / "linked")

    catalog = build_source_catalog([repo], paths)

    assert [entry.name for entry in catalog] == ["valid"]


def test_build_source_catalog_rejects_symlinked_skills_root(tmp_path: Path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support is required")
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    repo_path.mkdir(parents=True)
    outside_skills = tmp_path / "outside-skills"
    outside_skills.mkdir()
    os.symlink(outside_skills, repo_path / "skills")

    with pytest.raises(SvError, match="Source skills path must not be a symlink"):
        build_source_catalog([repo], paths)


def test_build_source_catalog_rejects_symlinked_configured_root_ancestor(
    tmp_path: Path,
):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support is required")
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(
        id="Org/Skills",
        url="https://github.com/Org/Skills.git",
        skills_paths=("linked/skills",),
    )
    repo_path = paths.source_repo_for(repo.id)
    repo_path.mkdir(parents=True)
    outside = tmp_path / "outside"
    (outside / "skills" / "alpha").mkdir(parents=True)
    os.symlink(outside, repo_path / "linked")

    with pytest.raises(SvError, match="Source skills path must not contain symlinks"):
        build_source_catalog([repo], paths)


def test_build_source_catalog_rejects_symlinked_cache_ancestor(tmp_path: Path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support is required")
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    outside_org = tmp_path / "outside-org"
    outside_org.mkdir()
    paths.sources_dir.mkdir(parents=True)
    os.symlink(outside_org, paths.sources_dir / "Org")

    with pytest.raises(SvError, match="Source cache path must not contain symlinks"):
        build_source_catalog([repo], paths)


def test_build_source_catalog_skips_control_character_skill_names(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    make_skill(repo_path, "valid", "Valid skill.")
    make_skill(repo_path, "bad\x1bname", "Bad skill.")

    catalog = build_source_catalog([repo], paths)

    assert [entry.name for entry in catalog] == ["valid"]


def test_build_source_catalog_reports_unreadable_skill_files(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    unreadable = repo_path / "skills" / "broken"
    unreadable.mkdir(parents=True)
    (unreadable / "SKILL.md").write_bytes(b"\xff\xfe\x00")

    with pytest.raises(SvError, match="Failed to read SKILL.md"):
        build_source_catalog([repo], paths)


def test_build_source_catalog_reports_skill_directory_listing_failures(
    tmp_path: Path, monkeypatch
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    skills_root = paths.source_repo_for(repo.id) / "skills"
    skills_root.mkdir(parents=True)

    original_iterdir = Path.iterdir

    def fail_iterdir(path):
        if path == skills_root:
            raise OSError("cannot list")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)

    with pytest.raises(SvError, match="Failed to list source skills"):
        build_source_catalog([repo], paths)

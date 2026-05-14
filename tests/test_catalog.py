from pathlib import Path
import os

import pytest

from sv.catalog import SourceSkill, build_source_catalog, find_qualified_catalog_entry
from sv.config import RepoConfig, SvPaths
from sv.errors import SvError


def make_skill(repo_path: Path, name: str, description: str) -> None:
    skill_dir = repo_path / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"
    )


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
    make_skill(paths.source_repo_for(beta.id), "same", "B skill.")
    make_skill(paths.source_repo_for(alpha.id), "same", "A skill.")

    catalog = build_source_catalog([beta, alpha], paths)

    assert [(entry.name, entry.repo_id) for entry in catalog] == [
        ("same", "A/Skills"),
        ("same", "B/Skills"),
    ]


def test_build_source_catalog_ignores_exact_repeated_repo_entries(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    make_skill(paths.source_repo_for(repo.id), "alpha", "Alpha skill.")

    catalog = build_source_catalog([repo, repo], paths)

    assert [(entry.name, entry.repo_id) for entry in catalog] == [
        ("alpha", "Org/Skills")
    ]


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
    duplicate_url = RepoConfig(
        id="Mirror/Skills", url="git@github.com:Org/Skills.git"
    )
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

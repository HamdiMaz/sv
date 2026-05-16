from pathlib import Path
import os
import subprocess

import pytest

from sv.catalog import SourceSkill, build_source_catalog
from sv.config import RepoConfig, SvPaths
from sv.errors import SvError
from sv.project import add_project_skill
from sv.hashing import sha256_file, sha256_skill_directory
from sv.skills import parse_skill_file
from sv.source import ensure_source_repo, ensure_source_repos
from tests.helpers import write_source_skill


pytestmark = [
    pytest.mark.security,
    pytest.mark.skipif(
        not hasattr(os, "symlink"),
        reason="symlink support is required",
    ),
]


def test_ensure_source_repo_rejects_symlinked_cache_path_before_git(
    tmp_path: Path,
) -> None:
    real_repo = tmp_path / "real-repo"
    (real_repo / ".git").mkdir(parents=True)
    (real_repo / "sentinel.txt").write_text("outside\n")
    repo_path = tmp_path / "repo-link"
    repo_path.symlink_to(real_repo, target_is_directory=True)
    calls: list[tuple[list[str], Path | None]] = []

    def git_runner(args, cwd=None):
        calls.append((list(args), cwd))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="",
            stderr="",
        )

    with pytest.raises(SvError, match="Source repo cache path must not be a symlink"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=git_runner)

    assert calls == []
    assert (real_repo / "sentinel.txt").read_text() == "outside\n"


def test_ensure_source_repo_rejects_symlinked_cache_ancestor_before_git(
    tmp_path: Path,
) -> None:
    outside_sources = tmp_path / "outside-sources"
    outside_sources.mkdir()
    (outside_sources / "sentinel.txt").write_text("outside\n")
    symlinked_sources = tmp_path / "sources-link"
    symlinked_sources.symlink_to(outside_sources, target_is_directory=True)
    repo_path = symlinked_sources / "Org" / "Skills" / "repo"

    calls: list[tuple[list[str], Path | None]] = []

    def git_runner(args, cwd=None):
        calls.append((list(args), cwd))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="",
            stderr="",
        )

    with pytest.raises(SvError, match="Source cache path must not contain symlinks"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=git_runner)

    assert calls == []
    assert (outside_sources / "sentinel.txt").read_text() == "outside\n"


def test_ensure_source_repos_rejects_symlinked_cache_path_before_git(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.sources_dir.mkdir(parents=True)
    repo_path = paths.source_repo_for("Org/Skills")
    repo_path.parent.mkdir(parents=True, exist_ok=True)

    outside_repo = tmp_path / "outside-repo"
    outside_repo.mkdir()
    (outside_repo / "sentinel.txt").write_text("outside\n")
    repo_path.symlink_to(outside_repo, target_is_directory=True)
    calls: list[tuple[list[str], Path | None]] = []

    def git_runner(args, cwd=None):
        calls.append((list(args), cwd))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="",
            stderr="",
        )

    with pytest.raises(SvError, match="Source cache path must not contain symlinks"):
        ensure_source_repos(
            [RepoConfig(id="Org/Skills", url="https://example.com/skills.git")],
            paths,
            runner=git_runner,
        )

    assert calls == []
    assert (outside_repo / "sentinel.txt").read_text() == "outside\n"


def test_ensure_source_repos_rejects_symlinked_cache_ancestor_before_git(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.sources_dir.mkdir(parents=True)
    outside_org = tmp_path / "outside-org"
    outside_org.mkdir()
    (outside_org / "sentinel.txt").write_text("outside\n")
    (paths.sources_dir / "Org").symlink_to(outside_org, target_is_directory=True)
    calls: list[tuple[list[str], Path | None]] = []

    def git_runner(args, cwd=None):
        calls.append((list(args), cwd))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="",
            stderr="",
        )

    with pytest.raises(SvError, match="Source cache path must not contain symlinks"):
        ensure_source_repos(
            [RepoConfig(id="Org/Skills", url="https://example.com/skills.git")],
            paths,
            runner=git_runner,
        )

    assert calls == []
    assert (outside_org / "sentinel.txt").read_text() == "outside\n"


def test_ensure_source_repos_rejects_symlinked_cache_ancestor_before_git_for_batch(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.sources_dir.mkdir(parents=True)
    outside_org = tmp_path / "outside-org"
    outside_org.mkdir()
    (outside_org / "sentinel.txt").write_text("outside\n")
    (paths.sources_dir / "Org").symlink_to(outside_org, target_is_directory=True)
    calls: list[tuple[list[str], Path | None]] = []

    def git_runner(args, cwd=None):
        calls.append((list(args), cwd))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="",
            stderr="",
        )

    with pytest.raises(SvError, match="Source cache path must not contain symlinks"):
        ensure_source_repos(
            [
                RepoConfig(id="Org/Skills", url="https://example.com/skills.git"),
                RepoConfig(id="Other/Skills", url="https://example.com/other.git"),
            ],
            paths,
            runner=git_runner,
        )

    assert calls == []
    assert (outside_org / "sentinel.txt").read_text() == "outside\n"


def test_ensure_source_repo_rejects_symlinked_git_metadata_before_git(
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    outside_git = tmp_path / "outside-git"
    outside_git.mkdir()
    (outside_git / "sentinel.txt").write_text("outside\n")
    (repo_path / ".git").symlink_to(outside_git, target_is_directory=True)
    calls: list[tuple[list[str], Path | None]] = []

    def git_runner(args, cwd=None):
        calls.append((list(args), cwd))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="",
            stderr="",
        )

    with pytest.raises(SvError, match="Source Git metadata path must not be a symlink"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=git_runner)

    assert calls == []
    assert (outside_git / "sentinel.txt").read_text() == "outside\n"


def test_build_source_catalog_rejects_symlinked_skills_root(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    repo_path.mkdir(parents=True)
    outside = tmp_path / "outside-skills"
    outside.mkdir()
    (outside / "sentinel.md").write_text("outside\n")
    (outside / "alpha").mkdir(exist_ok=True)
    os.symlink(outside, repo_path / "skills", target_is_directory=True)

    with pytest.raises(SvError, match="Source skills path must not be a symlink"):
        build_source_catalog([repo], paths)

    assert (outside / "sentinel.md").read_text() == "outside\n"


def test_build_source_catalog_skips_symlinked_skill_directory(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    write_source_skill(repo_path, "valid", "Valid skill.", "valid\n")
    outside = tmp_path / "outside-link"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("outside\n")
    (outside / "SKILL.md").write_text(
        "---\nname: linked\ndescription: Linked skill.\n---\n"
    )
    os.symlink(outside, repo_path / "skills" / "linked", target_is_directory=True)

    catalog = build_source_catalog([repo], paths)

    assert [entry.name for entry in catalog] == ["valid"]
    assert (outside / "sentinel.txt").read_text() == "outside\n"


def test_parse_skill_file_rejects_symlinked_skill_file(tmp_path: Path) -> None:
    skill_dir = tmp_path / "source" / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    outside = tmp_path / "outside-skill-file"
    outside.write_text("outside\n")
    skill_file = skill_dir / "SKILL.md"
    os.symlink(outside, skill_file, target_is_directory=False)

    with pytest.raises(SvError, match="must not be a symlink"):
        parse_skill_file(skill_file, expected_folder="alpha")

    assert outside.read_text() == "outside\n"


def test_sha256_file_rejects_symlinked_file(tmp_path: Path) -> None:
    outside = tmp_path / "outside-file"
    outside.write_text("outside\n")
    skill_file = tmp_path / "SKILL.md"
    os.symlink(outside, skill_file, target_is_directory=False)

    with pytest.raises(SvError, match="must not be a symlink"):
        sha256_file(skill_file)

    assert outside.read_text() == "outside\n"


def test_sha256_file_rejects_symlinked_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside-parent"
    outside.mkdir()
    outside_file = outside / "alpha.txt"
    outside_file.write_text("outside\n")
    linked_parent = tmp_path / "linked-parent"
    os.symlink(outside, linked_parent, target_is_directory=True)

    with pytest.raises(SvError, match="must not contain symlinks"):
        sha256_file(linked_parent / "alpha.txt")

    assert outside_file.read_text() == "outside\n"


def test_sha256_skill_directory_rejects_symlink_inside_tree(tmp_path: Path) -> None:
    skill_dir = tmp_path / "alpha"
    skill_dir.mkdir()
    outside = tmp_path / "outside-data"
    outside.write_text("outside\n")
    os.symlink(outside, skill_dir / "secret.txt", target_is_directory=False)

    with pytest.raises(SvError, match="contains a symlink"):
        sha256_skill_directory(skill_dir)

    assert outside.read_text() == "outside\n"


def test_sha256_skill_directory_rejects_symlinked_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside-parent"
    outside_skill = outside / "alpha"
    outside_skill.mkdir(parents=True)
    (outside_skill / "SKILL.md").write_text("outside\n")
    linked_parent = tmp_path / "linked-parent"
    os.symlink(outside, linked_parent, target_is_directory=True)

    with pytest.raises(SvError, match="must not contain symlinks"):
        sha256_skill_directory(linked_parent / "alpha")

    assert (outside_skill / "SKILL.md").read_text() == "outside\n"


def test_add_project_skill_rejects_symlink_inside_source_skill_tree(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_source_skill(source, "alpha", "Alpha skill.", "alpha\n")
    outside = tmp_path / "outside-data"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside\n")
    os.symlink(outside / "secret.txt", source / "skills" / "alpha" / "secret.txt")
    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=source,
        source_path=source / "skills" / "alpha",
    )

    with pytest.raises(SvError, match="contains a symlink"):
        add_project_skill(entry, tmp_path / "project" / ".pi" / "skills")

    assert not (tmp_path / "project" / ".pi" / "skills" / "alpha").exists()
    assert (outside / "secret.txt").read_text() == "outside\n"


def test_add_project_skill_rejects_symlinked_source_skill_directory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "skills").mkdir(parents=True)
    outside = tmp_path / "outside-alpha"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("outside\n")
    source_skill = source / "skills" / "alpha"
    source_skill.symlink_to(outside, target_is_directory=True)
    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=source,
        source_path=source_skill,
    )

    with pytest.raises(SvError, match="contains a symlink"):
        add_project_skill(entry, tmp_path / "project" / ".pi" / "skills")

    assert not (tmp_path / "project" / ".pi" / "skills" / "alpha").exists()
    assert (outside / "sentinel.txt").read_text() == "outside\n"

from pathlib import Path
import subprocess

import pytest

from sv.config import RepoConfig, SvPaths
from sv.errors import SvError
from sv.source import ensure_source_repo, ensure_source_repos, list_source_skills


class FakeRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args, cwd=None):
        self.calls.append((list(args), cwd))
        if not self.results:
            raise AssertionError(f"unexpected command: {args}")
        return self.results.pop(0)


def completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=args, returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_ensure_source_repo_clones_when_missing(tmp_path: Path):
    repo_path = tmp_path / ".sv" / "sources" / "default" / "repo"
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(["git", "clone"]),
        ]
    )

    ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    assert runner.calls == [
        (["git", "--version"], None),
        (
            ["git", "clone", "--", "https://example.com/skills.git", str(repo_path)],
            None,
        ),
    ]
    assert repo_path.parent.exists()


def test_ensure_source_repo_rejects_option_like_repo_url(tmp_path: Path):
    repo_path = tmp_path / ".sv" / "sources" / "default" / "repo"
    runner = FakeRunner([])

    with pytest.raises(SvError, match="repo cannot start with '-'"):
        ensure_source_repo("--upload-pack=/tmp/fake", repo_path, runner=runner)

    assert runner.calls == []


def test_ensure_source_repo_rejects_symlinked_cache_path(tmp_path: Path):
    if not hasattr(Path, "symlink_to"):
        pytest.skip("symlink support is required")
    real_repo = tmp_path / "real-repo"
    (real_repo / ".git").mkdir(parents=True)
    repo_path = tmp_path / "repo-link"
    repo_path.symlink_to(real_repo, target_is_directory=True)
    runner = FakeRunner([])

    with pytest.raises(SvError, match="Source repo cache path must not be a symlink"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    assert runner.calls == []


def test_ensure_source_repo_pulls_existing_clone(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(
                ["git", "remote", "get-url", "origin"],
                stdout="https://example.com/skills.git\n",
            ),
            completed(["git", "pull", "--ff-only"]),
        ]
    )

    ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    assert runner.calls == [
        (["git", "--version"], None),
        (["git", "remote", "get-url", "origin"], repo_path),
        (["git", "pull", "--ff-only"], repo_path),
    ]


def test_ensure_source_repo_can_skip_pull_for_existing_clone(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(
                ["git", "remote", "get-url", "origin"],
                stdout="https://example.com/skills.git\n",
            ),
        ]
    )

    ensure_source_repo(
        "https://example.com/skills.git", repo_path, runner=runner, update=False
    )

    assert runner.calls == [
        (["git", "--version"], None),
        (["git", "remote", "get-url", "origin"], repo_path),
    ]


def test_ensure_source_repo_reports_remote_mismatch(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(
                ["git", "remote", "get-url", "origin"],
                stdout="https://example.com/other.git\n",
            ),
        ]
    )

    with pytest.raises(SvError, match="existing source clone uses"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)


def test_ensure_source_repo_reports_git_failure(tmp_path: Path):
    repo_path = tmp_path / "repo"
    runner = FakeRunner(
        [
            completed(["git", "--version"], returncode=1, stderr="git missing\n"),
        ]
    )

    with pytest.raises(SvError, match="Checking Git availability failed"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)


def test_ensure_source_repo_reports_non_git_existing_path(tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
        ]
    )

    with pytest.raises(SvError, match="exists but is not a Git clone"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)


def test_ensure_source_repo_reports_cache_directory_creation_failures(tmp_path: Path):
    repo_path = tmp_path / ".sv" / "sources" / "Org" / "Skills" / "repo"
    blocker = tmp_path / ".sv" / "sources"
    blocker.parent.mkdir(parents=True)
    blocker.write_text("not a directory\n")
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
        ]
    )

    with pytest.raises(SvError, match="Failed to prepare source cache directory"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)


def test_list_source_skills_lists_immediate_skill_folders_only(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / "skills" / "alpha").mkdir(parents=True)
    (repo_path / "skills" / "beta").mkdir(parents=True)
    (repo_path / "skills" / "alpha" / "nested").mkdir()
    (repo_path / "skills" / "not-a-dir.md").write_text("not a skill folder\n")

    assert list_source_skills(repo_path) == ["alpha", "beta"]


def test_list_source_skills_returns_empty_when_skills_dir_missing(tmp_path: Path):
    assert list_source_skills(tmp_path / "repo") == []


def test_ensure_source_repos_rejects_symlinked_cache_ancestor(tmp_path: Path):
    if not hasattr(Path, "symlink_to"):
        pytest.skip("symlink support is required")
    paths = SvPaths.from_home(tmp_path)
    outside_org = tmp_path / "outside-org"
    outside_org.mkdir()
    paths.sources_dir.mkdir(parents=True)
    (paths.sources_dir / "Org").symlink_to(outside_org, target_is_directory=True)
    repos = [RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")]
    runner = FakeRunner([])

    with pytest.raises(SvError, match="Source cache path must not contain symlinks"):
        ensure_source_repos(repos, paths, runner=runner)

    assert runner.calls == []


def test_ensure_source_repos_clones_each_configured_repo(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repos = [
        RepoConfig(id="Org/A", url="https://github.com/Org/A.git"),
        RepoConfig(id="Org/B", url="https://github.com/Org/B.git"),
    ]
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(["git", "clone"]),
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(["git", "clone"]),
        ]
    )

    ensured = ensure_source_repos(repos, paths, runner=runner)

    assert ensured == [
        paths.source_repo_for("Org/A"),
        paths.source_repo_for("Org/B"),
    ]
    assert runner.calls == [
        (["git", "--version"], None),
        (
            [
                "git",
                "clone",
                "--",
                "https://github.com/Org/A.git",
                str(paths.source_repo_for("Org/A")),
            ],
            None,
        ),
        (["git", "--version"], None),
        (
            [
                "git",
                "clone",
                "--",
                "https://github.com/Org/B.git",
                str(paths.source_repo_for("Org/B")),
            ],
            None,
        ),
    ]

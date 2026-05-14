from pathlib import Path
import subprocess

import pytest

from sv.config import RepoConfig, SvPaths
from sv.errors import SvError
import sv.source as source_module
from sv.source import (
    default_runner,
    ensure_source_repo,
    ensure_source_repos,
    list_source_skills,
    reject_symlinked_source_cache_path,
)


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


def test_default_runner_reports_missing_git_as_sv_error(monkeypatch):
    def missing_binary(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(source_module.subprocess, "run", missing_binary)

    with pytest.raises(SvError, match="Git is required"):
        default_runner(["git", "status"])


def test_default_runner_reraises_missing_non_git_binary(monkeypatch):
    def missing_binary(*args, **kwargs):
        raise FileNotFoundError("custom")

    monkeypatch.setattr(source_module.subprocess, "run", missing_binary)

    with pytest.raises(FileNotFoundError):
        default_runner(["custom-tool"])


def test_ensure_source_repo_rejects_control_characters_in_repo_url(tmp_path: Path):
    repo_path = tmp_path / ".sv" / "sources" / "default" / "repo"
    runner = FakeRunner([])

    with pytest.raises(SvError, match="repo cannot contain control characters"):
        ensure_source_repo("https://example.com/skills.git\x1b[2J", repo_path, runner=runner)

    assert runner.calls == []


def test_ensure_source_repo_reports_missing_git_from_runner(tmp_path: Path):
    repo_path = tmp_path / "repo"

    def runner(args, cwd=None):
        raise FileNotFoundError("git")

    with pytest.raises(SvError, match="Git is required"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)


def test_ensure_source_repo_reports_exit_code_without_git_output(tmp_path: Path):
    repo_path = tmp_path / "repo"
    runner = FakeRunner(
        [
            completed(["git", "--version"], returncode=2),
        ]
    )

    with pytest.raises(SvError, match="exit code 2"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)


def test_reject_symlinked_source_cache_path_handles_paths_outside_cache(tmp_path: Path):
    sources_dir = tmp_path / "sources"
    repo_path = tmp_path / "elsewhere" / "repo"

    reject_symlinked_source_cache_path(repo_path, sources_dir)


def test_reject_symlinked_source_cache_path_reports_inspection_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    sources_dir = tmp_path / "sources"
    repo_path = sources_dir / "Org" / "Skills"
    blocked_path = sources_dir / "Org"
    original_is_symlink = Path.is_symlink

    def fail_for_blocked_path(path: Path) -> bool:
        if path == blocked_path:
            raise OSError("permission denied")
        return original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", fail_for_blocked_path)

    with pytest.raises(SvError) as exc_info:
        reject_symlinked_source_cache_path(repo_path, sources_dir)

    assert str(exc_info.value) == (
        f"Failed to inspect source path {blocked_path}: permission denied"
    )


def test_ensure_source_repo_reports_cache_path_inspection_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_path = tmp_path / "repo"
    runner = FakeRunner([])
    original_is_symlink = Path.is_symlink

    def fail_for_repo_path(path: Path) -> bool:
        if path == repo_path:
            raise OSError("permission denied")
        return original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", fail_for_repo_path)

    with pytest.raises(SvError) as exc_info:
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    assert str(exc_info.value) == (
        f"Failed to inspect source path {repo_path}: permission denied"
    )
    assert runner.calls == []


def test_ensure_source_repo_reports_cache_ancestor_inspection_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    blocked_path = tmp_path / "blocked"
    repo_path = blocked_path / "repo"
    runner = FakeRunner([])
    original_is_symlink = Path.is_symlink

    def fail_for_blocked_path(path: Path) -> bool:
        if path == blocked_path:
            raise OSError("permission denied")
        return original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", fail_for_blocked_path)

    with pytest.raises(SvError) as exc_info:
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    assert str(exc_info.value) == (
        f"Failed to inspect source path {blocked_path}: permission denied"
    )
    assert runner.calls == []


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


def test_ensure_source_repo_rejects_symlinked_git_dir(tmp_path: Path):
    if not hasattr(Path, "symlink_to"):
        pytest.skip("symlink support is required")
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    outside_git = tmp_path / "outside-git"
    outside_git.mkdir()
    (repo_path / ".git").symlink_to(outside_git, target_is_directory=True)
    runner = FakeRunner([])

    with pytest.raises(SvError, match="Source Git metadata path must not be a symlink"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    assert runner.calls == []


def test_ensure_source_repo_rejects_symlinked_cache_ancestor(tmp_path: Path):
    if not hasattr(Path, "symlink_to"):
        pytest.skip("symlink support is required")
    outside_sources = tmp_path / "outside-sources"
    outside_sources.mkdir()
    sources_link = tmp_path / "sources-link"
    sources_link.symlink_to(outside_sources, target_is_directory=True)
    repo_path = sources_link / "Org" / "Skills" / "repo"
    runner = FakeRunner([])

    with pytest.raises(SvError, match="Source cache path must not contain symlinks"):
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


def test_ensure_source_repo_accepts_equivalent_github_remote_url(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(
                ["git", "remote", "get-url", "origin"],
                stdout="https://github.com/Org/Skills.git\n",
            ),
            completed(["git", "pull", "--ff-only"]),
        ]
    )

    ensure_source_repo("https://github.com/Org/Skills", repo_path, runner=runner)

    assert runner.calls == [
        (["git", "--version"], None),
        (["git", "remote", "get-url", "origin"], repo_path),
        (["git", "pull", "--ff-only"], repo_path),
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


def test_ensure_source_repo_escapes_control_characters_in_remote_mismatch(
    tmp_path: Path,
):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(
                ["git", "remote", "get-url", "origin"],
                stdout="https://example.com/other.git\x1b[2J\n",
            ),
        ]
    )

    with pytest.raises(SvError) as exc_info:
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    message = str(exc_info.value)
    assert "https://example.com/other.git\\x1b[2J" in message
    assert "\x1b" not in message


def test_ensure_source_repo_reports_git_failure(tmp_path: Path):
    repo_path = tmp_path / "repo"
    runner = FakeRunner(
        [
            completed(["git", "--version"], returncode=1, stderr="git missing\n"),
        ]
    )

    with pytest.raises(SvError, match="Checking Git availability failed"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)


def test_ensure_source_repo_escapes_control_characters_in_git_failure(
    tmp_path: Path,
):
    repo_path = tmp_path / "repo"
    runner = FakeRunner(
        [
            completed(
                ["git", "--version"],
                returncode=1,
                stderr="bad\x1b[2J output\n",
            ),
        ]
    )

    with pytest.raises(SvError) as exc_info:
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    message = str(exc_info.value)
    assert "bad\\x1b[2J output" in message
    assert "\x1b" not in message


def test_ensure_source_repo_reports_runner_os_errors(tmp_path: Path):
    repo_path = tmp_path / "repo"

    def runner(args, cwd=None):
        raise PermissionError("denied")

    with pytest.raises(SvError, match="Checking Git availability failed: denied"):
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

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
import subprocess

from sv.config import RepoConfig, SvPaths, repo_source_key
from sv.errors import SvError

Runner = Callable[[Sequence[str], Path | None], subprocess.CompletedProcess[str]]


def default_runner(
    args: Sequence[str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(list(args), cwd=cwd, text=True, capture_output=True)
    except FileNotFoundError as exc:
        if args and args[0] == "git":
            raise SvError("Git is required but was not found on PATH.") from exc
        raise


def ensure_source_repo(
    repo_url: str,
    repo_path: Path,
    runner: Runner = default_runner,
    *,
    update: bool = True,
) -> None:
    _validate_repo_url(repo_url)
    _reject_symlinked_source_path(repo_path, "Source repo cache path")
    _reject_symlinked_source_cache_ancestors(repo_path)
    _reject_symlinked_source_path(repo_path / ".git", "Source Git metadata path")
    _run_git(["--version"], cwd=None, runner=runner, action="Checking Git availability")

    if repo_path.exists():
        if not (repo_path / ".git").exists():
            raise SvError(
                f"Source path {repo_path} exists but is not a Git clone. Remove it and rerun sv."
            )

        current_remote = _run_git(
            ["remote", "get-url", "origin"],
            cwd=repo_path,
            runner=runner,
            action="Reading source repo remote",
        )
        if current_remote != repo_url and not _same_source_repo(current_remote, repo_url):
            safe_current_remote = _escape_control_characters(current_remote)
            raise SvError(
                f"Configured source repo is {repo_url}, but existing source clone uses {safe_current_remote}. "
                f"Remove {repo_path} and rerun sv."
            )

        if update:
            _run_git(
                ["pull", "--ff-only"],
                cwd=repo_path,
                runner=runner,
                action="Updating source repo",
            )
        return

    try:
        repo_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SvError(
            f"Failed to prepare source cache directory {repo_path.parent}: {exc}"
        ) from exc

    _run_git(
        ["clone", "--", repo_url, str(repo_path)],
        cwd=None,
        runner=runner,
        action="Cloning source repo",
    )


def ensure_source_repos(
    repos: Sequence[RepoConfig],
    paths: SvPaths,
    runner: Runner = default_runner,
    *,
    update: bool = True,
) -> list[Path]:
    repo_paths: list[Path] = []
    for repo in repos:
        repo_path = paths.source_repo_for(repo.id)
        reject_symlinked_source_cache_path(repo_path, paths.sources_dir)
        ensure_source_repo(repo.url, repo_path, runner=runner, update=update)
        repo_paths.append(repo_path)
    return repo_paths


def _same_source_repo(left: str, right: str) -> bool:
    try:
        return repo_source_key(left) == repo_source_key(right)
    except ValueError:
        return False


def list_source_skills(repo_path: Path) -> list[str]:
    _reject_symlinked_source_path(repo_path, "Source repo cache path")
    skills_root = repo_path / "skills"
    _reject_symlinked_source_path(skills_root, "Source skills path")
    if not skills_root.is_dir():
        return []

    return sorted(
        path.name
        for path in skills_root.iterdir()
        if not path.is_symlink() and path.is_dir()
    )


def _validate_repo_url(repo_url: str) -> None:
    if repo_url.startswith("-"):
        raise SvError("Invalid source repo: repo cannot start with '-'.")
    if any(ord(char) < 0x20 or 0x7F <= ord(char) < 0xA0 for char in repo_url):
        raise SvError("Invalid source repo: repo cannot contain control characters.")


def _reject_symlinked_source_cache_ancestors(repo_path: Path) -> None:
    for ancestor in reversed(repo_path.parents):
        try:
            if ancestor.is_symlink():
                raise SvError(
                    f"Source cache path must not contain symlinks: {ancestor}."
                )
        except OSError as exc:
            raise SvError(f"Failed to inspect source path {ancestor}: {exc}") from exc


def reject_symlinked_source_cache_path(repo_path: Path, sources_dir: Path) -> None:
    try:
        relative_parts = repo_path.relative_to(sources_dir).parts
    except ValueError:
        relative_parts = repo_path.parts

    _reject_symlinked_source_path(sources_dir, "Source cache path")
    current = sources_dir
    for part in relative_parts:
        current = current / part
        try:
            if current.is_symlink():
                raise SvError(
                    f"Source cache path must not contain symlinks: {current}."
                )
        except OSError as exc:
            raise SvError(f"Failed to inspect source path {current}: {exc}") from exc


def _reject_symlinked_source_path(path: Path, label: str) -> None:
    try:
        if path.is_symlink():
            raise SvError(f"{label} must not be a symlink: {path}.")
    except OSError as exc:
        raise SvError(f"Failed to inspect source path {path}: {exc}") from exc


def _run_git(args: Sequence[str], cwd: Path | None, runner: Runner, action: str) -> str:
    command = ["git", *args]
    try:
        result = runner(command, cwd)
    except FileNotFoundError as exc:
        raise SvError("Git is required but was not found on PATH.") from exc
    except OSError as exc:
        raise SvError(f"{action} failed: {_escape_control_characters(str(exc))}") from exc

    if result.returncode != 0:
        details = _escape_control_characters(
            (result.stderr or result.stdout or "").strip()
        )
        if details:
            raise SvError(f"{action} failed: {details}")
        raise SvError(f"{action} failed with exit code {result.returncode}.")

    return (result.stdout or "").strip()


def _escape_control_characters(value: str) -> str:
    escaped: list[str] = []
    for char in value:
        codepoint = ord(char)
        if codepoint < 0x20 or 0x7F <= codepoint < 0xA0:
            escaped.append(f"\\x{codepoint:02x}")
        else:
            escaped.append(char)
    return "".join(escaped)

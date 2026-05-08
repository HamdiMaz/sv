from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
import subprocess

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
    repo_url: str, repo_path: Path, runner: Runner = default_runner
) -> None:
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
        if current_remote != repo_url:
            raise SvError(
                f"Configured source repo is {repo_url}, but existing source clone uses {current_remote}. "
                f"Remove {repo_path} and rerun sv."
            )

        _run_git(
            ["pull", "--ff-only"],
            cwd=repo_path,
            runner=runner,
            action="Updating source repo",
        )
        return

    repo_path.parent.mkdir(parents=True, exist_ok=True)
    _run_git(
        ["clone", repo_url, str(repo_path)],
        cwd=None,
        runner=runner,
        action="Cloning source repo",
    )


def list_source_skills(repo_path: Path) -> list[str]:
    skills_root = repo_path / "skills"
    if not skills_root.is_dir():
        return []

    return sorted(path.name for path in skills_root.iterdir() if path.is_dir())


def _run_git(args: Sequence[str], cwd: Path | None, runner: Runner, action: str) -> str:
    command = ["git", *args]
    try:
        result = runner(command, cwd)
    except FileNotFoundError as exc:
        raise SvError("Git is required but was not found on PATH.") from exc

    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        if details:
            raise SvError(f"{action} failed: {details}")
        raise SvError(f"{action} failed with exit code {result.returncode}.")

    return (result.stdout or "").strip()

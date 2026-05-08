# sv MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Pi-only `sv` CLI that manages project-local skills from a Git-backed source repository.

**Architecture:** The CLI is a thin `argparse` layer over focused modules for configuration, Git source management, Pi adapter behavior, and project skill copying. The source repo is cloned under `~/.sv/sources/default/repo`, while project skills are written directly into `.pi/skills` in the current working directory.

**Tech Stack:** Python 3.14, stdlib `argparse`, `pathlib`, `subprocess`, `shutil`, `tomllib`, `dataclasses`, `pytest`, `hatchling`, `uv`.

---

## Scope Check

The approved spec describes one coherent MVP: Pi-only skill management from a single Git source. It does not need to be split into separate subsystem plans.

## File Structure

- Modify `pyproject.toml` — package metadata, console script, pytest configuration, build backend, dev dependency group.
- Modify `main.py` — compatibility entry point that delegates to `sv.cli.main`.
- Create `src/sv/__init__.py` — package marker and version string.
- Create `src/sv/errors.py` — shared `SvError` exception for user-facing failures.
- Create `src/sv/config.py` — default repo, repo normalization, `~/.sv` paths, TOML config read/write.
- Create `src/sv/source.py` — Git availability checks, source clone/update, source skill listing.
- Create `src/sv/agents.py` — Pi adapter for `.pi/skills` and isolated `pi` command construction.
- Create `src/sv/project.py` — copy/add/sync behavior for project-local Pi skill folders.
- Create `src/sv/cli.py` — argument parser and command handlers.
- Create `tests/test_config.py` — config and repo normalization tests.
- Create `tests/test_source.py` — Git source helper tests using fake Git runners and local directories.
- Create `tests/test_project.py` — add/sync copy behavior tests.
- Create `tests/test_cli.py` — CLI config and `sv run` tests.
- Create `tests/test_cli_source_commands.py` — end-to-end CLI tests using a temporary local Git source repo.
- Modify `README.md` — MVP usage documentation.

---

### Task 1: Package Metadata and Config Module

**Files:**
- Modify: `pyproject.toml`
- Modify: `main.py`
- Create: `src/sv/__init__.py`
- Create: `src/sv/errors.py`
- Create: `src/sv/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Configure package metadata and pytest path**

Replace `pyproject.toml` with:

```toml
[project]
name = "sv"
version = "0.1.0"
description = "Project-local AI agent skill manager"
readme = "README.md"
requires-python = ">=3.14"
dependencies = []

[project.scripts]
sv = "sv.cli:main"

[dependency-groups]
dev = [
    "pytest>=8.0.0",
]

[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
pythonpath = ["src"]
```

Create the package directory and minimal package marker:

```bash
mkdir -p src/sv tests
cat > src/sv/__init__.py <<'PY'
"""sv: project-local AI agent skill manager."""

__version__ = "0.1.0"
PY
```

- [ ] **Step 2: Write failing config tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

import pytest

from sv.config import DEFAULT_REPO, SvConfig, SvPaths, load_config, normalize_repo, save_repo


def test_normalize_repo_accepts_github_shorthand():
    assert normalize_repo("HamdiMaz/Skills") == "https://github.com/HamdiMaz/Skills.git"


def test_normalize_repo_keeps_https_url():
    url = "https://github.com/HamdiMaz/Skills.git"
    assert normalize_repo(url) == url


def test_normalize_repo_keeps_ssh_url():
    url = "git@github.com:HamdiMaz/Skills.git"
    assert normalize_repo(url) == url


def test_normalize_repo_rejects_empty_value():
    with pytest.raises(ValueError, match="repo cannot be empty"):
        normalize_repo("   ")


def test_paths_use_single_sv_home_under_user_home(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)

    assert paths.sv_home == tmp_path / ".sv"
    assert paths.config_file == tmp_path / ".sv" / "config.toml"
    assert paths.source_repo == tmp_path / ".sv" / "sources" / "default" / "repo"


def test_load_config_uses_default_repo_when_config_missing(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)

    config = load_config(paths)

    assert config == SvConfig(repo=DEFAULT_REPO)


def test_save_repo_writes_normalized_repo(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)

    saved = save_repo(paths, "HamdiMaz/Skills")

    assert saved == "https://github.com/HamdiMaz/Skills.git"
    assert paths.config_file.read_text() == 'repo = "https://github.com/HamdiMaz/Skills.git"\n'
    assert load_config(paths) == SvConfig(repo="https://github.com/HamdiMaz/Skills.git")
```

- [ ] **Step 3: Run config tests and verify failure**

Run:

```bash
uv run pytest tests/test_config.py -v
```

Expected: FAIL because `sv.config` does not exist yet.

- [ ] **Step 4: Implement config module**

Create `src/sv/errors.py`:

```python
class SvError(RuntimeError):
    """User-facing sv error."""
```

Create `src/sv/config.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib

DEFAULT_REPO = "https://github.com/HamdiMaz/Skills.git"

_GITHUB_SHORTHAND = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class SvPaths:
    sv_home: Path
    config_file: Path
    source_repo: Path

    @classmethod
    def from_home(cls, home: Path | None = None) -> "SvPaths":
        user_home = Path.home() if home is None else home
        sv_home = user_home / ".sv"
        return cls(
            sv_home=sv_home,
            config_file=sv_home / "config.toml",
            source_repo=sv_home / "sources" / "default" / "repo",
        )


@dataclass(frozen=True)
class SvConfig:
    repo: str = DEFAULT_REPO


def normalize_repo(repo: str) -> str:
    value = repo.strip()
    if not value:
        raise ValueError("repo cannot be empty")
    if value.startswith(("https://", "http://", "git@", "ssh://", "file://")):
        return value
    if _GITHUB_SHORTHAND.fullmatch(value):
        return f"https://github.com/{value}.git"
    return value


def load_config(paths: SvPaths) -> SvConfig:
    if not paths.config_file.exists():
        return SvConfig()

    with paths.config_file.open("rb") as file:
        data = tomllib.load(file)

    repo = data.get("repo", DEFAULT_REPO)
    return SvConfig(repo=str(repo))


def save_repo(paths: SvPaths, repo: str) -> str:
    normalized = normalize_repo(repo)
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text(f'repo = "{_toml_escape(normalized)}"\n')
    return normalized


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
```

Modify `main.py`:

```python
from sv.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
```

`main.py` imports `sv.cli`, which is created in Task 4. It is not exercised by the config tests.

- [ ] **Step 5: Run config tests and verify pass**

Run:

```bash
uv run pytest tests/test_config.py -v
```

Expected: PASS for all tests in `tests/test_config.py`.

- [ ] **Step 6: Commit package/config work**

Run:

```bash
git add pyproject.toml main.py src/sv/__init__.py src/sv/errors.py src/sv/config.py tests/test_config.py
git commit -m "feat: add sv config model"
```

---

### Task 2: Git Source Management

**Files:**
- Create: `src/sv/source.py`
- Test: `tests/test_source.py`

- [ ] **Step 1: Write failing Git source tests**

Create `tests/test_source.py`:

```python
from pathlib import Path
import subprocess

import pytest

from sv.errors import SvError
from sv.source import ensure_source_repo, list_source_skills


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
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


def test_ensure_source_repo_clones_when_missing(tmp_path: Path):
    repo_path = tmp_path / ".sv" / "sources" / "default" / "repo"
    runner = FakeRunner([
        completed(["git", "--version"], stdout="git version 2.0\n"),
        completed(["git", "clone"]),
    ])

    ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    assert runner.calls == [
        (["git", "--version"], None),
        (["git", "clone", "https://example.com/skills.git", str(repo_path)], None),
    ]
    assert repo_path.parent.exists()


def test_ensure_source_repo_pulls_existing_clone(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner([
        completed(["git", "--version"], stdout="git version 2.0\n"),
        completed(["git", "remote", "get-url", "origin"], stdout="https://example.com/skills.git\n"),
        completed(["git", "pull", "--ff-only"]),
    ])

    ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    assert runner.calls == [
        (["git", "--version"], None),
        (["git", "remote", "get-url", "origin"], repo_path),
        (["git", "pull", "--ff-only"], repo_path),
    ]


def test_ensure_source_repo_reports_remote_mismatch(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner([
        completed(["git", "--version"], stdout="git version 2.0\n"),
        completed(["git", "remote", "get-url", "origin"], stdout="https://example.com/other.git\n"),
    ])

    with pytest.raises(SvError, match="existing source clone uses"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)


def test_ensure_source_repo_reports_git_failure(tmp_path: Path):
    repo_path = tmp_path / "repo"
    runner = FakeRunner([
        completed(["git", "--version"], returncode=1, stderr="git missing\n"),
    ])

    with pytest.raises(SvError, match="Checking Git availability failed"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)


def test_ensure_source_repo_reports_non_git_existing_path(tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    runner = FakeRunner([
        completed(["git", "--version"], stdout="git version 2.0\n"),
    ])

    with pytest.raises(SvError, match="exists but is not a Git clone"):
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
```

- [ ] **Step 2: Run source tests and verify failure**

Run:

```bash
uv run pytest tests/test_source.py -v
```

Expected: FAIL because `sv.source` does not exist yet.

- [ ] **Step 3: Implement Git source module**

Create `src/sv/source.py`:

```python
from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
import subprocess

from sv.errors import SvError

Runner = Callable[[Sequence[str], Path | None], subprocess.CompletedProcess[str]]


def default_runner(args: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(list(args), cwd=cwd, text=True, capture_output=True)
    except FileNotFoundError as exc:
        if args and args[0] == "git":
            raise SvError("Git is required but was not found on PATH.") from exc
        raise


def ensure_source_repo(repo_url: str, repo_path: Path, runner: Runner = default_runner) -> None:
    _run_git(["--version"], cwd=None, runner=runner, action="Checking Git availability")

    if repo_path.exists():
        if not (repo_path / ".git").exists():
            raise SvError(f"Source path {repo_path} exists but is not a Git clone. Remove it and rerun sv.")

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

        _run_git(["pull", "--ff-only"], cwd=repo_path, runner=runner, action="Updating source repo")
        return

    repo_path.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["clone", repo_url, str(repo_path)], cwd=None, runner=runner, action="Cloning source repo")


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
```

- [ ] **Step 4: Run source tests and verify pass**

Run:

```bash
uv run pytest tests/test_source.py -v
```

Expected: PASS for all tests in `tests/test_source.py`.

- [ ] **Step 5: Run existing config tests**

Run:

```bash
uv run pytest tests/test_config.py tests/test_source.py -v
```

Expected: PASS for config and source tests.

- [ ] **Step 6: Commit Git source work**

Run:

```bash
git add src/sv/source.py tests/test_source.py
git commit -m "feat: manage git skill source"
```

---

### Task 3: Pi Adapter and Project Skill Operations

**Files:**
- Create: `src/sv/agents.py`
- Create: `src/sv/project.py`
- Test: `tests/test_project.py`

- [ ] **Step 1: Write failing project operation tests**

Create `tests/test_project.py`:

```python
from pathlib import Path

import pytest

from sv.agents import PiAdapter
from sv.errors import SvError
from sv.project import add_project_skill, sync_project_skills


def test_pi_adapter_uses_project_pi_skills_dir(tmp_path: Path):
    adapter = PiAdapter()

    assert adapter.name == "pi"
    assert adapter.project_skill_dir(tmp_path) == tmp_path / ".pi" / "skills"


def test_pi_adapter_builds_isolated_run_command():
    adapter = PiAdapter()

    assert adapter.run_command(["--model", "fast"]) == [
        "pi",
        "--no-skills",
        "--skill",
        ".pi/skills",
        "--model",
        "fast",
    ]


def test_add_project_skill_creates_pi_skills_and_copies_folder(tmp_path: Path):
    source_repo = tmp_path / "source"
    source_skill = source_repo / "skills" / "alpha"
    source_skill.mkdir(parents=True)
    (source_skill / "notes.md").write_text("alpha skill\n")
    project_skills = tmp_path / "project" / ".pi" / "skills"

    result = add_project_skill("alpha", source_repo, project_skills)

    assert result.status == "added"
    assert result.skill == "alpha"
    assert result.target == project_skills / "alpha"
    assert (project_skills / "alpha" / "notes.md").read_text() == "alpha skill\n"


def test_add_project_skill_existing_skill_is_success_without_overwrite(tmp_path: Path):
    source_repo = tmp_path / "source"
    source_skill = source_repo / "skills" / "alpha"
    source_skill.mkdir(parents=True)
    (source_skill / "notes.md").write_text("remote version\n")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    existing = project_skills / "alpha"
    existing.mkdir(parents=True)
    (existing / "notes.md").write_text("local version\n")

    result = add_project_skill("alpha", source_repo, project_skills)

    assert result.status == "exists"
    assert (existing / "notes.md").read_text() == "local version\n"


def test_add_project_skill_missing_source_skill_raises_error(tmp_path: Path):
    with pytest.raises(SvError, match="Skill 'missing' was not found"):
        add_project_skill("missing", tmp_path / "source", tmp_path / "project" / ".pi" / "skills")


def test_sync_project_skills_updates_matching_and_leaves_unknown(tmp_path: Path):
    source_repo = tmp_path / "source"
    managed_source = source_repo / "skills" / "managed"
    managed_source.mkdir(parents=True)
    (managed_source / "notes.md").write_text("remote v2\n")

    project_skills = tmp_path / "project" / ".pi" / "skills"
    managed_local = project_skills / "managed"
    managed_local.mkdir(parents=True)
    (managed_local / "notes.md").write_text("local v1\n")
    unknown_local = project_skills / "local-only"
    unknown_local.mkdir()
    (unknown_local / "notes.md").write_text("keep me\n")

    result = sync_project_skills(source_repo, project_skills)

    assert result.no_skills_dir is False
    assert result.updated == ["managed"]
    assert result.skipped == ["local-only"]
    assert (managed_local / "notes.md").read_text() == "remote v2\n"
    assert (unknown_local / "notes.md").read_text() == "keep me\n"


def test_sync_project_skills_reports_no_skills_dir(tmp_path: Path):
    result = sync_project_skills(tmp_path / "source", tmp_path / "project" / ".pi" / "skills")

    assert result.no_skills_dir is True
    assert result.updated == []
    assert result.skipped == []
```

- [ ] **Step 2: Run project tests and verify failure**

Run:

```bash
uv run pytest tests/test_project.py -v
```

Expected: FAIL because `sv.agents` and `sv.project` do not exist yet.

- [ ] **Step 3: Implement Pi adapter**

Create `src/sv/agents.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PiAdapter:
    name: str = "pi"

    def project_skill_dir(self, project_root: Path) -> Path:
        return project_root / ".pi" / "skills"

    def run_command(self, extra_args: Sequence[str] = ()) -> list[str]:
        return ["pi", "--no-skills", "--skill", ".pi/skills", *list(extra_args)]
```

- [ ] **Step 4: Implement project skill operations**

Create `src/sv/project.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from sv.errors import SvError


@dataclass(frozen=True)
class AddSkillResult:
    skill: str
    target: Path
    status: str


@dataclass(frozen=True)
class SyncResult:
    updated: list[str]
    skipped: list[str]
    no_skills_dir: bool = False


def add_project_skill(skill: str, source_repo: Path, project_skills_dir: Path) -> AddSkillResult:
    source_skill = source_repo / "skills" / skill
    if not source_skill.is_dir():
        raise SvError(f"Skill '{skill}' was not found in source skills directory.")

    project_skills_dir.mkdir(parents=True, exist_ok=True)
    target = project_skills_dir / skill
    if target.exists():
        return AddSkillResult(skill=skill, target=target, status="exists")

    shutil.copytree(source_skill, target)
    return AddSkillResult(skill=skill, target=target, status="added")


def sync_project_skills(source_repo: Path, project_skills_dir: Path) -> SyncResult:
    if not project_skills_dir.is_dir():
        return SyncResult(updated=[], skipped=[], no_skills_dir=True)

    source_root = source_repo / "skills"
    source_names = _source_skill_names(source_root)
    updated: list[str] = []
    skipped: list[str] = []

    for local_skill in sorted(project_skills_dir.iterdir(), key=lambda path: path.name):
        if not local_skill.is_dir():
            continue

        if local_skill.name not in source_names:
            skipped.append(local_skill.name)
            continue

        shutil.rmtree(local_skill)
        shutil.copytree(source_root / local_skill.name, local_skill)
        updated.append(local_skill.name)

    return SyncResult(updated=updated, skipped=skipped, no_skills_dir=False)


def _source_skill_names(source_root: Path) -> set[str]:
    if not source_root.is_dir():
        return set()
    return {path.name for path in source_root.iterdir() if path.is_dir()}
```

- [ ] **Step 5: Run project tests and verify pass**

Run:

```bash
uv run pytest tests/test_project.py -v
```

Expected: PASS for all tests in `tests/test_project.py`.

- [ ] **Step 6: Run accumulated unit tests**

Run:

```bash
uv run pytest tests/test_config.py tests/test_source.py tests/test_project.py -v
```

Expected: PASS for config, source, and project tests.

- [ ] **Step 7: Commit Pi adapter and project operations**

Run:

```bash
git add src/sv/agents.py src/sv/project.py tests/test_project.py
git commit -m "feat: add pi project skill operations"
```

---

### Task 4: CLI for Config and Isolated Pi Run

**Files:**
- Create: `src/sv/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI config/run tests**

Create `tests/test_cli.py`:

```python
from pathlib import Path

from sv.cli import build_parser, handle
from sv.config import SvPaths, load_config


def parse(argv):
    return build_parser().parse_args(argv)


def test_config_repo_writes_global_config(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(parse(["config", "repo", "HamdiMaz/Skills"]), cwd=project, home=home)

    assert exit_code == 0
    assert load_config(SvPaths.from_home(home)).repo == "https://github.com/HamdiMaz/Skills.git"
    assert "Set source repo to https://github.com/HamdiMaz/Skills.git" in capsys.readouterr().out


def test_config_show_prints_effective_config(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(parse(["config", "show"]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert f"repo = https://github.com/HamdiMaz/Skills.git" in output
    assert f"source = {home / '.sv' / 'sources' / 'default' / 'repo'}" in output
    assert f"pi_skills = {project / '.pi' / 'skills'}" in output


def test_run_builds_isolated_pi_command_and_forwards_args(tmp_path: Path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    calls = []

    def process_runner(command):
        calls.append(command)
        return 23

    exit_code = handle(
        parse(["run", "--", "--model", "fast"]),
        cwd=project,
        home=home,
        process_runner=process_runner,
    )

    assert exit_code == 23
    assert calls == [["pi", "--no-skills", "--skill", ".pi/skills", "--model", "fast"]]
```

- [ ] **Step 2: Run CLI tests and verify failure**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected: FAIL because `sv.cli` does not exist yet.

- [ ] **Step 3: Implement initial CLI config/run support**

Create `src/sv/cli.py`:

```python
from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import subprocess
import sys

from sv.agents import PiAdapter
from sv.config import SvPaths, load_config, save_repo
from sv.errors import SvError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sv", description="Manage project-local AI agent skills.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run Pi with only project skills enabled.")
    run_parser.add_argument("pi_args", nargs=argparse.REMAINDER)

    config_parser = subparsers.add_parser("config", help="Show or change sv configuration.")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    repo_parser = config_subparsers.add_parser("repo", help="Set the default skill source repo.")
    repo_parser.add_argument("repo")
    config_subparsers.add_parser("show", help="Show effective sv configuration.")

    return parser


def default_process_runner(command: Sequence[str]) -> int:
    return subprocess.call(list(command))


def handle(
    args: argparse.Namespace,
    cwd: Path,
    home: Path,
    process_runner=default_process_runner,
) -> int:
    paths = SvPaths.from_home(home)
    adapter = PiAdapter()

    try:
        if args.command == "config":
            return _handle_config(args, cwd=cwd, paths=paths, adapter=adapter)

        if args.command == "run":
            forwarded = _strip_arg_separator(args.pi_args)
            return process_runner(adapter.run_command(forwarded))

        raise SvError(f"Unknown command: {args.command}")
    except SvError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return handle(args, cwd=Path.cwd(), home=Path.home())


def _handle_config(args: argparse.Namespace, cwd: Path, paths: SvPaths, adapter: PiAdapter) -> int:
    if args.config_command == "repo":
        repo = save_repo(paths, args.repo)
        print(f"Set source repo to {repo}")
        return 0

    if args.config_command == "show":
        config = load_config(paths)
        print(f"repo = {config.repo}")
        print(f"source = {paths.source_repo}")
        print(f"pi_skills = {adapter.project_skill_dir(cwd)}")
        return 0

    raise SvError(f"Unknown config command: {args.config_command}")


def _strip_arg_separator(args: Sequence[str]) -> list[str]:
    forwarded = list(args)
    if forwarded and forwarded[0] == "--":
        return forwarded[1:]
    return forwarded
```

- [ ] **Step 4: Run CLI config/run tests and verify pass**

Run:

```bash
uv run pytest tests/test_cli.py -v
```

Expected: PASS for all tests in `tests/test_cli.py`.

- [ ] **Step 5: Run accumulated tests**

Run:

```bash
uv run pytest tests/test_config.py tests/test_source.py tests/test_project.py tests/test_cli.py -v
```

Expected: PASS for config, source, project, and CLI config/run tests.

- [ ] **Step 6: Commit initial CLI work**

Run:

```bash
git add src/sv/cli.py tests/test_cli.py
git commit -m "feat: add config and run cli commands"
```

---

### Task 5: CLI Source Commands and Local Git Integration

**Files:**
- Modify: `src/sv/cli.py`
- Create: `tests/test_cli_source_commands.py`

- [ ] **Step 1: Write failing source-command integration tests**

Create `tests/test_cli_source_commands.py`:

```python
from pathlib import Path
import shutil
import subprocess

import pytest

from sv.cli import build_parser, handle


def parse(argv):
    return build_parser().parse_args(argv)


def run_git(args, cwd: Path):
    subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True)


def make_source_repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is required for integration tests")

    source = tmp_path / "skill-source"
    (source / "skills" / "alpha").mkdir(parents=True)
    (source / "skills" / "alpha" / "notes.md").write_text("alpha v1\n")
    (source / "skills" / "beta").mkdir(parents=True)
    (source / "skills" / "beta" / "notes.md").write_text("beta v1\n")

    run_git(["init"], source)
    run_git(["config", "user.email", "tests@example.com"], source)
    run_git(["config", "user.name", "sv tests"], source)
    run_git(["add", "skills"], source)
    run_git(["commit", "-m", "initial skills"], source)
    return source


def configure_source(source: Path, project: Path, home: Path):
    exit_code = handle(parse(["config", "repo", str(source)]), cwd=project, home=home)
    assert exit_code == 0


def test_list_and_add_from_local_git_source(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["list"]), cwd=project, home=home)

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == ["alpha", "beta"]

    exit_code = handle(parse(["add", "alpha"]), cwd=project, home=home)

    assert exit_code == 0
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == "alpha v1\n"
    assert "Added Pi skill 'alpha'" in capsys.readouterr().out

    exit_code = handle(parse(["add", "alpha"]), cwd=project, home=home)

    assert exit_code == 0
    assert "already exists" in capsys.readouterr().out


def test_add_missing_skill_reports_error(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["add", "missing"]), cwd=project, home=home)

    assert exit_code == 1
    assert "Skill 'missing' was not found" in capsys.readouterr().err


def test_sync_updates_matching_skills_and_leaves_unknown(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert handle(parse(["add", "alpha"]), cwd=project, home=home) == 0
    capsys.readouterr()

    unknown = project / ".pi" / "skills" / "local-only"
    unknown.mkdir()
    (unknown / "notes.md").write_text("keep me\n")

    (source / "skills" / "alpha" / "notes.md").write_text("alpha v2\n")
    run_git(["add", "skills/alpha/notes.md"], source)
    run_git(["commit", "-m", "update alpha"], source)

    exit_code = handle(parse(["sync"]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Synced Pi skill 'alpha'." in output
    assert "Skipped local Pi skill 'local-only'" in output
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == "alpha v2\n"
    assert (unknown / "notes.md").read_text() == "keep me\n"


def test_sync_with_no_pi_skills_dir_exits_successfully(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["sync"]), cwd=project, home=home)

    assert exit_code == 0
    assert "No Pi skills found to sync." in capsys.readouterr().out
```

- [ ] **Step 2: Run source-command tests and verify failure**

Run:

```bash
uv run pytest tests/test_cli_source_commands.py -v
```

Expected: FAIL because the parser does not know `list`, `add`, or `sync` yet.

- [ ] **Step 3: Replace CLI with full command support**

Replace `src/sv/cli.py` with:

```python
from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import subprocess
import sys

from sv.agents import PiAdapter
from sv.config import SvPaths, load_config, save_repo
from sv.errors import SvError
from sv.project import add_project_skill, sync_project_skills
from sv.source import default_runner, ensure_source_repo, list_source_skills


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sv", description="Manage project-local AI agent skills.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List skills available in the configured source repo.")

    add_parser = subparsers.add_parser("add", help="Add a source skill to this Pi project.")
    add_parser.add_argument("skill")

    subparsers.add_parser("sync", help="Update local Pi skills that exist in the source repo.")

    run_parser = subparsers.add_parser("run", help="Run Pi with only project skills enabled.")
    run_parser.add_argument("pi_args", nargs=argparse.REMAINDER)

    config_parser = subparsers.add_parser("config", help="Show or change sv configuration.")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    repo_parser = config_subparsers.add_parser("repo", help="Set the default skill source repo.")
    repo_parser.add_argument("repo")
    config_subparsers.add_parser("show", help="Show effective sv configuration.")

    return parser


def default_process_runner(command: Sequence[str]) -> int:
    return subprocess.call(list(command))


def handle(
    args: argparse.Namespace,
    cwd: Path,
    home: Path,
    git_runner=default_runner,
    process_runner=default_process_runner,
) -> int:
    paths = SvPaths.from_home(home)
    adapter = PiAdapter()

    try:
        if args.command == "config":
            return _handle_config(args, cwd=cwd, paths=paths, adapter=adapter)

        if args.command == "run":
            forwarded = _strip_arg_separator(args.pi_args)
            return process_runner(adapter.run_command(forwarded))

        config = load_config(paths)
        ensure_source_repo(config.repo, paths.source_repo, runner=git_runner)

        if args.command == "list":
            return _handle_list(paths)

        if args.command == "add":
            return _handle_add(args.skill, cwd=cwd, paths=paths, adapter=adapter)

        if args.command == "sync":
            return _handle_sync(cwd=cwd, paths=paths, adapter=adapter)

        raise SvError(f"Unknown command: {args.command}")
    except SvError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return handle(args, cwd=Path.cwd(), home=Path.home())


def _handle_config(args: argparse.Namespace, cwd: Path, paths: SvPaths, adapter: PiAdapter) -> int:
    if args.config_command == "repo":
        repo = save_repo(paths, args.repo)
        print(f"Set source repo to {repo}")
        return 0

    if args.config_command == "show":
        config = load_config(paths)
        print(f"repo = {config.repo}")
        print(f"source = {paths.source_repo}")
        print(f"pi_skills = {adapter.project_skill_dir(cwd)}")
        return 0

    raise SvError(f"Unknown config command: {args.config_command}")


def _handle_list(paths: SvPaths) -> int:
    skills = list_source_skills(paths.source_repo)
    if not skills:
        print("No skills found in source repo.")
        return 0

    for skill in skills:
        print(skill)
    return 0


def _handle_add(skill: str, cwd: Path, paths: SvPaths, adapter: PiAdapter) -> int:
    result = add_project_skill(skill, paths.source_repo, adapter.project_skill_dir(cwd))
    if result.status == "exists":
        print(f"Pi skill '{skill}' already exists at {result.target}")
        return 0

    print(f"Added Pi skill '{skill}' to {result.target}")
    return 0


def _handle_sync(cwd: Path, paths: SvPaths, adapter: PiAdapter) -> int:
    result = sync_project_skills(paths.source_repo, adapter.project_skill_dir(cwd))
    if result.no_skills_dir:
        print("No Pi skills found to sync.")
        return 0

    if result.updated:
        for skill in result.updated:
            print(f"Synced Pi skill '{skill}'.")
    else:
        print("No matching Pi skills found to sync.")

    for skill in result.skipped:
        print(f"Skipped local Pi skill '{skill}' because it is not in the source repo.")

    return 0


def _strip_arg_separator(args: Sequence[str]) -> list[str]:
    forwarded = list(args)
    if forwarded and forwarded[0] == "--":
        return forwarded[1:]
    return forwarded
```

- [ ] **Step 4: Run source-command integration tests and verify pass**

Run:

```bash
uv run pytest tests/test_cli_source_commands.py -v
```

Expected: PASS for all source-command integration tests.

- [ ] **Step 5: Run the full test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS for all tests.

- [ ] **Step 6: Commit full CLI source commands**

Run:

```bash
git add src/sv/cli.py tests/test_cli_source_commands.py
git commit -m "feat: add skill source cli commands"
```

---

### Task 6: README and Final Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write user-facing README**

Replace `README.md` with:

```markdown
# sv

`sv` manages project-local AI agent skills. The MVP supports Pi and copies skills from a Git-backed skill source into the current project's `.pi/skills` directory.

Default skill source:

```text
https://github.com/HamdiMaz/Skills.git
```

Global sv files live under:

```text
~/.sv/
```

Project Pi skills live under:

```text
.pi/skills/
```

## Commands

List skills available in the configured source repo:

```bash
sv list
```

Add a skill to the current project:

```bash
sv add github-release
```

If the skill already exists in `.pi/skills`, sv prints a friendly message and leaves it unchanged.

Update project skills whose names exist in the source repo:

```bash
sv sync
```

Run Pi with global skill discovery disabled and only project skills enabled:

```bash
sv run -- <pi args>
```

Set a different global skill source:

```bash
sv config repo HamdiMaz/Skills
sv config repo https://github.com/HamdiMaz/Skills.git
sv config repo git@github.com:HamdiMaz/Skills.git
```

Show effective configuration:

```bash
sv config show
```

## Source repo layout

sv expects skills to be immediate folders under `skills/`:

```text
skills/
  github-release/
    ...
  find-docs/
    ...
```

sv does not validate skill contents in the MVP. It copies the folder as-is.

## Pi isolation

`sv run` launches Pi like this:

```bash
pi --no-skills --skill .pi/skills
```

This is the MVP mechanism for using only project skills. Start Pi through `sv run` when you want this isolation.
```

- [ ] **Step 2: Verify CLI help works**

Run:

```bash
uv run sv --help
```

Expected: output includes these subcommands:

```text
list
add
sync
run
config
```

- [ ] **Step 3: Run the full test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS for all tests.

- [ ] **Step 4: Check working tree**

Run:

```bash
git status --short
```

Expected: only intended implementation files are modified or untracked before the commit.

- [ ] **Step 5: Commit README and verification checkpoint**

Run:

```bash
git add README.md
git commit -m "docs: add sv usage guide"
```

---

## Self-Review Notes

- Spec coverage: Tasks cover Pi-only `.pi/skills`, Git-backed default source, `~/.sv`, `list`, `add`, `sync`, `run`, `config repo`, `config show`, no skill content validation, existing add behavior, sync update behavior, and local-skill preservation.
- Placeholder scan: The plan contains concrete file paths, commands, expected outputs, and code blocks for each code-bearing step.
- Type consistency: `SvPaths`, `SvConfig`, `PiAdapter`, `AddSkillResult`, `SyncResult`, `ensure_source_repo`, `list_source_skills`, `add_project_skill`, and `sync_project_skills` are named consistently across tests and implementation steps.

# Adapter Modularization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Implement one task, run its verification, commit it, then continue.

**Goal:** Modularize `sv` around eight adapter seams—UI/terminal, runtime/environment, concurrency, process execution, source backends, filesystem/materialization, persistence, and cache—without changing user-visible behavior or adding runtime dependencies.

**Architecture:** Use an adapter-first migration: introduce narrow classes/protocols and route existing call sites through them while preserving public function imports as compatibility wrappers. Keep pure domain logic as plain functions; put side effects and external boundaries behind adapters. Split large modules only after contract tests lock current behavior.

**Tech Stack:** Python 3.14 standard library, existing `pytest`, `ruff`, `ty`, current `sv` modules, no new runtime dependencies.


**Change Log:**
For reviewers, any changes to the plan should be logged here with a brief description and rationale.
- 2026-05-18: Aligned the handoff header with the agentic plan workflow requirement.
- 2026-05-18: Fixed runtime and concurrency implementation guidance that would have caused lint failures or broken existing monkeypatch-based worker-count tests.
- 2026-05-18: Clarified process and source-backend split order, compatibility exports, and private-test retargeting to avoid import cycles and façade monkeypatch regressions.
- 2026-05-18: Corrected materialization adapter signatures and refactor approach so required `error_message` behavior and existing public-function monkeypatch tests remain intact.
- 2026-05-18: Tightened persistence-store and CLI-boundary tasks with exact signatures, runtime stderr/env assertions, preserved first-run config loading semantics, and broader manifest-store routing notes.

---

## Scout Summary

**Architecture:** `sv` is a Python CLI under `src/sv`. Side-effect boundaries already exist in partial form: `src/sv/ui.py` wraps plain table formatting and selector calls; `src/sv/source.py` defines a `SourceBackend` protocol and subprocess runner alias; `src/sv/materialization.py` centralizes safe filesystem staging/replacement; `src/sv/parallel.py` centralizes ordered worker pools; `handle()` in `src/sv/cli.py` already accepts `cwd`, `home`, `git_runner`, `process_runner`, `skill_selector`, and `skill_chooser`.

**Likely files:**
- UI/terminal: `src/sv/ui.py`, `src/sv/table.py`, `src/sv/selector.py`, `src/sv/cli.py`, `tests/test_ui.py`, `tests/test_table.py`, `tests/test_selector.py`, `tests/test_cli_tty_browsing_contracts.py`.
- Runtime/environment: create `src/sv/runtime.py`, update `src/sv/cli.py`, add `tests/test_runtime.py`.
- Concurrency: `src/sv/parallel.py`, `src/sv/cli.py`, `tests/test_parallel.py`.
- Process execution: create `src/sv/process.py`, update `src/sv/source.py`, `src/sv/cli.py`, add `tests/test_process.py`.
- Source backends: create `src/sv/source_backends/`, keep `src/sv/source.py` compatibility exports, update `src/sv/catalog.py`, `src/sv/source_cache.py`, tests in `tests/test_source.py` plus new `tests/test_source_backends.py`.
- Filesystem/materialization: `src/sv/materialization.py`, `src/sv/project.py`, `tests/test_materialization.py`, `tests/test_project.py`.
- Persistence: create `src/sv/stores.py`, update `src/sv/project.py`, `src/sv/cli.py`, add `tests/test_stores.py`, keep `src/sv/config.py` and `src/sv/manifest.py` public functions.
- Cache: `src/sv/source_cache.py`, `tests/test_source_cache.py`.

**Existing patterns:** Preserve compatibility wrappers, lazy TTY imports, exact plain-output formatting, deterministic parallel output, symlink/path safety, TOML atomic writes, and no runtime dependency policy.

**Risks:** Broad output snapshot failures, import-cycle regressions when splitting `source.py`, weakening symlink/path safety, changing sparse Git locking semantics, changing `SV_JOBS` validation, or changing cache-only/stale-cache behavior.

**Tests:** Add adapter contract tests first. Keep focused tests running after each slice with `--no-cov`; run the full release gate at the end.

**Unknowns:** None blocking. The selected direction is adapter-first; this plan intentionally does not migrate to Rich, prompt-toolkit, or Textual.

---

## Recommended Design

**Recommendation:** Introduce adapters as stable internal seams before swapping libraries or moving large behavior. Each adapter should wrap an existing implementation first, with exact behavior preserved. After all seams exist and tests pass, future work can replace individual implementations—Rich for rendering, prompt-toolkit/Textual for cross-platform input, alternate source providers, or alternate storage—without rewriting command logic.

**Components:**
1. `sv.ui`: UI/terminal adapter for plain tables, TTY table browsing, and multi-select.
2. `sv.runtime`: process runtime/environment adapter for cwd, home, env, streams, prompts, terminal width, and time.
3. `sv.parallel`: concurrency adapter for job configuration and ordered worker execution.
4. `sv.process`: command runner adapter for `git`, `gh`, and `pi` execution.
5. `sv.source_backends`: source-provider adapter package for GitHub API, HTTPS API, local Git, and sparse Git.
6. `sv.materialization`: filesystem/materialization adapter for safe copy/install/replace/remove.
7. `sv.stores`: persistence adapters for config, project manifest, and global manifest.
8. `sv.source_cache`: cache adapters for catalog metadata and skill body cache.

**Flow:** CLI handlers use `Runtime` and adapter functions rather than raw process globals. Source commands create backend adapters through a factory. Catalog entries materialize through cache adapters, which delegate to source backends on misses. Project operations use materialization and manifest store adapters for final filesystem and state mutations. Concurrency remains deterministic through `OrderedExecutor`/`map_ordered`.

**Trade-offs:** This optimizes for low-risk modularity and future replaceability. It does not immediately deliver cross-platform interactive input; it creates the seam required to choose and test that backend later.

**Risks & mitigations:**
- Output changes: keep current renderers and add exact contract tests before wiring.
- Import churn: keep `sv.source`, `sv.materialization`, `sv.config`, and `sv.manifest` public functions as compatibility wrappers.
- Security regressions: keep symlink/path checks in existing implementation functions; adapters delegate to those functions first.
- Parallel nondeterminism: keep final writes and visible output serialized.

**Testing strategy:** Each task starts with failing adapter tests, then minimal behavior-preserving implementation. Focused tests run after each task. Final task runs `ruff`, `ty`, full coverage, build, and wheel smoke test.

**Rollout/migration:** Internal-only refactor. No CLI behavior, docs output, command names, or dependency changes. Public compatibility imports remain valid.

---

## Defaults and behavior locked by this plan

- No new runtime dependencies.
- `CHOSEN_TTY_UI_APPROACH.name` remains `"stdlib-inline"`.
- Rich, prompt-toolkit, and Textual remain alternatives, not dependencies.
- Plain output remains readable in terminals, logs, and CI.
- Existing table wrapping, truncation, CJK width handling, duplicate-name sections, and control-character escaping remain unchanged.
- TTY selectors and browsers remain lazy imports for plain output paths.
- `sv list`, `sv search`, and `sv repo list` keep TTY browser fallback to plain output when browser setup is unavailable.
- `sv add -l`, `sv remove -l`, and duplicate-source choice keep current TTY behavior and error messages.
- `SV_JOBS` accepts integers from `1` through `64`; invalid values raise `SvError("SV_JOBS must be an integer between 1 and 64.")`.
- `map_ordered` returns results in input order and raises the first worker exception by input index.
- Source backend order remains: `github-gh-api`, `github-https-api`, `git-treeless-partial`, `git-blobless-sparse` for GitHub sources; local paths use local source behavior only.
- Sparse Git source cache mutation remains locked per repo path.
- Manifest writes, final skill target renames, cache metadata writes, and visible output remain serialized.
- Symlink rejection, path validation, content hash verification, TOML size limits, and atomic write behavior remain fail-closed.
- Canonical project manifest remains `.sv/manifest.toml`; legacy `.pi/skills/.sv-manifest.toml` remains readable.
- Cache policy remains unchanged: normal 24-hour metadata TTL, `--refresh`, `--cached`, stale fallback only where currently allowed, body cache hash verification, and lazy pruning.
- `sv.source` public imports remain compatible during and after the source backend package split.

---

## File structure after the plan

Create:
- `src/sv/runtime.py` — runtime/environment adapter.
- `src/sv/process.py` — subprocess/process execution adapter.
- `src/sv/source_backends/__init__.py` — source backend package exports.
- `src/sv/source_backends/base.py` — source backend protocols/errors/shared dataclasses.
- `src/sv/source_backends/factory.py` — backend factory.
- `src/sv/source_backends/github.py` — GitHub API/HTTPS backend implementations.
- `src/sv/source_backends/git.py` — local Git and sparse Git backend implementations plus shared Git/path/cache-lock helpers used by the factory.
- `src/sv/stores.py` — config and manifest store adapters.
- `tests/test_runtime.py` — runtime adapter tests.
- `tests/test_process.py` — process adapter tests.
- `tests/test_source_backends.py` — source backend package/compatibility tests.
- `tests/test_stores.py` — persistence adapter tests.

Modify:
- `src/sv/ui.py`
- `src/sv/cli.py`
- `src/sv/parallel.py`
- `src/sv/source.py`
- `src/sv/catalog.py`
- `src/sv/source_cache.py`
- `src/sv/materialization.py`
- `src/sv/project.py`
- `tests/test_ui.py`
- `tests/test_parallel.py`
- `tests/test_source.py`
- `tests/test_source_cache.py`
- `tests/test_materialization.py`
- `tests/test_project.py`
- `tests/test_cli.py`
- `tests/test_cli_tty_browsing_contracts.py`

---

### Task 1: Complete the UI/terminal adapter seam

**Files:**
- Modify: `src/sv/ui.py`
- Modify: `src/sv/cli.py`
- Test: `tests/test_ui.py`
- Test: `tests/test_cli_tty_browsing_contracts.py`

- [ ] **Step 1: Write failing UI adapter tests**

Append these tests to `tests/test_ui.py`:

```python

def test_tty_ui_delegates_table_browsing_to_configured_browser():
    calls = []

    def browser(headers, rows, **kwargs):
        calls.append((headers, rows, kwargs))
        return ["alpha"]

    selected = TtyUi(table_browser=browser).browse_table(
        ["Skill"],
        [["alpha"]],
        key_help="a action",
    )

    assert selected == ["alpha"]
    assert calls == [
        (["Skill"], [["alpha"]], {"key_help": "a action"})
    ]


def test_browse_tty_table_uses_the_chosen_browser(monkeypatch):
    from sv import ui as ui_module

    calls = []

    def browser(headers, rows, **kwargs):
        calls.append((headers, rows, kwargs))
        return None

    monkeypatch.setattr(ui_module, "TtyUi", lambda: TtyUi(table_browser=browser))

    assert ui_module.browse_tty_table(["Skill"], [["alpha"]], clear_on_exit=True) is None
    assert calls == [
        (["Skill"], [["alpha"]], {"clear_on_exit": True})
    ]
```

- [ ] **Step 2: Run tests and verify intended failure**

Run:

```bash
uv run pytest tests/test_ui.py::test_tty_ui_delegates_table_browsing_to_configured_browser tests/test_ui.py::test_browse_tty_table_uses_the_chosen_browser -q --no-cov
```

Expected: fails because `TtyUi` has no `table_browser` field and no `browse_table` method, or because `sv.ui.browse_tty_table` does not exist.

- [ ] **Step 3: Implement the adapter method**

In `src/sv/ui.py`:

- Add a `table_browser: Callable[..., Sequence[str] | None] | None = None` field to `TtyUi`.
- Add `browse_table(self, headers, rows, **kwargs)` that:
  - uses `self.table_browser` when provided;
  - otherwise lazily imports `browse_table` from `sv.table`;
  - returns the browser result.
- Add top-level function:

```python
def browse_tty_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    **kwargs: Any,
) -> Sequence[str] | None:
    """Run the chosen interactive table browser for TTY-only flows."""
    return TtyUi().browse_table(headers, rows, **kwargs)
```

- [ ] **Step 4: Route CLI through the UI adapter**

In `src/sv/cli.py`:

- Remove `from sv.table import browse_table`.
- Add `browse_tty_table` to the `from sv.ui import (...)` import block.
- Change `_browse_tty_table()` to:

```python
def _browse_tty_table(headers: Sequence[str], rows: Sequence[Sequence[str]], **kwargs):
    return browse_tty_table(headers, rows, **kwargs)
```

Keep `_browse_tty_table` in `cli.py` so existing tests can keep monkeypatching it.

- [ ] **Step 5: Run focused verification**

Run:

```bash
uv run pytest tests/test_ui.py tests/test_cli_tty_browsing_contracts.py -q --no-cov
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/sv/ui.py src/sv/cli.py tests/test_ui.py tests/test_cli_tty_browsing_contracts.py
git commit -m "refactor: route table browsing through UI adapter"
```

---

### Task 2: Add the runtime/environment adapter skeleton

**Files:**
- Create: `src/sv/runtime.py`
- Modify: `src/sv/cli.py`
- Test: `tests/test_runtime.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing runtime tests**

Create `tests/test_runtime.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from sv.runtime import Runtime, default_terminal_width


class TtyStringIO(StringIO):
    def isatty(self):
        return True


def test_runtime_captures_paths_env_streams_and_clock(tmp_path: Path):
    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    stdin = StringIO("answer\n")
    stdout = StringIO()
    stderr = StringIO()

    runtime = Runtime(
        cwd=tmp_path / "project",
        home=tmp_path / "home",
        env={"SV_JOBS": "1"},
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        now=lambda: now,
        terminal_width=lambda: 42,
    )

    assert runtime.cwd == tmp_path / "project"
    assert runtime.home == tmp_path / "home"
    assert runtime.env["SV_JOBS"] == "1"
    assert runtime.now() == now
    assert runtime.width() == 42
    assert runtime.can_prompt() is False


def test_runtime_prompt_reads_from_configured_streams():
    runtime = Runtime(
        cwd=Path("/work"),
        home=Path("/home/user"),
        env={},
        stdin=StringIO("repo/name\n"),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert runtime.prompt("Repo: ") == "repo/name"
    assert runtime.stdout.getvalue() == "Repo: "


def test_runtime_can_prompt_requires_tty_stdin_and_stdout():
    runtime = Runtime(
        cwd=Path("/work"),
        home=Path("/home/user"),
        env={},
        stdin=TtyStringIO(),
        stdout=TtyStringIO(),
        stderr=StringIO(),
    )

    assert runtime.can_prompt() is True


def test_default_terminal_width_clamps_to_at_least_one(monkeypatch):
    monkeypatch.setattr("sv.runtime.shutil.get_terminal_size", lambda fallback: type("Size", (), {"columns": 0})())

    assert default_terminal_width() == 1
```

- [ ] **Step 2: Run tests and verify intended failure**

Run:

```bash
uv run pytest tests/test_runtime.py -q --no-cov
```

Expected: fails with `ModuleNotFoundError: No module named 'sv.runtime'`.

- [ ] **Step 3: Implement `Runtime`**

Create `src/sv/runtime.py` with:

```python
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import shutil
import sys
from typing import TextIO


def utc_now() -> datetime:
    return datetime.now(UTC)


def default_terminal_width() -> int:
    return max(shutil.get_terminal_size(fallback=(120, 24)).columns, 1)


@dataclass(frozen=True)
class Runtime:
    cwd: Path
    home: Path
    env: Mapping[str, str]
    stdin: TextIO
    stdout: TextIO
    stderr: TextIO
    now: Callable[[], datetime] = utc_now
    terminal_width: Callable[[], int] = default_terminal_width

    @classmethod
    def from_process(cls) -> "Runtime":
        return cls(
            cwd=Path.cwd(),
            home=Path.home(),
            env=os.environ,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

    def width(self) -> int:
        return max(self.terminal_width(), 1)

    def can_prompt(self) -> bool:
        return self.stdin.isatty() and self.stdout.isatty()

    def prompt(self, prompt: str) -> str:
        self.stdout.write(prompt)
        self.stdout.flush()
        return self.stdin.readline().rstrip("\n")
```

- [ ] **Step 4: Wire CLI entrypoints without changing `handle()` call signatures**

In `src/sv/cli.py`:

- Import `Runtime` from `sv.runtime`.
- In `main()`, replace direct `Path.cwd()`/`Path.home()` reads with:

```python
runtime = Runtime.from_process()
return handle(args, cwd=runtime.cwd, home=runtime.home)
```

- In `svx_main()`, use the same pattern before calling `handle()`.

Keep the current `handle` positional arguments unchanged for compatibility in this task.

- [ ] **Step 5: Run focused verification**

Run:

```bash
uv run pytest tests/test_runtime.py tests/test_cli.py::test_main_parses_arguments_and_uses_current_project_paths tests/test_cli.py::test_svx_main_dispatches_to_repo_add_flow -q --no-cov
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/sv/runtime.py src/sv/cli.py tests/test_runtime.py tests/test_cli.py
git commit -m "refactor: add runtime environment adapter"
```

---

### Task 3: Strengthen the concurrency adapter

**Files:**
- Modify: `src/sv/parallel.py`
- Modify: `src/sv/cli.py`
- Test: `tests/test_parallel.py`
- Test: `tests/test_cli_error_contracts.py`

- [ ] **Step 1: Write failing concurrency adapter tests**

Append to `tests/test_parallel.py`:

```python
from sv.parallel import ConcurrencyConfig, OrderedExecutor


def test_concurrency_config_uses_injected_env_and_cpu_count():
    assert ConcurrencyConfig(env={}, cpu_count=lambda: 64).jobs() == 8
    assert ConcurrencyConfig(env={"SV_JOBS": "3"}, cpu_count=lambda: 64).jobs() == 3


def test_ordered_executor_uses_configured_jobs():
    executor = OrderedExecutor(ConcurrencyConfig(env={"SV_JOBS": "1"}))

    assert executor.map([1, 2, 3], lambda value: value * 10) == [10, 20, 30]
```

- [ ] **Step 2: Run tests and verify intended failure**

Run:

```bash
uv run pytest tests/test_parallel.py::test_concurrency_config_uses_injected_env_and_cpu_count tests/test_parallel.py::test_ordered_executor_uses_configured_jobs -q --no-cov
```

Expected: fails because `ConcurrencyConfig` and `OrderedExecutor` do not exist.

- [ ] **Step 3: Implement adapter classes while keeping existing functions**

In `src/sv/parallel.py`:

- Add `from dataclasses import dataclass, field`.
- Add:

```python
@dataclass(frozen=True)
class ConcurrencyConfig:
    env: Mapping[str, str] | None = None
    cpu_count: Callable[[], int | None] | None = None

    def jobs(self) -> int:
        values = os.environ if self.env is None else self.env
        raw_value = values.get("SV_JOBS")
        if raw_value is None:
            detected_cpu_count = (self.cpu_count or os.cpu_count)() or 1
            return min(_DEFAULT_MAX_JOBS, detected_cpu_count + 4)
        try:
            jobs = int(raw_value, 10)
        except ValueError as exc:
            raise SvError("SV_JOBS must be an integer between 1 and 64.") from exc
        if jobs < 1 or jobs > _MAX_JOBS:
            raise SvError("SV_JOBS must be an integer between 1 and 64.")
        return jobs


@dataclass(frozen=True)
class OrderedExecutor:
    config: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)

    def map(self, items: Iterable[T], worker: Callable[[T], R], *, jobs: int | None = None) -> list[R]:
        worker_count = self.config.jobs() if jobs is None else jobs
        return _map_ordered_with_worker_count(items, worker, worker_count)
```

- Move the body of `map_ordered()` into private `_map_ordered_with_worker_count(...)`.
- Preserve current validation order: compute and validate `worker_count` before returning for an empty input list, so invalid `SV_JOBS` still fails even for `map_ordered([], ...)`.
- Keep `configured_jobs(env=None)` as `return ConcurrencyConfig(env=env).jobs()`; with `cpu_count=None`, this must still read `os.cpu_count` at call time so existing monkeypatch tests keep working.
- Keep `map_ordered(...)` as `return OrderedExecutor().map(items, worker, jobs=jobs)`.

- [ ] **Step 4: Validate `SV_JOBS` with runtime env at CLI entry**

If `handle()` has no runtime parameter yet, keep the current `configured_jobs()` call. If Task 2 has introduced a runtime parameter in local implementation, call `configured_jobs(runtime.env)` there. Preserve current behavior: invalid `SV_JOBS` must fail before command work starts.

- [ ] **Step 5: Run focused verification**

Run:

```bash
uv run pytest tests/test_parallel.py tests/test_cli_error_contracts.py -q --no-cov
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/sv/parallel.py src/sv/cli.py tests/test_parallel.py tests/test_cli_error_contracts.py
git commit -m "refactor: formalize concurrency adapter"
```

---

### Task 4: Extract the process runner adapter

**Files:**
- Create: `src/sv/process.py`
- Modify: `src/sv/source.py`
- Modify: `src/sv/cli.py`
- Test: `tests/test_process.py`
- Test: `tests/test_source.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing process adapter tests**

Create `tests/test_process.py`:

```python
from __future__ import annotations

from pathlib import Path
import subprocess

from sv.process import default_process_runner, default_runner


def test_default_process_runner_delegates_to_subprocess_call(monkeypatch):
    calls = []

    def fake_call(command):
        calls.append(command)
        return 7

    monkeypatch.setattr("sv.process.subprocess.call", fake_call)

    assert default_process_runner(["pi", "--help"]) == 7
    assert calls == [["pi", "--help"]]


def test_default_runner_sets_noninteractive_environment(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("sv.process.subprocess.run", fake_run)

    result = default_runner(["git", "status"], tmp_path)

    assert result.stdout == "ok"
    assert captured["args"] == ["git", "status"]
    assert captured["cwd"] == tmp_path
    assert captured["text"] is True
    assert captured["capture_output"] is True
    assert captured["timeout"] == 60
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["env"]["GH_PROMPT_DISABLED"] == "1"
    assert captured["env"]["GIT_ALLOW_PROTOCOL"] == "file:https:ssh"
```

- [ ] **Step 2: Run tests and verify intended failure**

Run:

```bash
uv run pytest tests/test_process.py -q --no-cov
```

Expected: fails with `ModuleNotFoundError: No module named 'sv.process'`.

- [ ] **Step 3: Move runner code into `src/sv/process.py`**

Create `src/sv/process.py` containing only generic process-runner pieces:

- `Runner = Callable[[Sequence[str], Path | None], subprocess.CompletedProcess[str]]`
- `DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 60`
- `_COMMAND_TIMEOUT_EXIT_CODE = 124`
- `_ALLOWED_GIT_PROTOCOLS = "file:https:ssh"`
- `default_runner()` currently in `src/sv/source.py`
- `_noninteractive_subprocess_env()` currently in `src/sv/source.py`
- `_ssh_batch_mode_command()` currently in `src/sv/source.py`
- `_timeout_error_message()` currently in `src/sv/source.py`
- `_timeout_stream_text()` currently in `src/sv/source.py`
- `_missing_git_guidance()` currently in `src/sv/source.py`
- `default_process_runner()` currently in `src/sv/cli.py`

Implementation guidance:

```python
def default_process_runner(command: Sequence[str]) -> int:
    return subprocess.call(list(command))
```

For `default_runner()`, preserve current timeout, text decoding, invalid UTF-8 replacement behavior, missing Git guidance, and noninteractive environment exactly.

Do **not** move `_run_gh_api_with_limited_output()` or `_read_limited_process_output_file()` in this task. They currently raise `SourceBackendError`; moving them before Task 5 would create a `sv.process -> sv.source -> sv.process` cycle or force the source-backend error split too early. Leave them in `src/sv/source.py` for Task 4 and let them import/use the generic process constants/helpers as needed.

- [ ] **Step 4: Add compatibility imports**

In `src/sv/source.py`, import and re-export the moved process names that existing tests or downstream imports may reach through `sv.source`:

```python
from sv.process import (
    DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
    Runner,
    default_runner,
    _COMMAND_TIMEOUT_EXIT_CODE,
    _noninteractive_subprocess_env,
    _timeout_error_message,
    _timeout_stream_text,
)
```

Keep `_run_gh_api_with_limited_output()` and `_read_limited_process_output_file()` defined in `src/sv/source.py` in this task. Before committing, run `rg "source_module\._|from sv\.source import" tests/test_source.py tests -n` and ensure every moved process helper or constant still has a compatibility alias or the test has been intentionally retargeted.

In `src/sv/cli.py`, import `default_process_runner` from `sv.process` and remove the local definition.

- [ ] **Step 5: Run focused verification**

Run:

```bash
uv run pytest tests/test_process.py tests/test_source.py tests/test_cli.py -q --no-cov
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/sv/process.py src/sv/source.py src/sv/cli.py tests/test_process.py tests/test_source.py tests/test_cli.py
git commit -m "refactor: extract process runner adapter"
```

---

### Task 5: Split source backend adapters behind compatibility exports

**Files:**
- Create: `src/sv/source_backends/__init__.py`
- Create: `src/sv/source_backends/base.py`
- Create: `src/sv/source_backends/factory.py`
- Create: `src/sv/source_backends/github.py`
- Create: `src/sv/source_backends/git.py`
- Modify: `src/sv/source.py`
- Modify: `src/sv/catalog.py`
- Modify: `src/sv/source_cache.py`
- Test: `tests/test_source_backends.py`
- Test: `tests/test_source.py`
- Test: `tests/test_catalog.py`
- Test: `tests/test_source_cache.py`

- [ ] **Step 1: Write failing source backend package tests**

Create `tests/test_source_backends.py`:

```python
from __future__ import annotations

from sv.config import RepoConfig, SvPaths
from sv.source import SourceBackend as CompatSourceBackend
from sv.source import source_backends_for_repo as compat_source_backends_for_repo
from sv.source_backends.base import SourceBackend
from sv.source_backends.factory import source_backends_for_repo


def test_source_backend_protocol_is_available_from_package():
    assert SourceBackend is CompatSourceBackend


def test_source_backend_factory_is_available_from_package(tmp_path):
    repo = RepoConfig(id="Org/Repo", url="https://github.com/Org/Repo.git")
    paths = SvPaths.from_home(tmp_path)

    packaged = [backend.name for backend in source_backends_for_repo(repo, paths, update=False)]
    compat = [backend.name for backend in compat_source_backends_for_repo(repo, paths, update=False)]

    assert packaged == compat
    assert packaged == [
        "github-gh-api",
        "github-https-api",
        "git-treeless-partial",
        "git-blobless-sparse",
    ]
```

- [ ] **Step 2: Run tests and verify intended failure**

Run:

```bash
uv run pytest tests/test_source_backends.py -q --no-cov
```

Expected: fails because `sv.source_backends` does not exist.

- [ ] **Step 3: Create package with base types first**

Move these from `src/sv/source.py` into `src/sv/source_backends/base.py`:

- `SourceBackend` protocol
- `SourceBackendError`
- `SourceBackendFailure`
- `FakeSourceBackend`
- shared backend constants/helpers needed by multiple backend modules, including source-relative path normalization and source metadata size enforcement used by `FakeSourceBackend`, GitHub backends, and Git/local backends

In `src/sv/source.py`, re-export those names from `sv.source_backends.base`.

Update imports in `src/sv/catalog.py` and `src/sv/source_cache.py` to import base types from `sv.source_backends.base`; after `sv.source.py` becomes a façade, these modules must not import backend base types from `sv.source` because that can create cycles.

- [ ] **Step 4: Move GitHub backends**

Move GitHub-specific code from `src/sv/source.py` into `src/sv/source_backends/github.py`:

- `GitHubRepoRef`
- `GitHubHttpResponse`
- `GitHubGhApiBackend`
- `GitHubHttpsApiBackend`
- `parse_github_repo_ref`
- `_run_gh_api_with_limited_output()` and `_read_limited_process_output_file()` now that `SourceBackendError` lives in `sv.source_backends.base`
- GitHub API URL/content traversal helpers, GitHub constants, and response-size limits used only by those classes

Keep backend names exactly `github-gh-api` and `github-https-api`. Retarget tests that monkeypatch GitHub private constants/helpers from `sv.source` to `sv.source_backends.github`; keep `sv.source` direct class/type imports compatible, but do not rely on a façade alias to propagate monkeypatches into moved module globals.

- [ ] **Step 5: Move Git/local sparse backends**

Move Git/local source code from `src/sv/source.py` into `src/sv/source_backends/git.py`:

- `GitLocalSourceBackend`
- `LocalGitSourceBackend`
- `GitSparseSourceBackend`
- `GitTreelessPartialBackend`
- `GitBloblessSparseBackend`
- sparse checkout helpers
- per-repo source cache lock helper
- `ensure_source_repo()` and `ensure_source_repos()`
- `list_source_skills()`
- `reject_symlinked_source_cache_path()` and source path safety helpers
- Git URL/path validation helpers required by the factory, including `_validate_repo_url()` and `_local_source_repo_path()`

Preserve per-repo sparse checkout locking exactly. Update `src/sv/catalog.py` to import `LocalGitSourceBackend` and `reject_symlinked_source_cache_path` from `sv.source_backends.git`, not from the `sv.source` compatibility façade.

- [ ] **Step 6: Move factory**

Move `source_backends_for_repo()` into `src/sv/source_backends/factory.py`.

`src/sv/source_backends/__init__.py` should export:

```python
from sv.source_backends.base import SourceBackend, SourceBackendError, SourceBackendFailure
from sv.source_backends.factory import source_backends_for_repo
from sv.source_backends.github import (
    GitHubGhApiBackend,
    GitHubHttpResponse,
    GitHubHttpsApiBackend,
    GitHubRepoRef,
    parse_github_repo_ref,
)
from sv.source_backends.git import (
    GitBloblessSparseBackend,
    GitLocalSourceBackend,
    GitSparseSourceBackend,
    GitTreelessPartialBackend,
    LocalGitSourceBackend,
    reject_symlinked_source_cache_path,
)
```

`src/sv/source.py` should become a compatibility façade that re-exports all public/source-test imported names from `sv.source`, including `FakeSourceBackend`, `ensure_source_repo`, `ensure_source_repos`, `list_source_skills`, `reject_symlinked_source_cache_path`, `GitHubRepoRef`, `GitHubHttpResponse`, all backend classes, `SourceBackend`, `SourceBackendError`, `SourceBackendFailure`, `Runner`, and `default_runner`. If any legacy helper is intentionally left implemented in `sv.source` for this migration, call that out in a comment and keep its dependencies acyclic; do not leave its destination ambiguous.

Compatibility audit before Step 7:

```bash
rg "from sv\.source import|source_module\." tests/test_source.py tests/test_source_cache.py tests/test_cli_* tests/test_global_manifest.py tests/test_vault_mode_targets.py
```

For direct imports, keep `sv.source` re-exports. For tests that monkeypatch private implementation details such as `_MAX_GITHUB_API_RESPONSE_BYTES`, `_MAX_GITHUB_MATERIALIZATION_BYTES`, `base64.b64decode`, `subprocess.run`, `validate_materialization_source_tree`, `shutil.copytree`, or private helper functions, retarget the monkeypatch to the new implementation module (`sv.source_backends.github`, `sv.source_backends.git`, or `sv.process`) so the monkeypatch affects the code path under test.

- [ ] **Step 7: Run focused verification**

Run:

```bash
uv run pytest tests/test_source_backends.py tests/test_source.py tests/test_catalog.py tests/test_source_cache.py -q --no-cov
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/sv/source_backends src/sv/source.py src/sv/catalog.py src/sv/source_cache.py tests/test_source_backends.py tests/test_source.py tests/test_catalog.py tests/test_source_cache.py
git commit -m "refactor: split source backend adapters"
```

---

### Task 6: Wrap filesystem/materialization operations in an adapter

**Files:**
- Modify: `src/sv/materialization.py`
- Modify: `src/sv/project.py`
- Test: `tests/test_materialization.py`
- Test: `tests/test_project.py`

- [ ] **Step 1: Write failing materialization adapter tests**

Add `MaterializationAdapter` to the existing grouped `from sv.materialization import (...)` block in `tests/test_materialization.py`, then append these tests:

```python
def test_materialization_adapter_installs_temp_folder(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: alpha\ndescription: Alpha.\n---\n")
    temp = tmp_path / ".alpha.tmp"
    target = tmp_path / "alpha"

    adapter = MaterializationAdapter()
    adapter.copy_skill_folder_to_temp(source, temp, error_message="copy failed")
    adapter.install_materialized_skill_folder(temp, target)

    assert not temp.exists()
    assert (target / "SKILL.md").is_file()


def test_materialization_adapter_restores_backup_on_replace_callback_failure(tmp_path):
    target = tmp_path / "alpha"
    target.mkdir()
    (target / "old.txt").write_text("old")
    materialized = tmp_path / ".alpha.new"
    materialized.mkdir()
    (materialized / "new.txt").write_text("new")
    backup = tmp_path / ".alpha.backup"

    adapter = MaterializationAdapter()

    def fail_after_replace():
        raise RuntimeError("manifest write failed")

    try:
        adapter.replace_with_materialized_skill_folder(
            materialized,
            target,
            backup,
            after_replace=fail_after_replace,
        )
    except RuntimeError as exc:
        assert str(exc) == "manifest write failed"
    else:
        raise AssertionError("expected callback failure")

    assert (target / "old.txt").read_text() == "old"
    assert not backup.exists()
```

- [ ] **Step 2: Run tests and verify intended failure**

Run:

```bash
uv run pytest tests/test_materialization.py::test_materialization_adapter_installs_temp_folder tests/test_materialization.py::test_materialization_adapter_restores_backup_on_replace_callback_failure -q --no-cov
```

Expected: fails because `MaterializationAdapter` does not exist.

- [ ] **Step 3: Introduce adapter without behavior changes**

In `src/sv/materialization.py`:

- Do **not** rename the existing public function bodies in this task. Existing tests monkeypatch `materialization_module.validate_materialization_source_tree` and expect `copy_skill_folder_to_temp()` to call that public symbol. Keep those public functions as the canonical implementations for now.
- Add the adapter as a thin delegating façade over the existing public functions:

```python
@dataclass(frozen=True)
class MaterializationAdapter:
    def copy_skill_folder_to_temp(self, source: Path, temp_target: Path, *, error_message: str) -> Path:
        return copy_skill_folder_to_temp(source, temp_target, error_message=error_message)

    def install_materialized_skill_folder(
        self,
        materialized_target: Path,
        target: Path,
        *,
        error_message: str = "Failed to install materialized skill folder",
        after_install: Callable[[], None] | None = None,
    ) -> None:
        install_materialized_skill_folder(
            materialized_target,
            target,
            error_message=error_message,
            after_install=after_install,
        )

    def replace_with_materialized_skill_folder(
        self,
        materialized_target: Path,
        target: Path,
        backup_target: Path,
        *,
        error_message: str = "Failed to replace materialized skill folder",
        after_replace: Callable[[], None] | None = None,
    ) -> None:
        replace_with_materialized_skill_folder(
            materialized_target,
            target,
            backup_target,
            error_message=error_message,
            after_replace=after_replace,
        )

    def validate_materialization_source_tree(self, source: Path) -> None:
        validate_materialization_source_tree(source)

    def remove_materialization_path(self, path: Path, *, ignore_errors: bool = False) -> None:
        remove_materialization_path(path, ignore_errors=ignore_errors)


DEFAULT_MATERIALIZATION_ADAPTER = MaterializationAdapter()
```

The default adapter error messages are only for direct adapter use in tests or future callers. Existing public functions still require their current explicit `error_message` arguments.

- [ ] **Step 4: Route project internals through the adapter wrappers**

In `src/sv/project.py`, keep imports of public materialization functions or switch to `DEFAULT_MATERIALIZATION_ADAPTER`. Do not change operation order. If switching to the adapter, update each current call site to use the matching method and pass the existing explicit `error_message` strings for install/replace operations:

- copy/stage source skill folder
- install prepared temp folder
- replace target with prepared temp and backup
- cleanup temp/backup paths, including existing `ignore_errors=True` behavior

- [ ] **Step 5: Run focused verification**

Run:

```bash
uv run pytest tests/test_materialization.py tests/test_project.py -q --no-cov
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/sv/materialization.py src/sv/project.py tests/test_materialization.py tests/test_project.py
git commit -m "refactor: add materialization adapter"
```

---

### Task 7: Add persistence store adapters

**Files:**
- Create: `src/sv/stores.py`
- Modify: `src/sv/project.py`
- Modify: `src/sv/cli.py`
- Test: `tests/test_stores.py`
- Test: `tests/test_manifest.py`
- Test: `tests/test_config.py`
- Test: `tests/test_project.py`

- [ ] **Step 1: Write failing store adapter tests**

Create `tests/test_stores.py`:

```python
from __future__ import annotations

from pathlib import Path

from sv.config import SvPaths
from sv.manifest import GlobalSourceState, ManifestEntry
from sv.stores import ConfigStore, GlobalManifestStore, ProjectManifestStore


def test_project_manifest_store_round_trips_entries(tmp_path: Path):
    skills_dir = tmp_path / ".pi" / "skills"
    store = ProjectManifestStore(skills_dir)
    entry = ManifestEntry(
        name="alpha",
        repo_id="Org/Repo",
        repo_url="https://github.com/Org/Repo.git",
        source_path="skills/alpha",
        description="Alpha skill.",
        installed_content_hash="sha256:" + "a" * 64,
    )

    store.save({"alpha": entry})

    assert store.load()["alpha"] == entry


def test_global_manifest_store_round_trips_source_state(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    store = GlobalManifestStore(paths)
    state = GlobalSourceState(
        repo_id="Org/Repo",
        repo_url="https://github.com/Org/Repo.git",
        backend="github-https-api",
    )

    store.save({"Org/Repo": state})

    assert store.load()["Org/Repo"] == state


def test_config_store_delegates_to_existing_config_persistence(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    store = ConfigStore(paths)

    result = store.add_repo("Org/Repo")

    assert result.repo.id == "Org/Repo"
    assert store.load().repos[0].id == "Org/Repo"
```

- [ ] **Step 2: Run tests and verify intended failure**

Run:

```bash
uv run pytest tests/test_stores.py -q --no-cov
```

Expected: fails with `ModuleNotFoundError: No module named 'sv.stores'`.

- [ ] **Step 3: Implement store adapters**

Create `src/sv/stores.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sv.config import (
    RepoChangeResult,
    RepoConfig,
    SvConfig,
    SvPaths,
    add_repo as add_repo_config,
    load_config as load_sv_config,
    remove_repo as remove_repo_config,
)
from sv.manifest import (
    GlobalSourceState,
    ManifestEntry,
    load_global_manifest,
    load_manifest,
    save_global_manifest,
    save_manifest,
)


@dataclass(frozen=True)
class ProjectManifestStore:
    skills_dir: Path

    def load(self) -> dict[str, ManifestEntry]:
        return load_manifest(self.skills_dir)

    def save(self, entries: dict[str, ManifestEntry]) -> None:
        save_manifest(self.skills_dir, entries)


@dataclass(frozen=True)
class GlobalManifestStore:
    paths: SvPaths

    def load(self) -> dict[str, GlobalSourceState]:
        return load_global_manifest(self.paths)

    def save(self, states: dict[str, GlobalSourceState]) -> None:
        save_global_manifest(self.paths, states)


@dataclass(frozen=True)
class ConfigStore:
    paths: SvPaths

    def load(self) -> SvConfig:
        return load_sv_config(self.paths)

    def add_repo(self, repo: str, *, skills_paths: Sequence[str] = ()) -> RepoChangeResult:
        return add_repo_config(self.paths, repo, skills_paths=skills_paths)

    def remove_repo(self, repo_id: str) -> RepoConfig:
        return remove_repo_config(self.paths, repo_id)
```

Adjust signatures to match current `add_repo()` / `remove_repo()` definitions exactly.

- [ ] **Step 4: Route high-churn call sites through stores**

In `src/sv/project.py`:

- Replace direct `load_manifest(project_skills_dir)` / `save_manifest(project_skills_dir, manifest)` pairs inside add/remove/sync/update/refresh workflows with `ProjectManifestStore(project_skills_dir).load()` and `.save(...)`. This includes `_refresh_skill_states()` and `_refresh_local_skill_states()`.
- Preserve all existing manifest mutation order and rollback callbacks.
- Keep `upsert_manifest_entry()` and `remove_manifest_entry()` for Pi-target helper semantics unless you add equivalent explicit `ProjectManifestStore` methods and tests in this task; do not silently replace them with generic load/mutate/save code.

In `src/sv/cli.py`:

- Keep `_load_config_for_source_command(paths)` as the single entry point for source-command config loading so first-run prompt/guidance behavior is unchanged; inside that helper, use `ConfigStore(paths).load()` for the raw config read.
- Use `ConfigStore(paths).add_repo(...)` and `.remove_repo(...)` in repo command handlers and initial-source prompting.
- Use `GlobalManifestStore(paths)` where global source state is read/written.

Keep the public functions in `sv.config` and `sv.manifest` unchanged.

- [ ] **Step 5: Run focused verification**

Run:

```bash
uv run pytest tests/test_stores.py tests/test_manifest.py tests/test_config.py tests/test_project.py tests/test_cli.py tests/test_first_run_source_config.py -q --no-cov
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/sv/stores.py src/sv/project.py src/sv/cli.py tests/test_stores.py tests/test_manifest.py tests/test_config.py tests/test_project.py tests/test_cli.py
git commit -m "refactor: add persistence store adapters"
```

---

### Task 8: Formalize cache adapters

**Files:**
- Modify: `src/sv/source_cache.py`
- Test: `tests/test_source_cache.py`

- [ ] **Step 1: Write failing cache adapter tests**

Append to `tests/test_source_cache.py`:

```python
from sv.config import RepoConfig, SvPaths
from sv.source_cache import CatalogCacheStore, SkillBodyCacheStore, catalog_cache_path, skill_body_cache_path


def test_catalog_cache_store_exposes_existing_cache_path(tmp_path):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Repo", url="https://github.com/Org/Repo.git")

    assert CatalogCacheStore(paths).path_for(repo) == catalog_cache_path(paths, repo)


def test_skill_body_cache_store_exposes_existing_body_path(tmp_path):
    paths = SvPaths.from_home(tmp_path)
    content_hash = "sha256:" + "a" * 64

    assert SkillBodyCacheStore(paths).path_for(content_hash) == skill_body_cache_path(paths, content_hash)
```

- [ ] **Step 2: Run tests and verify intended failure**

Run:

```bash
uv run pytest tests/test_source_cache.py::test_catalog_cache_store_exposes_existing_cache_path tests/test_source_cache.py::test_skill_body_cache_store_exposes_existing_body_path -q --no-cov
```

Expected: fails because `CatalogCacheStore` and `SkillBodyCacheStore` do not exist.

- [ ] **Step 3: Add cache store classes**

In `src/sv/source_cache.py`, add:

```python
@dataclass(frozen=True)
class CatalogCacheStore:
    paths: SvPaths

    def path_for(self, repo: RepoConfig) -> Path:
        return catalog_cache_path(self.paths, repo)

    def load(self, repo: RepoConfig) -> CachedCatalogDocument | None:
        return load_cached_catalog(self.paths, repo)

    def save(self, repo: RepoConfig, document: CachedCatalogDocument) -> None:
        save_cached_catalog(self.paths, repo, document)
```

and:

```python
@dataclass(frozen=True)
class SkillBodyCacheStore:
    paths: SvPaths

    def path_for(self, content_hash: str) -> Path:
        return skill_body_cache_path(self.paths, content_hash)

    def try_materialize(
        self,
        *,
        content_hash: str,
        skill_name: str,
        destination: Path,
        now: datetime,
        warn: Warn | None = None,
    ) -> bool:
        return try_materialize_from_skill_body_cache(
            self.paths,
            content_hash=content_hash,
            skill_name=skill_name,
            destination=destination,
            now=now,
            warn=warn,
        )

    def store(
        self,
        source_skill_dir: Path,
        *,
        skill_name: str,
        content_hash: str,
        source_reference: str,
        now: datetime,
    ) -> None:
        store_skill_body_cache(
            self.paths,
            source_skill_dir,
            skill_name=skill_name,
            content_hash=content_hash,
            source_reference=source_reference,
            now=now,
        )
```

Use the current `source_cache.py` public functions as compatibility wrappers.

- [ ] **Step 4: Route internal cache workflows through stores**

In `src/sv/source_cache.py`:

- Update `get_catalog_with_cache()` to instantiate `CatalogCacheStore(paths)` and call store methods for path/load/save operations.
- Update `wrap_catalog_with_skill_body_cache()` and body-cache materializer helpers to instantiate `SkillBodyCacheStore(paths)` and call store methods for path/materialize/store operations.
- Keep current cache locks, stale fallback warnings, cache-only failures, pruning behavior, and security failures unchanged.

- [ ] **Step 5: Run focused verification**

Run:

```bash
uv run pytest tests/test_source_cache.py tests/test_cli_list_contracts.py tests/test_cli_add_contracts.py tests/test_cli_sync_contracts.py tests/test_cli_update_contracts.py tests/test_cli_status_contracts.py -q --no-cov
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/sv/source_cache.py tests/test_source_cache.py tests/test_cli_list_contracts.py tests/test_cli_add_contracts.py tests/test_cli_sync_contracts.py tests/test_cli_update_contracts.py tests/test_cli_status_contracts.py
git commit -m "refactor: formalize cache adapters"
```

---

### Task 9: Wire adapters together at CLI/service boundaries

**Files:**
- Modify: `src/sv/cli.py`
- Modify: `src/sv/project.py`
- Modify: `src/sv/ui.py`
- Modify: `src/sv/parallel.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_ui.py`
- Test: `tests/test_project.py`
- Test: `tests/test_parallel.py`

- [ ] **Step 1: Write failing boundary wiring tests**

Append to `tests/test_cli.py`:

```python

def test_handle_validates_sv_jobs_from_injected_environment(tmp_path):
    from io import StringIO

    from sv.cli import handle
    from sv.runtime import Runtime

    runtime = Runtime(
        cwd=tmp_path,
        home=tmp_path / "home",
        env={"SV_JOBS": "invalid"},
        stdin=StringIO(),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    exit_code = handle(
        parse(["list"]),
        cwd=runtime.cwd,
        home=runtime.home,
        runtime=runtime,
    )

    assert exit_code == 1
    assert (
        "SV_JOBS must be an integer between 1 and 64."
        in runtime.stderr.getvalue()
    )
```

Append to `tests/test_ui.py`:

```python

def test_plain_output_and_tty_ui_are_separate_adapter_instances():
    plain = PlainOutput()
    tty = TtyUi(
        selector=lambda items, **kwargs: list(items),
        table_browser=lambda headers, rows, **kwargs: None,
    )

    assert plain.table(["A"], [["B"]]).splitlines()[0] == "A"
    assert tty.select_many(["alpha"]) == ["alpha"]
    assert tty.browse_table(["Skill"], [["alpha"]]) is None
```

- [ ] **Step 2: Run tests and verify intended failure**

Run:

```bash
uv run pytest tests/test_cli.py::test_handle_validates_sv_jobs_from_injected_environment tests/test_ui.py::test_plain_output_and_tty_ui_are_separate_adapter_instances -q --no-cov
```

Expected: CLI test fails because `handle()` has no `runtime` parameter; UI test passes only if Task 1 is complete.

- [ ] **Step 3: Add optional runtime to `handle()`**

In `src/sv/cli.py`:

- Update `handle()` signature to include a keyword-only `runtime: Runtime | None = None` after injected chooser arguments, preserving existing positional compatibility for `git_runner`, `process_runner`, `skill_selector`, and `skill_chooser`.
- At the top of `handle()`, before the `try` block:

```python
runtime = (
    Runtime(
        cwd=cwd,
        home=home,
        env=os.environ,
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    if runtime is None
    else runtime
)
cwd = runtime.cwd
home = runtime.home
paths = SvPaths.from_home(runtime.home)
```

Import `os`, and import `Runtime` from `sv.runtime`. If `cli.py` already uses `sys`, keep the existing `sys` import.

- Use `runtime.cwd` and `runtime.home` for local variables where `cwd`/`home` are currently used at handle entry, including the `record_global_source_state = _should_record_global_source_state(cwd, home)` calculation.
- Replace the early `configured_jobs()` call with `configured_jobs(runtime.env)`.
- In the top-level `except SvError as exc` block, print to `runtime.stderr` instead of `sys.stderr`.
- Keep downstream helper signatures unchanged unless a helper needs runtime immediately. When later migrating prompt or terminal-width helpers, use `runtime.can_prompt()`, `runtime.prompt()`, and `runtime.width()` rather than adding new direct `sys.stdin`/`sys.stdout`/`input()`/`shutil.get_terminal_size()` reads.

- [ ] **Step 4: Use stores and adapters at service boundaries**

In `src/sv/cli.py`:

- Use `ConfigStore(paths)` for repo add/remove and raw config reads inside existing config-loading helpers. Do not replace calls to `_load_config_for_source_command(paths)` with bare `ConfigStore(paths).load()`.
- Use `GlobalManifestStore(paths)` for global source-state functions.
- Use `browse_tty_table` and `select_tty_items` only through `sv.ui` imports.
- Use `default_process_runner` from `sv.process`.
- Use `source_backends_for_repo` from `sv.source_backends.factory` for new internal imports, while preserving `sv.source.source_backends_for_repo` compatibility.

In `src/sv/project.py`:

- Use `ProjectManifestStore` for manifest load/save.
- Use `DEFAULT_MATERIALIZATION_ADAPTER` or public materialization wrappers consistently.

Do not change visible output strings in this task.

- [ ] **Step 5: Run focused verification**

Run:

```bash
uv run pytest tests/test_cli.py tests/test_first_run_source_config.py tests/test_ui.py tests/test_project.py tests/test_parallel.py -q --no-cov
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/sv/cli.py src/sv/project.py src/sv/ui.py src/sv/parallel.py tests/test_cli.py tests/test_ui.py tests/test_project.py tests/test_parallel.py
git commit -m "refactor: wire adapters at CLI boundaries"
```

---

### Task 10: Add an internal adapter map document

**Files:**
- Create: `docs/adapters.md`
- Test: `tests/test_docs.py`

- [ ] **Step 1: Write failing docs test**

Append to `tests/test_docs.py`:

```python

def test_adapter_architecture_document_lists_all_adapter_modules():
    content = (DOCS_DIR / "adapters.md").read_text()

    for module_name in [
        "sv.ui",
        "sv.runtime",
        "sv.parallel",
        "sv.process",
        "sv.source_backends",
        "sv.materialization",
        "sv.stores",
        "sv.source_cache",
    ]:
        assert f"`{module_name}`" in content
```

Use the existing docs test constants/import style in `tests/test_docs.py`; if `DOCS_DIR` has a different name, use the existing docs root constant from that file.

- [ ] **Step 2: Run tests and verify intended failure**

Run:

```bash
uv run pytest tests/test_docs.py::test_adapter_architecture_document_lists_all_adapter_modules -q --no-cov
```

Expected: fails because `docs/adapters.md` does not exist.

- [ ] **Step 3: Write `docs/adapters.md`**

Create `docs/adapters.md` with these headings and exact module references:

```markdown
# sv adapter architecture

`sv` keeps pure domain logic as plain functions and places side effects behind small adapters.

## Adapter modules

| Adapter | Module | Owns | Does not own |
| --- | --- | --- | --- |
| UI/terminal | `sv.ui` | Plain tables, TTY selection, TTY table browsing façade | Business decisions about which command to run |
| Runtime/environment | `sv.runtime` | cwd, home, env, streams, prompts, terminal width, current time | Command parsing |
| Concurrency | `sv.parallel` | worker-count policy, ordered worker execution | Final manifest/cache writes |
| Process execution | `sv.process` | subprocess execution, noninteractive env, Pi process launcher | Source backend selection |
| Source backends | `sv.source_backends` | GitHub, HTTPS, local Git, sparse Git source access | Project install policy |
| Filesystem/materialization | `sv.materialization` | safe copy, validation, install, replace, remove, rollback | Manifest semantics |
| Persistence stores | `sv.stores` | config and manifest store façades | TOML schema definitions |
| Cache | `sv.source_cache` | catalog cache, skill body cache, cache-aware materializers | Source provider implementation |

## Compatibility policy

Existing public functions remain available during adapter migration. New code should import adapter modules directly; compatibility modules such as `sv.source` continue to re-export old names for tests and downstream users.
```

- [ ] **Step 4: Run docs verification**

Run:

```bash
uv run pytest tests/test_docs.py -q --no-cov
```

Expected: all docs tests pass.

- [ ] **Step 5: Commit**

```bash
git add docs/adapters.md tests/test_docs.py
git commit -m "docs: document adapter architecture"
```

---

### Task 11: Final release-gate verification

**Files:**
- No source edits expected unless verification finds issues.

- [ ] **Step 1: Run lint**

Run:

```bash
uv run ruff check .
```

Expected: exits `0` with no lint errors.

- [ ] **Step 2: Run type check**

Run:

```bash
uv run ty check src tests
```

Expected: exits `0` with no type errors.

- [ ] **Step 3: Run full test suite with coverage**

Run:

```bash
uv run pytest --cov=sv --cov-report=term-missing
```

Expected: exits `0`; coverage remains at or above the `95` threshold in `pyproject.toml`.

- [ ] **Step 4: Build package**

Run:

```bash
rm -rf dist
uv build
```

Expected: exits `0` and creates exactly one wheel under `dist/`.

- [ ] **Step 5: Smoke test built wheel**

Run:

```bash
tmp_venv="$(mktemp -d)"
trap 'rm -rf "$tmp_venv"' EXIT
python -m venv "$tmp_venv"
wheel_path="$(python -c 'from pathlib import Path; wheels = sorted(Path("dist").glob("sv-*.whl")); assert len(wheels) == 1, wheels; print(wheels[0])')"
expected_version="$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
uv pip install --python "$tmp_venv/bin/python" --link-mode=copy --no-index "$wheel_path"
"$tmp_venv/bin/sv" --help
EXPECTED_SV_VERSION="$expected_version" "$tmp_venv/bin/python" -c 'import os, sv; assert sv.__version__ == os.environ["EXPECTED_SV_VERSION"], sv.__version__'
```

Expected: exits `0`; installed `sv --help` runs; installed package version matches `pyproject.toml`.

- [ ] **Step 6: Commit any verification fixes**

If verification required fixes in source, tests, docs, project config, or workflow files:

```bash
git add src/sv tests docs pyproject.toml .github/workflows/tests.yml
git commit -m "fix: complete adapter modularization verification"
```

If no fixes were needed, do not create an empty commit.

---

## Self-review checklist

- Each of the eight requested adapters maps to at least one task:
  - UI/terminal: Task 1
  - Runtime/environment: Task 2 and Task 9
  - Concurrency: Task 3
  - Process runner: Task 4
  - Source backend: Task 5
  - Filesystem/materialization: Task 6
  - Persistence: Task 7
  - Cache: Task 8
- All behavior changes are internal refactors; no CLI behavior change is intended.
- Public compatibility imports remain in place.
- Each task starts with a failing test step.
- Each task has exact paths, commands, expected results, and commit messages.
- Final release gate matches `docs/testing.md` and `.github/workflows/tests.yml`.
- Existing monkeypatch-heavy tests for `sv.source` and `sv.materialization` are either preserved through public compatibility behavior or intentionally retargeted to the new implementation modules in the task that moves those internals.
- Runtime wiring preserves existing positional `handle()` compatibility while using injected env/stderr for top-level validation and errors.

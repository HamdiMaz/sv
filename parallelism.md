# Parallelism Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded, deterministic parallelism to `sv` source refresh, bulk add/sync/update materialization, and read-only hash/index work without weakening current safety, cache, or manifest guarantees.

**Architecture:** Introduce one small standard-library parallel execution helper that preserves input-order results, worker-count limits, and first-error reporting. Parallelize independent work only: source repos refresh concurrently while each repo keeps backend fallback order; bulk skill operations prepare materialized temp trees concurrently and commit final filesystem/manifest mutations serially; read-only status/index hashing runs per skill concurrently and saves one deterministic result. Shared mutable resources stay serialized with process-local locks: project/vault manifests, final target renames, cache metadata writes, skill-body pruning, and sparse Git cache mutation for the same repo.

**Tech Stack:** Python 3.14 standard library (`concurrent.futures`, `threading`, `os`), existing `SvError`, `RepoConfig`, `SourceSkill`, source backends, `source_cache`, `project`, `index`, pytest, ruff, ty.

---

## Defaults and behavior locked by this plan

- Parallelism is **bounded** and uses threads. This workload is I/O-heavy: Git/GitHub calls, local file traversal, hashing, copying, TOML reads/writes.
- Default worker count is `min(8, (os.cpu_count() or 1) + 4)`. This avoids spawning one subprocess per configured repo when many repos exist.
- `SV_JOBS=1` disables thread pools and runs the same code path sequentially. This gives users and tests a simple debugging escape hatch.
- `SV_JOBS=N` accepts decimal integers from `1` through `64`. Invalid values raise `SvError("SV_JOBS must be an integer between 1 and 64.")`.
- Results, warnings, user output, and manifest/index/cache writes are deterministic. Completion order never affects visible order.
- Worker exceptions are collected and re-raised by original input order. If repo 1 and repo 3 both fail, the command reports repo 1 first, even if repo 3 finished first.
- Source backend fallback order remains unchanged **within one repo**:
  1. `github-gh-api`
  2. `github-https-api`
  3. `git-treeless-partial`
  4. `git-blobless-sparse`
- Different repos may refresh in parallel. Backends for the same repo do not run in parallel.
- Candidate `SKILL.md` reads within one repo remain sequential in the first implementation pass unless they are part of read-only index scanning. This avoids thread-safety surprises with backend instances.
- The same sparse Git repo cache path may not be mutated by two threads at once. Lock by `repo_path.resolve()` where possible, and by the raw `Path` if resolving fails.
- Skill-body cache writes, body-cache pruning, and cached catalog hash writeback are serialized with one process-local `RLock`. This prevents lost cached metadata updates when bulk materialization discovers multiple body hashes from the same repo.
- Project and vault manifest writes remain serial. No worker writes `.sv/manifest.toml` or `.pi/skills/.sv-manifest.toml`.
- Final target mutation remains serial. No worker renames a prepared temp tree into `.pi/skills/<skill>` or `skills/<skill>`.
- Bulk add/sync/update workers may create and validate hidden temp directories such as `.alpha.sv-add-tmp` or `.alpha.sv-sync-tmp`. Commit steps consume those temps in stable input order.
- If a parallel prepare step fails before the serial commit phase starts, prepared temps from other workers are removed and no final targets are changed by that phase.
- If a serial commit fails after previous serial commits already succeeded, existing rollback behavior for that one target stays in force. The command reports the failure and leaves already committed earlier targets as successful, matching the current command style.
- Source refresh cache policy stays unchanged:
  - `list`, `search`, `add`, and `add --all`: 24-hour metadata TTL and stale fallback warning when cached metadata exists.
  - `sync`, `update`, and source-aware `status`: force-refresh by default and fail closed on source refresh errors.
  - `--cached`: never refreshes source metadata or source bodies.
- Parallel code must not interleave stdout/stderr. Workers return warning strings; the parent prints them after joining in input order.
- Tests must not rely on timing sleeps when an event/barrier can prove overlap. Any timeout used to prevent hangs must be at most 2 seconds and must fail with an explicit assertion message.

## File structure

- Create `src/sv/parallel.py`
  - Worker-count parsing.
  - Ordered thread-pool map helper.
  - First-error-by-input-order behavior.
- Modify `src/sv/catalog.py`
  - Process unique repos concurrently in `build_source_catalog_from_backends`.
  - Keep backend fallback serial inside one repo.
  - Capture worker warnings and replay them in repo order.
  - Attach per-backend materialization locks to catalog entries.
- Modify `src/sv/source.py`
  - Add per-repo-cache `RLock` helper.
  - Lock sparse checkout mutation and sparse materialization cleanup for one `repo_path`.
  - Parallelize `ensure_source_repos` across independent repos.
- Modify `src/sv/source_cache.py`
  - Add one process-local cache write lock.
  - Serialize skill-body cache writes/prunes and cached catalog body-hash writeback.
  - Serialize cached-entry source materialization by repo when source fallback is used.
- Modify `src/sv/project.py`
  - Add plan/prepare dataclasses for add/sync/update.
  - Parallelize materialization/hash preparation for bulk operations.
  - Keep final target renames and manifest writes serial.
  - Parallelize read-only local state refresh hashing.
- Modify `src/sv/index.py`
  - Parallelize per-skill parse/hash work after deterministic candidate discovery.
  - Replay invalid-skill warnings in candidate order.
- Modify `src/sv/cli.py`
  - No new CLI flag is required.
  - Source refresh gains parallelism through `catalog.py`/`source.py`.
  - Bulk add/sync/update gains parallelism through `project.py`.
  - Invalid `SV_JOBS` surfaces as the existing `error: ...` CLI failure.
- Create `tests/test_parallel.py`
  - Unit tests for worker-count parsing, ordered results, sequential mode, and first input-order error.
- Modify tests
  - `tests/test_catalog.py`
  - `tests/test_source.py`
  - `tests/test_source_cache.py`
  - `tests/test_project.py`
  - `tests/test_index.py`
  - `tests/test_cli_list_contracts.py`
  - `tests/test_cli_add_contracts.py`
  - `tests/test_cli_sync_contracts.py`
  - `tests/test_cli_update_contracts.py`
  - `tests/test_cli_status_contracts.py`
- Modify docs
  - `README.md`
  - `docs/commands.md`
  - `docs/usage.md`
  - `docs/testing.md`
  - `docs/troubleshooting.md`

---

### Task 1: Add bounded ordered parallel helper

**Files:**
- Create: `src/sv/parallel.py`
- Create: `tests/test_parallel.py`

- [ ] **Step 1: Write tests for worker-count parsing**

Create `tests/test_parallel.py` with this content:

```python
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from sv.errors import SvError
from sv.parallel import configured_jobs, map_ordered


def test_configured_jobs_defaults_to_bounded_io_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SV_JOBS", raising=False)
    monkeypatch.setattr("sv.parallel.os.cpu_count", lambda: 128)

    assert configured_jobs() == 8


def test_configured_jobs_accepts_explicit_sequential_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SV_JOBS", "1")

    assert configured_jobs() == 1


def test_configured_jobs_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for value in ("", "0", "65", "two", "1.5", "-1"):
        monkeypatch.setenv("SV_JOBS", value)
        with pytest.raises(SvError, match="SV_JOBS must be an integer between 1 and 64"):
            configured_jobs()
```

- [ ] **Step 2: Write tests for ordered parallel execution**

Append these tests to `tests/test_parallel.py`:

```python

def test_map_ordered_preserves_input_order_when_workers_complete_out_of_order() -> None:
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()

    def worker(value: int) -> str:
        if value == 0:
            first_started.set()
            assert second_started.wait(2), "second worker did not start while first was blocked"
            assert release_first.wait(2), "first worker was not released"
            return "first"
        second_started.set()
        assert first_started.wait(2), "first worker did not start"
        release_first.set()
        return "second"

    assert map_ordered([0, 1], worker, jobs=2) == ["first", "second"]


def test_map_ordered_runs_inline_when_jobs_is_one() -> None:
    thread_names: list[str] = []

    def worker(value: int) -> int:
        thread_names.append(threading.current_thread().name)
        return value * 2

    assert map_ordered([1, 2, 3], worker, jobs=1) == [2, 4, 6]
    assert thread_names == [threading.current_thread().name] * 3


def test_map_ordered_reraises_first_failure_by_input_order() -> None:
    def worker(value: str) -> str:
        if value == "first":
            raise SvError("first failure")
        if value == "second":
            raise SvError("second failure")
        return value

    with pytest.raises(SvError, match="first failure"):
        map_ordered(["ok", "first", "second"], worker, jobs=3)
```

- [ ] **Step 3: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_parallel.py -q --no-cov
```

Expected: import failure because `sv.parallel` does not exist.

- [ ] **Step 4: Create `src/sv/parallel.py`**

Create `src/sv/parallel.py` with this exact implementation:

```python
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from typing import TypeVar

from sv.errors import SvError

T = TypeVar("T")
R = TypeVar("R")

_DEFAULT_MAX_JOBS = 8
_MAX_JOBS = 64


def configured_jobs(env: Mapping[str, str] | None = None) -> int:
    """Return the configured sv worker count.

    SV_JOBS is intentionally process-wide because these are internal worker pools,
    not a user-facing command option. Tests may pass an explicit environment map.
    """

    values = os.environ if env is None else env
    raw_value = values.get("SV_JOBS")
    if raw_value is None:
        return min(_DEFAULT_MAX_JOBS, (os.cpu_count() or 1) + 4)
    try:
        jobs = int(raw_value, 10)
    except ValueError as exc:
        raise SvError("SV_JOBS must be an integer between 1 and 64.") from exc
    if jobs < 1 or jobs > _MAX_JOBS:
        raise SvError("SV_JOBS must be an integer between 1 and 64.")
    return jobs


def map_ordered(
    items: Sequence[T],
    worker: Callable[[T], R],
    *,
    jobs: int | None = None,
) -> list[R]:
    """Run independent work and return results in input order.

    Exceptions are collected and the first exception by input index is re-raised.
    This keeps command failures deterministic even when workers finish out of order.
    """

    item_list = list(items)
    if not item_list:
        return []

    worker_count = configured_jobs() if jobs is None else jobs
    if worker_count < 1 or worker_count > _MAX_JOBS:
        raise SvError("SV_JOBS must be an integer between 1 and 64.")
    if worker_count == 1 or len(item_list) == 1:
        return [worker(item) for item in item_list]

    results: list[R | None] = [None] * len(item_list)
    failures: list[BaseException | None] = [None] * len(item_list)
    max_workers = min(worker_count, len(item_list))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="sv") as executor:
        futures = {
            executor.submit(worker, item): index
            for index, item in enumerate(item_list)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except BaseException as exc:  # noqa: BLE001 - preserve original exception type
                failures[index] = exc

    for failure in failures:
        if failure is not None:
            raise failure

    return [result for result in results if result is not None]
```

- [ ] **Step 5: Run helper tests and quality checks**

Run:

```bash
uv run pytest tests/test_parallel.py -q --no-cov
uv run ruff check src/sv/parallel.py tests/test_parallel.py
uv run ty check src tests
```

Expected: all pass. If `ty` complains about the final list narrowing in `map_ordered`, replace the return line with this explicit loop:

```python
    ordered: list[R] = []
    for result in results:
        if result is None:
            raise AssertionError("parallel worker result missing after successful join")
        ordered.append(result)
    return ordered
```

- [ ] **Step 6: Commit Task 1**

```bash
git add src/sv/parallel.py tests/test_parallel.py
git commit -m "feat: add ordered parallel helper"
```

---

### Task 2: Parallelize lightweight source catalog discovery by repo

**Files:**
- Modify: `src/sv/catalog.py`
- Modify: `tests/test_catalog.py`

- [ ] **Step 1: Add a concurrency test for independent repo discovery**

Append this helper backend and test to `tests/test_catalog.py` after the existing backend test doubles:

```python
class PeerWaitingBackend:
    name = "peer-waiting"

    def __init__(self, skill_name: str, started: threading.Event, peer_started: threading.Event):
        self.skill_name = skill_name
        self.started = started
        self.peer_started = peer_started

    def read_index(self) -> bytes | None:
        self.started.set()
        assert self.peer_started.wait(2), "independent repo discovery did not run concurrently"
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
```

Add this import near the top of `tests/test_catalog.py`:

```python
import threading
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```bash
uv run pytest tests/test_catalog.py::test_build_source_catalog_from_backends_refreshes_independent_repos_in_parallel -q --no-cov
```

Expected: failure because `build_source_catalog_from_backends` does not accept `jobs` and refreshes repos sequentially.

- [ ] **Step 3: Add repo-result dataclass and warning capture**

In `src/sv/catalog.py`, add imports:

```python
import threading
from sv.parallel import map_ordered
```

Add this dataclass after `_BackendCatalogResult`:

```python
@dataclass(frozen=True)
class _RepoBackendCatalogResult:
    repo_id: str
    entries: tuple[SourceSkill, ...]
    failures: tuple[SourceBackendFailure, ...]
    refreshed: bool
    refreshed_backend: str | None = None
    index_hash: str | None = None
    warnings: tuple[str, ...] = ()
```

- [ ] **Step 4: Extract unique repo planning from `build_source_catalog_from_backends`**

In `src/sv/catalog.py`, add this helper above `build_source_catalog_from_backends`:

```python
def _unique_catalog_repos(
    repos: Iterable[RepoConfig],
) -> tuple[list[RepoConfig], dict[str, tuple[str, ...]]]:
    unique_repos: list[RepoConfig] = []
    aliases_by_source: dict[str, tuple[str, ...]] = {}
    seen_repos: dict[str, str] = {}
    seen_sources: set[str] = set()
    for repo in repos:
        source_key = repo_source_key(repo.url)
        existing_url = seen_repos.get(repo.id)
        if existing_url is not None:
            if existing_url == repo.url or repo_source_key(existing_url) == source_key:
                continue
            raise SvError(
                f"Configured source repo id {repo.id!r} is listed more than once with different URLs."
            )
        seen_repos[repo.id] = repo.url
        if source_key in seen_sources:
            aliases_by_source[source_key] = (
                *aliases_by_source[source_key],
                repo.id,
                *repo.aliases,
            )
            continue
        seen_sources.add(source_key)
        aliases_by_source[source_key] = repo.aliases
        unique_repos.append(repo)
    return unique_repos, aliases_by_source
```

Then replace the duplicated unique-repo block inside both `build_source_catalog` and `build_source_catalog_from_backends` with:

```python
    unique_repos, aliases_by_source = _unique_catalog_repos(repos)
```

Keep the existing loop bodies after `unique_repos` intact for `build_source_catalog`.

- [ ] **Step 5: Add one-repo worker helper**

Add this helper below `_unique_catalog_repos`:

```python
def _catalog_from_one_repo_backends(
    repo: RepoConfig,
    paths: SvPaths,
    repo_aliases: tuple[str, ...],
    backends: Sequence[SourceBackend],
) -> _RepoBackendCatalogResult:
    repo_path = paths.source_repo_for(repo.id)
    reject_symlinked_source_cache_path(repo_path, paths.sources_dir)
    warnings: list[str] = []
    if not backends:
        return _RepoBackendCatalogResult(
            repo_id=repo.id,
            entries=(),
            failures=(
                SourceBackendFailure(
                    repo_id=repo.id,
                    repo_url=repo.url,
                    backend="none",
                    operation="selecting source backend",
                    detail="no lightweight source backend was configured",
                ),
            ),
            refreshed=False,
        )

    failures: list[SourceBackendFailure] = []
    for backend in backends:
        try:
            backend_result = _catalog_entries_from_backend(
                repo,
                repo_path,
                repo_aliases,
                backend,
                warnings.append,
            )
        except SourceBackendError as exc:
            failures.append(
                SourceBackendFailure.from_error(
                    repo_id=repo.id,
                    repo_url=repo.url,
                    backend=backend.name,
                    error=exc,
                )
            )
            if exc.operation == "parsing .sv/index.toml":
                break
            continue
        return _RepoBackendCatalogResult(
            repo_id=repo.id,
            entries=tuple(backend_result.entries),
            failures=tuple(failures),
            refreshed=True,
            refreshed_backend=backend.name,
            index_hash=backend_result.index_hash,
            warnings=tuple(warnings),
        )

    return _RepoBackendCatalogResult(
        repo_id=repo.id,
        entries=(),
        failures=tuple(failures),
        refreshed=False,
        warnings=tuple(warnings),
    )
```

- [ ] **Step 6: Replace `build_source_catalog_from_backends` with ordered parallel merge**

Replace the body of `build_source_catalog_from_backends` with this implementation and add the optional keyword-only `jobs` parameter:

```python
def build_source_catalog_from_backends(
    repos: Iterable[RepoConfig],
    paths: SvPaths,
    backends_by_repo: Mapping[str, Sequence[SourceBackend]],
    warn: Callable[[str], None] | None = None,
    *,
    jobs: int | None = None,
) -> SourceCatalogResult:
    """Build a source catalog through lightweight backend interfaces.

    Repos are independent and may refresh concurrently. Backends for one repo are
    still attempted in the configured fallback order.
    """

    unique_repos, aliases_by_source = _unique_catalog_repos(repos)

    def worker(repo: RepoConfig) -> _RepoBackendCatalogResult:
        source_key = repo_source_key(repo.url)
        return _catalog_from_one_repo_backends(
            repo,
            paths,
            aliases_by_source[source_key],
            tuple(backends_by_repo.get(repo.id, ())),
        )

    repo_results = map_ordered(unique_repos, worker, jobs=jobs)

    entries: list[SourceSkill] = []
    failures: list[SourceBackendFailure] = []
    refreshed_repo_ids: list[str] = []
    refreshed_backends_by_repo: dict[str, str] = {}
    index_hashes_by_repo: dict[str, str | None] = {}

    for repo_result in repo_results:
        if warn is not None:
            for message in repo_result.warnings:
                warn(message)
        entries.extend(repo_result.entries)
        failures.extend(repo_result.failures)
        if repo_result.refreshed:
            refreshed_repo_ids.append(repo_result.repo_id)
            if repo_result.refreshed_backend is not None:
                refreshed_backends_by_repo[repo_result.repo_id] = repo_result.refreshed_backend
            index_hashes_by_repo[repo_result.repo_id] = repo_result.index_hash

    return SourceCatalogResult(
        entries=tuple(
            sorted(
                entries,
                key=lambda entry: (
                    entry.name,
                    entry.repo_id,
                    entry.source_relative_path,
                ),
            )
        ),
        failures=tuple(failures),
        refreshed_repo_ids=tuple(refreshed_repo_ids),
        refreshed_backends_by_repo=refreshed_backends_by_repo,
        index_hashes_by_repo=index_hashes_by_repo,
    )
```

- [ ] **Step 7: Run catalog tests**

Run:

```bash
uv run pytest tests/test_catalog.py -q --no-cov
```

Expected: all pass. Existing tests that assert sorted entries and backend fallback behavior must continue to pass.

- [ ] **Step 8: Commit Task 2**

```bash
git add src/sv/catalog.py tests/test_catalog.py
git commit -m "feat: refresh source catalogs in parallel"
```

---

### Task 3: Parallelize non-lightweight source repo preparation and surface `SV_JOBS` errors

**Files:**
- Modify: `src/sv/source.py`
- Modify: `src/sv/cli.py`
- Modify: `tests/test_source.py`
- Modify: `tests/test_cli_list_contracts.py`

- [ ] **Step 1: Add a test that `ensure_source_repos` overlaps independent repos**

Append this test to `tests/test_source.py` near `test_ensure_source_repos_uses_partial_sparse_for_each_configured_repo`:

```python
def test_ensure_source_repos_prepares_independent_repos_in_parallel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repos = [
        RepoConfig(id="Org/A", url="https://github.com/Org/A.git"),
        RepoConfig(id="Org/B", url="https://github.com/Org/B.git"),
    ]
    started: dict[str, threading.Event] = {
        "Org/A": threading.Event(),
        "Org/B": threading.Event(),
    }

    def fake_ensure_source_repo(repo_url, repo_path, runner, *, update, configured_skills_paths):
        repo_id = "Org/A" if repo_url.endswith("/A.git") else "Org/B"
        peer_id = "Org/B" if repo_id == "Org/A" else "Org/A"
        started[repo_id].set()
        assert started[peer_id].wait(2), "independent source repo preparation did not overlap"
        repo_path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(source_module, "ensure_source_repo", fake_ensure_source_repo)

    ensured = ensure_source_repos(repos, paths, runner=FakeRunner([]), update=True, jobs=2)

    assert ensured == [
        paths.source_repo_for("Org/A"),
        paths.source_repo_for("Org/B"),
    ]
```

Add this import near the top of `tests/test_source.py`:

```python
import threading
```

- [ ] **Step 2: Add a CLI test for invalid `SV_JOBS`**

Append this test to `tests/test_cli_list_contracts.py`:

```python
def test_source_command_reports_invalid_sv_jobs(tmp_path, run_sv, monkeypatch):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    monkeypatch.setenv("SV_JOBS", "many")

    result = run_sv(["list", "--refresh"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 1
    assert "SV_JOBS must be an integer between 1 and 64" in result.stderr
    assert "Traceback" not in result.stderr
```

Add these imports to `tests/test_cli_list_contracts.py` if they are not already present:

```python
from tests.helpers import configure_source, make_source_repo
```

- [ ] **Step 3: Run the new tests and verify they fail**

Run:

```bash
uv run pytest \
  tests/test_source.py::test_ensure_source_repos_prepares_independent_repos_in_parallel \
  tests/test_cli_list_contracts.py::test_source_command_reports_invalid_sv_jobs \
  -q --no-cov
```

Expected: `ensure_source_repos` does not accept `jobs`, and the CLI test fails until source refresh calls `configured_jobs` through the parallel helper.

- [ ] **Step 4: Parallelize `ensure_source_repos`**

In `src/sv/source.py`, import the helper:

```python
from sv.parallel import map_ordered
```

Change the signature of `ensure_source_repos` to include `jobs`:

```python
def ensure_source_repos(
    repos: Sequence[RepoConfig],
    paths: SvPaths,
    runner: Runner = default_runner,
    *,
    update: bool = True,
    jobs: int | None = None,
) -> list[Path]:
```

Replace the loop body with ordered parallel work:

```python
    def worker(repo: RepoConfig) -> Path:
        repo_path = paths.source_repo_for(repo.id)
        reject_symlinked_source_cache_path(repo_path, paths.sources_dir)
        ensure_source_repo(
            repo.url,
            repo_path,
            runner=runner,
            update=update,
            configured_skills_paths=repo.skills_paths,
        )
        return repo_path

    return map_ordered(list(repos), worker, jobs=jobs)
```

- [ ] **Step 5: Parallelize CLI non-lightweight refresh path**

In `src/sv/cli.py`, the helper `_ensure_source_repos_for_refresh` currently loops over repos. Replace that loop with `map_ordered` so failures still record source refresh failure per repo.

Add import:

```python
from sv.parallel import map_ordered
```

Replace `_ensure_source_repos_for_refresh` body after the function signature with:

```python
    def worker(repo: RepoConfig) -> None:
        repo_path = paths.source_repo_for(repo.id)
        reject_symlinked_source_cache_path(repo_path, paths.sources_dir)
        try:
            ensure_source_repo(
                repo.url,
                repo_path,
                runner=git_runner,
                update=update,
                configured_skills_paths=repo.skills_paths,
            )
        except SvError as exc:
            if record_global_source_state:
                _record_global_source_refresh_failure(
                    paths, (repo,), str(exc), started_at
                )
            raise

    map_ordered(list(repos), worker)
```

This keeps per-repo failure recording and lets `SV_JOBS` errors bubble through the existing `handle` exception block.

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run pytest \
  tests/test_source.py::test_ensure_source_repos_prepares_independent_repos_in_parallel \
  tests/test_source.py::test_ensure_source_repos_uses_partial_sparse_for_each_configured_repo \
  tests/test_cli_list_contracts.py::test_source_command_reports_invalid_sv_jobs \
  -q --no-cov
```

Expected: all pass. If `test_ensure_source_repos_uses_partial_sparse_for_each_configured_repo` asserts exact fake runner call order and fails under real parallelism, set `monkeypatch.setenv("SV_JOBS", "1")` inside that existing test because it is testing Git command construction, not parallel scheduling.

- [ ] **Step 7: Commit Task 3**

```bash
git add src/sv/source.py src/sv/cli.py tests/test_source.py tests/test_cli_list_contracts.py
git commit -m "feat: parallelize source repo preparation"
```

---

### Task 4: Add locks for mutable source and cache state

**Files:**
- Modify: `src/sv/source.py`
- Modify: `src/sv/source_cache.py`
- Modify: `src/sv/catalog.py`
- Modify: `tests/test_source.py`
- Modify: `tests/test_source_cache.py`

- [ ] **Step 1: Add a sparse backend serialization test**

Append this test to `tests/test_source.py` near the sparse backend tests:

```python
def test_sparse_backend_materialization_serializes_same_repo_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_path = tmp_path / "cache" / "repo"
    active = 0
    max_active = 0
    active_lock = threading.Lock()
    entered_once = threading.Event()
    release = threading.Event()

    def fake_prepare(*args, **kwargs):
        return None

    def fake_copy(self, source_path, destination):
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
            entered_once.set()
        assert release.wait(2), "test did not release materialization"
        with active_lock:
            active -= 1
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "SKILL.md").write_text("---\nname: alpha\ndescription: Alpha.\n---\n")

    monkeypatch.setattr(source_module, "_ensure_sparse_git_repo", fake_prepare)
    monkeypatch.setattr(source_module, "_remove_backend_cache_folder", lambda *args, **kwargs: None)
    monkeypatch.setattr(source_module.GitLocalSourceBackend, "materialize_folder", fake_copy)

    first = GitTreelessPartialBackend("https://example.com/repo.git", repo_path, runner=FakeRunner([]))
    second = GitTreelessPartialBackend("https://example.com/repo.git", repo_path, runner=FakeRunner([]))
    errors: list[BaseException] = []

    def run_backend(backend, destination):
        try:
            backend.materialize_folder("skills/alpha", destination)
        except BaseException as exc:  # noqa: BLE001 - test captures worker failures
            errors.append(exc)

    thread_a = threading.Thread(target=run_backend, args=(first, tmp_path / "a"))
    thread_b = threading.Thread(target=run_backend, args=(second, tmp_path / "b"))
    thread_a.start()
    assert entered_once.wait(2), "first materialization did not start"
    thread_b.start()
    release.set()
    thread_a.join(2)
    thread_b.join(2)

    assert errors == []
    assert max_active == 1
```

- [ ] **Step 2: Add a cached catalog writeback concurrency test**

Append this test to `tests/test_source_cache.py` near `record_cached_skill_body_hash` tests:

```python
def test_record_cached_skill_body_hash_preserves_parallel_updates(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    alpha = _source_skill(repo, paths, "alpha")
    beta = _source_skill(repo, paths, "beta")
    document = _catalog_document(
        "2026-05-18T12:00:00Z",
        entries=(
            CachedCatalogEntry("alpha", "Alpha skill.", "skills/alpha"),
            CachedCatalogEntry("beta", "Beta skill.", "skills/beta"),
        ),
    )
    save_cached_catalog(paths, repo, document)

    alpha_hash = "sha256:" + "a" * 64
    alpha_skill_file_hash = "sha256:" + "b" * 64
    beta_hash = "sha256:" + "c" * 64
    beta_skill_file_hash = "sha256:" + "d" * 64

    barrier = threading.Barrier(2)

    def record(entry: SourceSkill, content_hash: str, skill_file_hash: str) -> None:
        barrier.wait(2)
        record_cached_skill_body_hash(
            paths,
            repo,
            entry,
            content_hash=content_hash,
            skill_file_hash=skill_file_hash,
        )

    first = threading.Thread(target=record, args=(alpha, alpha_hash, alpha_skill_file_hash))
    second = threading.Thread(target=record, args=(beta, beta_hash, beta_skill_file_hash))
    first.start()
    second.start()
    first.join(2)
    second.join(2)

    updated = load_cached_catalog(paths, repo)
    assert updated is not None
    by_name = {entry.name: entry for entry in updated.entries}
    assert by_name["alpha"].content_hash == alpha_hash
    assert by_name["alpha"].skill_file_hash == alpha_skill_file_hash
    assert by_name["beta"].content_hash == beta_hash
    assert by_name["beta"].skill_file_hash == beta_skill_file_hash
```

Add these imports if missing:

```python
import threading
from sv.catalog import SourceSkill
from sv.source_cache import CachedCatalogEntry
```

If `_catalog_document` in `tests/test_source_cache.py` does not accept an `entries` keyword, extend that test helper so the default remains the current single `find-docs` entry and callers may pass a custom tuple of `CachedCatalogEntry` values.

- [ ] **Step 3: Run the new lock tests and verify failures**

Run:

```bash
uv run pytest \
  tests/test_source.py::test_sparse_backend_materialization_serializes_same_repo_cache \
  tests/test_source_cache.py::test_record_cached_skill_body_hash_preserves_parallel_updates \
  -q --no-cov
```

Expected: sparse materialization may overlap, and cached catalog updates may lose one write without a lock.

- [ ] **Step 4: Add source repo cache locks**

In `src/sv/source.py`, add imports:

```python
import threading
```

Add these module-level helpers near constants:

```python
_SOURCE_REPO_LOCKS_GUARD = threading.Lock()
_SOURCE_REPO_LOCKS: dict[Path, threading.RLock] = {}


def _source_repo_lock_key(repo_path: Path) -> Path:
    try:
        return repo_path.resolve()
    except OSError:
        return repo_path.absolute()


def _source_repo_lock(repo_path: Path) -> threading.RLock:
    key = _source_repo_lock_key(repo_path)
    with _SOURCE_REPO_LOCKS_GUARD:
        lock = _SOURCE_REPO_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _SOURCE_REPO_LOCKS[key] = lock
        return lock
```

Wrap `GitSparseSourceBackend.materialize_folder` with this lock. The resulting function shape must be:

```python
    def materialize_folder(self, source_path: str, destination: Path) -> None:
        with _source_repo_lock(self.repo_path):
            self._materialize_folder_locked(source_path, destination)

    def _materialize_folder_locked(self, source_path: str, destination: Path) -> None:
        operation = "materializing selected folder"
        normalized_source_path = _normalize_backend_relative_path(source_path)
        self._prepare_checkout([_sparse_folder_pattern(normalized_source_path)], operation)
        copied = False
        try:
            GitLocalSourceBackend(self.repo_path).materialize_folder(
                normalized_source_path, destination
            )
            copied = True
        finally:
            cleanup_errors: list[str] = []
            try:
                _remove_backend_cache_folder(self.repo_path, normalized_source_path)
            except SvError as exc:
                cleanup_errors.append(str(exc))
            try:
                self._prepare_checkout(
                    _metadata_sparse_patterns(self._configured_skills_paths), operation
                )
            except SourceBackendError as exc:
                cleanup_errors.append(exc.detail)
            if copied and cleanup_errors:
                raise SourceBackendError(
                    operation,
                    "failed to restore metadata-only source cache after selected "
                    f"folder materialization: {'; '.join(cleanup_errors)}",
                )
```

Use an `RLock` because `_materialize_folder_locked` calls `_prepare_checkout`, and a following step also locks checkout preparation.

- [ ] **Step 5: Lock sparse checkout preparation**

In `src/sv/source.py`, wrap `_prepare_checkout` with the same repo lock so metadata refresh for a repo does not interleave sparse checkout commands with another materialization:

```python
    def _prepare_checkout(self, patterns: Sequence[str], operation: str) -> None:
        with _source_repo_lock(self.repo_path):
            self._prepare_checkout_locked(patterns, operation)

    def _prepare_checkout_locked(self, patterns: Sequence[str], operation: str) -> None:
        repo_existed_before = self.repo_path.exists() or self.repo_path.is_symlink()
        try:
            _ensure_sparse_git_repo(
                self.repo_url,
                self.repo_path,
                self._runner,
                filter_spec=self.filter_spec,
                sparse_patterns=patterns,
                update=self._update,
            )
        except SvError as exc:
            if not repo_existed_before:
                _remove_failed_lightweight_checkout(self.repo_path)
            raise SourceBackendError(
                operation,
                str(exc),
                hint=(
                    "sv did not run a full clone. Fix Git/auth/filter support or try "
                    "another configured source backend."
                ),
            ) from exc
```

- [ ] **Step 6: Add cache write lock**

In `src/sv/source_cache.py`, import `threading` and add this near constants:

```python
import threading

_CACHE_WRITE_LOCK = threading.RLock()
```

Wrap the bodies of these functions with `with _CACHE_WRITE_LOCK:`:

```python
def store_skill_body_cache(...):
    with _CACHE_WRITE_LOCK:
        return _store_skill_body_cache_locked(...)


def record_cached_skill_body_hash(...):
    with _CACHE_WRITE_LOCK:
        return _record_cached_skill_body_hash_locked(...)
```

Use a private locked helper for each function. Move the existing function body into the private helper without changing behavior. This keeps call sites unchanged and prevents lost update/write/prune races.

Also wrap `clean_cache` if it deletes skill-body cache entries and wrap `_prune_after_body_cache_write` if it is not already called inside the `store_skill_body_cache` lock.

- [ ] **Step 7: Attach materializer locks for in-memory backend instances**

In `src/sv/catalog.py`, create one `threading.RLock()` per `_catalog_entries_from_backend` call and pass it to entry materializers.

At the top of `_catalog_entries_from_backend`, after index handling begins, use:

```python
    materialize_lock = threading.RLock()
```

Change both `_catalog_entries_from_index` and non-index entry creation to pass that lock:

```python
                _materializer=_backend_materializer(
                    backend, source_relative_path, materialize_lock
                ),
```

Change `_backend_materializer` to:

```python
def _backend_materializer(
    backend: SourceBackend,
    source_relative_path: str,
    lock: threading.RLock | None = None,
) -> Callable[[Path], None]:
    def materialize(destination: Path) -> None:
        if lock is None:
            backend.materialize_folder(source_relative_path, destination)
            return
        with lock:
            backend.materialize_folder(source_relative_path, destination)

    return materialize
```

- [ ] **Step 8: Run lock tests and related suites**

Run:

```bash
uv run pytest \
  tests/test_source.py::test_sparse_backend_materialization_serializes_same_repo_cache \
  tests/test_source_cache.py::test_record_cached_skill_body_hash_preserves_parallel_updates \
  tests/test_catalog.py \
  tests/test_source.py \
  tests/test_source_cache.py \
  -q --no-cov
```

Expected: all pass.

- [ ] **Step 9: Commit Task 4**

```bash
git add src/sv/source.py src/sv/source_cache.py src/sv/catalog.py tests/test_source.py tests/test_source_cache.py
git commit -m "fix: serialize shared source and cache mutations"
```

---

### Task 5: Parallelize `add --all` materialization with serial commits

**Files:**
- Modify: `src/sv/project.py`
- Modify: `src/sv/cli.py`
- Modify: `tests/test_project.py`
- Modify: `tests/test_cli_add_contracts.py`

- [ ] **Step 1: Add a project-level overlap test for `add_all_project_skills`**

Append this test double and test to `tests/test_project.py`:

```python
class BlockingProjectSourceSkill:
    description = "Blocking skill."
    repo_id = "Org/Skills"
    repo_url = "https://github.com/Org/Skills.git"
    repo_aliases: tuple[str, ...] = ()
    source_backend = "blocking"

    def __init__(self, name: str, source_root: Path, started: threading.Event, peer_started: threading.Event):
        self.name = name
        self.source_path = source_root / "skills" / name
        self.source_relative_path = f"skills/{name}"
        self.started = started
        self.peer_started = peer_started
        self.source_path.mkdir(parents=True)
        (self.source_path / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            "description: Blocking skill.\n"
            "---\n"
        )

    def materialize_to(self, destination: Path) -> None:
        self.started.set()
        assert self.peer_started.wait(2), "add-all materialization did not overlap"
        shutil.copytree(self.source_path, destination)


def test_add_all_project_skills_prepares_new_skills_in_parallel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SV_JOBS", "2")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    source_root = tmp_path / "source"
    alpha_started = threading.Event()
    beta_started = threading.Event()
    catalog = [
        BlockingProjectSourceSkill("alpha", source_root, alpha_started, beta_started),
        BlockingProjectSourceSkill("beta", source_root, beta_started, alpha_started),
    ]

    result = add_all_project_skills(catalog, project_skills)

    assert [(item.skill, item.status) for item in result.results] == [
        ("alpha", "added"),
        ("beta", "added"),
    ]
    assert sorted(path.name for path in project_skills.iterdir() if not path.name.startswith(".")) == [
        "alpha",
        "beta",
    ]
    assert sorted(load_manifest(project_skills)) == ["alpha", "beta"]
```

Add imports if missing:

```python
import threading
from sv.project import add_all_project_skills
```

- [ ] **Step 2: Add a no-final-target-on-prepare-failure test**

Append this test to `tests/test_project.py`:

```python
class FailingPrepareProjectSourceSkill(BlockingProjectSourceSkill):
    def materialize_to(self, destination: Path) -> None:
        raise SvError("simulated materialization failure")


def test_add_all_project_skills_cleans_prepared_temps_when_one_prepare_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SV_JOBS", "2")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    source_root = tmp_path / "source"
    alpha_started = threading.Event()
    beta_started = threading.Event()
    alpha = BlockingProjectSourceSkill("alpha", source_root, alpha_started, beta_started)
    beta = FailingPrepareProjectSourceSkill("beta", source_root, beta_started, alpha_started)

    with pytest.raises(SvError, match="Failed to add Pi skill 'beta'"):
        add_all_project_skills([alpha, beta], project_skills)

    assert not (project_skills / "alpha").exists()
    assert not (project_skills / "beta").exists()
    assert not list(project_skills.glob(".*.sv-add-tmp"))
```

This locks in the all-prepare-before-commit safety guarantee for bulk adds.

- [ ] **Step 3: Run the new tests and verify they fail**

Run:

```bash
uv run pytest \
  tests/test_project.py::test_add_all_project_skills_prepares_new_skills_in_parallel \
  tests/test_project.py::test_add_all_project_skills_cleans_prepared_temps_when_one_prepare_fails \
  -q --no-cov
```

Expected: first test fails because add-all is serial; second may fail because sequential add can commit `alpha` before `beta` fails.

- [ ] **Step 4: Add add-all plan dataclasses**

In `src/sv/project.py`, import the helper:

```python
from sv.parallel import map_ordered
```

Add these dataclasses after `_MaterializedSkillMetadata`:

```python
@dataclass(frozen=True)
class _AddPlan:
    entry: ProjectSourceSkill
    skill_name: str
    target: Path
    result: AddSkillResult | None = None


@dataclass(frozen=True)
class _PreparedAdd:
    plan: _AddPlan
    metadata: _MaterializedSkillMetadata
```

- [ ] **Step 5: Add planning helper for bulk adds**

Add this helper near `add_all_project_skills`:

```python
def _plan_add_all_skills(
    catalog: Sequence[ProjectSourceSkill],
    project_skills_dir: Path,
    target_style: _TargetStyle,
    *,
    replace_existing: bool,
) -> tuple[list[_AddPlan], list[_AddPlan]]:
    manifest = _load_manifest_for_target(project_skills_dir, target_style)
    existing_or_skipped: list[_AddPlan] = []
    to_prepare: list[_AddPlan] = []
    for entry in catalog:
        skill_name = normalize_skill_name(entry.name)
        if entry.source_backend == "local-cache" and not entry.source_path.is_dir():
            raise SvError(f"Skill '{skill_name}' was not found in source skills directory.")
        target = project_skills_dir / skill_name
        _reject_symlinked_project_skill(target, target_style)
        if target.exists() or target.is_symlink():
            _reject_symlinked_project_skill(target, target_style)
            if not target.is_dir():
                raise SvError(
                    f"Cannot add {target_style.skill_label} '{skill_name}': non-directory path already exists at {target}."
                )
            existing_entry = manifest.get(skill_name)
            if not replace_existing:
                existing_or_skipped.append(
                    _AddPlan(
                        entry=entry,
                        skill_name=skill_name,
                        target=target,
                        result=AddSkillResult(
                            skill=skill_name,
                            target=target,
                            status="exists",
                            repo_id=entry.repo_id,
                            existing_repo_id=(
                                existing_entry.repo_id if existing_entry is not None else None
                            ),
                            source_reference=_source_reference_for(entry),
                            existing_source_reference=(
                                _source_reference_for_manifest(existing_entry)
                                if existing_entry is not None
                                else None
                            ),
                            target_kind=target_style.target_kind,
                        ),
                    )
                )
                continue
        to_prepare.append(_AddPlan(entry=entry, skill_name=skill_name, target=target))
    return existing_or_skipped, to_prepare
```

- [ ] **Step 6: Add parallel prepare and serial commit helper**

Add these helpers near `_materialize_entry_for_add`:

```python
def _prepare_add_plan(plan: _AddPlan, target_style: _TargetStyle) -> _PreparedAdd:
    metadata = _materialize_entry_for_add(plan.entry, plan.target, target_style)
    return _PreparedAdd(plan=plan, metadata=metadata)


def _cleanup_prepared_adds(prepared: Sequence[_PreparedAdd]) -> None:
    for item in prepared:
        remove_materialization_path(_add_temp_target(item.plan.target), ignore_errors=True)
```

Add this bulk helper near `add_all_project_skills`:

```python
def _add_all_skills_parallel(
    catalog: Sequence[ProjectSourceSkill],
    project_skills_dir: Path,
    target_style: _TargetStyle,
    *,
    replace_existing: bool = False,
) -> AddAllSkillsResult:
    _ensure_safe_project_skills_dir(project_skills_dir, target_style)
    try:
        project_skills_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SvError(
            f"Failed to prepare {target_style.skills_dir_label} {project_skills_dir}: {exc}"
        ) from exc

    existing_or_skipped, to_prepare = _plan_add_all_skills(
        catalog,
        project_skills_dir,
        target_style,
        replace_existing=replace_existing,
    )

    prepared: list[_PreparedAdd] = []
    try:
        prepared = map_ordered(
            to_prepare,
            lambda plan: _prepare_add_plan(plan, target_style),
        )
    except Exception:
        _cleanup_prepared_adds(prepared)
        for plan in to_prepare:
            remove_materialization_path(_add_temp_target(plan.target), ignore_errors=True)
        raise

    results_by_skill: dict[str, AddSkillResult] = {
        plan.skill_name: plan.result
        for plan in existing_or_skipped
        if plan.result is not None
    }

    try:
        for item in prepared:
            plan = item.plan
            manifest_entry = _manifest_entry_for(
                plan.entry,
                target_style,
                metadata=item.metadata,
            )

            def update_manifest(
                project_skills_dir: Path = project_skills_dir,
                manifest_entry: ManifestEntry = manifest_entry,
                target_style: _TargetStyle = target_style,
            ) -> None:
                _upsert_manifest_entry_for_target(
                    project_skills_dir,
                    manifest_entry,
                    target_style,
                )

            if plan.target.exists() and replace_existing:
                _replace_with_materialized_entry(
                    plan.target,
                    after_replace=update_manifest,
                    target_style=target_style,
                )
            else:
                install_materialized_skill_folder(
                    _add_temp_target(plan.target),
                    plan.target,
                    error_message=f"Failed to add {target_style.skill_label} '{plan.skill_name}'",
                    after_install=update_manifest,
                )
            results_by_skill[plan.skill_name] = AddSkillResult(
                skill=plan.skill_name,
                target=plan.target,
                status="added",
                repo_id=plan.entry.repo_id,
                source_reference=_source_reference_for(plan.entry),
                target_kind=target_style.target_kind,
            )
    except Exception:
        for item in prepared:
            remove_materialization_path(_add_temp_target(item.plan.target), ignore_errors=True)
        raise

    ordered_results = [
        results_by_skill[normalize_skill_name(entry.name)]
        for entry in catalog
        if normalize_skill_name(entry.name) in results_by_skill
    ]
    return AddAllSkillsResult(results=ordered_results)
```

If `ManifestEntry` is not imported in `project.py` scope, it already is imported near the top from `sv.manifest`; use that existing import.

- [ ] **Step 7: Route project and vault add-all through the parallel helper**

Replace `add_all_project_skills` with:

```python
def add_all_project_skills(
    catalog: Sequence[ProjectSourceSkill], project_skills_dir: Path
) -> AddAllSkillsResult:
    return _add_all_skills_parallel(
        catalog,
        project_skills_dir,
        _PI_TARGET,
        replace_existing=False,
    )
```

Replace `add_all_vault_skills` with:

```python
def add_all_vault_skills(
    catalog: Sequence[ProjectSourceSkill],
    vault_skills_dir: Path,
    *,
    replace_existing: bool = False,
) -> AddAllSkillsResult:
    return _add_all_skills_parallel(
        catalog,
        vault_skills_dir,
        _VAULT_TARGET,
        replace_existing=replace_existing,
    )
```

- [ ] **Step 8: Use `add_all_vault_skills` from CLI after replacement decisions**

In `src/sv/cli.py`, import `add_all_vault_skills` from `sv.project`.

Replace the skill-vault branch of `_add_all_skills_to_context` with serial replacement resolution followed by one bulk call:

```python
    if context.is_skill_vault:
        resolved_entries: list[SourceSkill] = []
        for entry in catalog:
            should_replace = _resolve_vault_replacement(
                entry,
                context.vault_skills_dir,
                replace_existing=replace_existing,
            )
            if should_replace or not (context.vault_skills_dir / entry.name).exists():
                resolved_entries.append(entry)
            else:
                resolved_entries.append(entry)
        return add_all_vault_skills(
            resolved_entries,
            context.vault_skills_dir,
            replace_existing=replace_existing,
        )
```

Keep prompts serial. Do not prompt from workers.

- [ ] **Step 9: Run add tests**

Run:

```bash
uv run pytest \
  tests/test_project.py::test_add_all_project_skills_prepares_new_skills_in_parallel \
  tests/test_project.py::test_add_all_project_skills_cleans_prepared_temps_when_one_prepare_fails \
  tests/test_project.py \
  tests/test_cli_add_contracts.py \
  -q --no-cov
```

Expected: all pass. If any CLI add-all output ordering test fails, keep printing in the existing `result.results` order and fix only the result ordering in `_add_all_skills_parallel`.

- [ ] **Step 10: Commit Task 5**

```bash
git add src/sv/project.py src/sv/cli.py tests/test_project.py tests/test_cli_add_contracts.py
git commit -m "feat: prepare bulk adds in parallel"
```

---

### Task 6: Parallelize sync/update replacement preparation with serial commits

**Files:**
- Modify: `src/sv/project.py`
- Modify: `tests/test_project.py`
- Modify: `tests/test_cli_sync_contracts.py`
- Modify: `tests/test_cli_update_contracts.py`

- [ ] **Step 1: Add sync overlap test**

Append this test to `tests/test_project.py`:

```python
def test_sync_project_skills_prepares_replacements_in_parallel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SV_JOBS", "2")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    source_root = tmp_path / "source"
    project_skills.mkdir(parents=True)

    alpha_started = threading.Event()
    beta_started = threading.Event()
    alpha = BlockingProjectSourceSkill("alpha", source_root, alpha_started, beta_started)
    beta = BlockingProjectSourceSkill("beta", source_root, beta_started, alpha_started)

    add_all_project_skills([alpha, beta], project_skills)
    alpha.started = threading.Event()
    beta.started = threading.Event()
    alpha.peer_started = beta.started
    beta.peer_started = alpha.started
    (alpha.source_path / "notes.md").write_text("alpha v2\n")
    (beta.source_path / "notes.md").write_text("beta v2\n")

    result = sync_project_skills([alpha, beta], project_skills)

    assert result.updated == ["alpha", "beta"]
    assert result.skipped == []
    assert (project_skills / "alpha" / "notes.md").read_text() == "alpha v2\n"
    assert (project_skills / "beta" / "notes.md").read_text() == "beta v2\n"
```

- [ ] **Step 2: Add update overlap test**

Append this test to `tests/test_project.py`:

```python
def test_update_project_skills_prepares_changed_replacements_in_parallel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SV_JOBS", "2")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    source_root = tmp_path / "source"
    project_skills.mkdir(parents=True)

    alpha_started = threading.Event()
    beta_started = threading.Event()
    alpha = BlockingProjectSourceSkill("alpha", source_root, alpha_started, beta_started)
    beta = BlockingProjectSourceSkill("beta", source_root, beta_started, alpha_started)

    add_all_project_skills([alpha, beta], project_skills)
    alpha.started = threading.Event()
    beta.started = threading.Event()
    alpha.peer_started = beta.started
    beta.peer_started = alpha.started
    (alpha.source_path / "notes.md").write_text("alpha update\n")
    (beta.source_path / "notes.md").write_text("beta update\n")

    result = update_project_skills([alpha, beta], project_skills)

    assert result.updated == ["alpha", "beta"]
    assert [skip.reason for skip in result.skipped] == []
    assert (project_skills / "alpha" / "notes.md").read_text() == "alpha update\n"
    assert (project_skills / "beta" / "notes.md").read_text() == "beta update\n"
```

Ensure these functions are imported in `tests/test_project.py`:

```python
from sv.project import sync_project_skills, update_project_skills
```

- [ ] **Step 3: Run new tests and verify they fail**

Run:

```bash
uv run pytest \
  tests/test_project.py::test_sync_project_skills_prepares_replacements_in_parallel \
  tests/test_project.py::test_update_project_skills_prepares_changed_replacements_in_parallel \
  -q --no-cov
```

Expected: failure because replacement preparation is serial.

- [ ] **Step 4: Add replacement plan dataclasses**

In `src/sv/project.py`, add these dataclasses after `_PreparedAdd`:

```python
@dataclass(frozen=True)
class _ReplacementPlan:
    entry: ProjectSourceSkill
    target: Path
    project_skills_dir: Path
    target_style: _TargetStyle


@dataclass(frozen=True)
class _PreparedReplacement:
    plan: _ReplacementPlan
    metadata: _MaterializedSkillMetadata
```

- [ ] **Step 5: Add replacement prepare/commit helpers**

Add these helpers near `_replace_tree_and_update_manifest`:

```python
def _prepare_replacement_plan(plan: _ReplacementPlan) -> _PreparedReplacement:
    metadata = _materialize_entry_for_replace(
        plan.entry,
        plan.target,
        plan.target_style,
    )
    return _PreparedReplacement(plan=plan, metadata=metadata)


def _commit_prepared_replacement(prepared: _PreparedReplacement) -> None:
    plan = prepared.plan
    manifest_entry = _manifest_entry_for(
        plan.entry,
        plan.target_style,
        metadata=prepared.metadata,
    )

    def update_manifest() -> None:
        _upsert_manifest_entry_for_target(
            plan.project_skills_dir,
            manifest_entry,
            plan.target_style,
        )

    _replace_with_materialized_entry(
        plan.target,
        after_replace=update_manifest,
        target_style=plan.target_style,
    )


def _prepare_replacements_ordered(
    plans: Sequence[_ReplacementPlan],
) -> list[_PreparedReplacement]:
    prepared: list[_PreparedReplacement] = []
    try:
        prepared = map_ordered(plans, _prepare_replacement_plan)
        return prepared
    except Exception:
        for plan in plans:
            remove_materialization_path(_sync_temp_target(plan.target), ignore_errors=True)
        raise
```

- [ ] **Step 6: Update `_sync_skills` to collect replacement plans**

In `_sync_skills`, keep all existing skip/source-missing logic. Change only the branch that currently calls `_replace_tree_and_update_manifest(...)`.

Before the local skills loop, add:

```python
    replacement_plans: list[_ReplacementPlan] = []
```

Replace:

```python
            _replace_tree_and_update_manifest(
                entry, local_skill, project_skills_dir, target_style
            )
            updated.append(local_skill.name)
            continue
```

with:

```python
            replacement_plans.append(
                _ReplacementPlan(
                    entry=entry,
                    target=local_skill,
                    project_skills_dir=project_skills_dir,
                    target_style=target_style,
                )
            )
            updated.append(local_skill.name)
            continue
```

After the local skills loop and before returning `SyncResult`, add:

```python
    prepared_replacements = _prepare_replacements_ordered(replacement_plans)
    for prepared in prepared_replacements:
        _commit_prepared_replacement(prepared)
```

This preserves the `updated` list order because it is still appended during deterministic local skill iteration.

- [ ] **Step 7: Update `_update_skills` changed-source branches**

In `_update_skills`, add before the manifest loop:

```python
    replacement_plans: list[_ReplacementPlan] = []
```

For the branch:

```python
        if source_hash is not None:
            _replace_tree_and_update_manifest(
                source_entry, target, project_skills_dir, target_style
            )
            updated.append(skill_name)
            continue
```

replace it with:

```python
        if source_hash is not None:
            replacement_plans.append(
                _ReplacementPlan(
                    entry=source_entry,
                    target=target,
                    project_skills_dir=project_skills_dir,
                    target_style=target_style,
                )
            )
            updated.append(skill_name)
            continue
```

For the final branch that materializes and commits immediately, replace from:

```python
        metadata = _materialize_entry_for_replace(source_entry, target, target_style)
```

through the `_replace_with_materialized_entry(...)` call with this two-phase code:

```python
        replacement_plans.append(
            _ReplacementPlan(
                entry=source_entry,
                target=target,
                project_skills_dir=project_skills_dir,
                target_style=target_style,
            )
        )
        updated.append(skill_name)
```

After the manifest loop and before returning `SyncResult`, add:

```python
    prepared_replacements = _prepare_replacements_ordered(replacement_plans)
    for prepared in prepared_replacements:
        _commit_prepared_replacement(prepared)
```

This commits every prepared replacement through the same manifest path and avoids stale metadata variables from the old inline branch.

- [ ] **Step 8: Keep compare-only materialization behavior correct**

The branches in `_update_skills` that materialize only to discover `source_hash` and then discard unchanged temps must remain serial until they are refactored into a distinct compare plan. Keep these existing blocks unchanged:

```python
metadata = _materialize_entry_for_replace(source_entry, target, target_style)
remove_materialization_path(_sync_temp_target(target), ignore_errors=True)
```

This avoids changing unchanged-source behavior while still parallelizing actual replacements. A separate compare-plan refactor can be done after this plan if profiling shows it matters.

- [ ] **Step 9: Run sync/update tests**

Run:

```bash
uv run pytest \
  tests/test_project.py::test_sync_project_skills_prepares_replacements_in_parallel \
  tests/test_project.py::test_update_project_skills_prepares_changed_replacements_in_parallel \
  tests/test_project.py \
  tests/test_cli_sync_contracts.py \
  tests/test_cli_update_contracts.py \
  -q --no-cov
```

Expected: all pass. If a test expects a target to update before a subsequent source failure, update the test expectation only if the failure occurs during the new prepare phase; the new behavior intentionally avoids final target mutation until all replacement preparations succeed.

- [ ] **Step 10: Commit Task 6**

```bash
git add src/sv/project.py tests/test_project.py tests/test_cli_sync_contracts.py tests/test_cli_update_contracts.py
git commit -m "feat: prepare sync and update replacements in parallel"
```

---

### Task 7: Parallelize read-only status and index hashing

**Files:**
- Modify: `src/sv/project.py`
- Modify: `src/sv/index.py`
- Modify: `tests/test_project.py`
- Modify: `tests/test_index.py`
- Modify: `tests/test_cli_status_contracts.py`

- [ ] **Step 1: Add local status hash overlap test**

Append this test to `tests/test_project.py`:

```python
def test_refresh_project_skill_local_states_hashes_skills_in_parallel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SV_JOBS", "2")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    source_root = tmp_path / "source"
    project_skills.mkdir(parents=True)
    alpha = BlockingProjectSourceSkill("alpha", source_root, threading.Event(), threading.Event())
    beta = BlockingProjectSourceSkill("beta", source_root, threading.Event(), threading.Event())
    add_all_project_skills([alpha, beta], project_skills)

    alpha_started = threading.Event()
    beta_started = threading.Event()
    original_hash = hashing_module.sha256_skill_directory

    def blocking_hash(path: Path, *, expected_name=None):
        if path.name == "alpha":
            alpha_started.set()
            assert beta_started.wait(2), "local state hashing did not overlap"
        if path.name == "beta":
            beta_started.set()
            assert alpha_started.wait(2), "local state hashing did not overlap"
        return original_hash(path, expected_name=expected_name)

    monkeypatch.setattr(hashing_module, "sha256_skill_directory", blocking_hash)

    refresh_project_skill_local_states(project_skills)

    manifest = load_manifest(project_skills)
    assert manifest["alpha"].local_content_hash is not None
    assert manifest["beta"].local_content_hash is not None
```

Add imports if missing:

```python
import sv.hashing as hashing_module
from sv.project import refresh_project_skill_local_states
```

- [ ] **Step 2: Add index scan hash overlap test**

Append this test to `tests/test_index.py`:

```python
def test_scan_repo_for_index_hashes_candidate_skills_in_parallel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SV_JOBS", "2")
    write_skill(tmp_path / "skills" / "alpha", name="alpha", description="Alpha skill.")
    write_skill(tmp_path / "skills" / "beta", name="beta", description="Beta skill.")
    alpha_started = threading.Event()
    beta_started = threading.Event()
    original_hash = index_module.sha256_skill_directory

    def blocking_hash(path: Path, *, expected_name=None):
        if path.name == "alpha":
            alpha_started.set()
            assert beta_started.wait(2), "index skill hashing did not overlap"
        if path.name == "beta":
            beta_started.set()
            assert alpha_started.wait(2), "index skill hashing did not overlap"
        return original_hash(path, expected_name=expected_name)

    monkeypatch.setattr(index_module, "sha256_skill_directory", blocking_hash)

    document = scan_repo_for_index(tmp_path, generated_at="2026-05-18T12:00:00Z")

    assert [(entry.name, entry.source_path) for entry in document.skills] == [
        ("alpha", "skills/alpha"),
        ("beta", "skills/beta"),
    ]
```

Use the existing helper names in `tests/test_index.py`. If the file uses `make_skill` instead of `write_skill`, call the existing helper and keep the same assertions. Add imports:

```python
import threading
import sv.index as index_module
```

- [ ] **Step 3: Run new tests and verify they fail**

Run:

```bash
uv run pytest \
  tests/test_project.py::test_refresh_project_skill_local_states_hashes_skills_in_parallel \
  tests/test_index.py::test_scan_repo_for_index_hashes_candidate_skills_in_parallel \
  -q --no-cov
```

Expected: both fail because hashing is serial.

- [ ] **Step 4: Parallelize local manifest state hashing**

In `src/sv/project.py`, add this dataclass after replacement dataclasses:

```python
@dataclass(frozen=True)
class _LocalStateRefresh:
    key: str
    entry: ManifestEntry
    refreshed: ManifestEntry
```

Replace `_refresh_local_skill_states` loop with worker-based collection:

```python
    refresh_items: list[tuple[str, ManifestEntry]] = []
    for key, manifest_entry in entries.items():
        if not _manifest_entry_matches_target(manifest_entry, target_style):
            continue
        _validate_manifest_entry_skill_name(manifest_entry, target_style)
        target = project_skills_dir / manifest_entry.name
        _reject_symlinked_project_skill(target, target_style)
        _validate_project_skill_dir_name(target, target_style)
        if target.is_dir():
            refresh_items.append((key, manifest_entry))

    def worker(item: tuple[str, ManifestEntry]) -> _LocalStateRefresh:
        key, manifest_entry = item
        target = project_skills_dir / manifest_entry.name
        local_content_hash = sha256_skill_directory(
            target, expected_name=manifest_entry.name
        )
        modified = (
            manifest_entry.installed_content_hash is not None
            and local_content_hash != manifest_entry.installed_content_hash
        )
        return _LocalStateRefresh(
            key=key,
            entry=manifest_entry,
            refreshed=replace(
                manifest_entry,
                local_content_hash=local_content_hash,
                modified=modified,
            ),
        )

    for result in map_ordered(refresh_items, worker):
        if result.refreshed != result.entry:
            updated_entries[result.key] = result.refreshed
            changed = True
```

Keep the final `if changed: save_manifest(...)` exactly once after the loop.

Apply the same pattern to `_refresh_skill_states`: build read-only work items, compute `_refreshed_manifest_entry_state(...)` in workers, and save once in input order.

- [ ] **Step 5: Parallelize index per-skill parse/hash**

In `src/sv/index.py`, import `map_ordered`:

```python
from sv.parallel import map_ordered
```

Add dataclass near `IndexScanConfig`:

```python
@dataclass(frozen=True)
class _ScannedSkill:
    entry: IndexSkillEntry | None
    warning: str | None = None
```

Add helper near `scan_repo_for_index`:

```python
def _scan_one_skill_for_index(skill_file: Path, root: Path) -> _ScannedSkill:
    skill_dir = skill_file.parent
    try:
        metadata = parse_skill_file(skill_file, expected_folder=skill_dir.name)
        return _ScannedSkill(
            entry=IndexSkillEntry(
                name=metadata.name,
                description=metadata.description,
                source_path=_repo_relative_path(skill_dir, root),
                content_hash=sha256_skill_directory(skill_dir),
                skill_file_hash=sha256_file(skill_file),
            )
        )
    except InvalidSkillError as exc:
        return _ScannedSkill(
            entry=None,
            warning=(
                "warning: skipping invalid skill at "
                f"{_escape_control_characters(str(skill_dir))}: "
                f"{_escape_control_characters(str(exc))}"
            ),
        )
    except SvError as exc:
        if _is_filesystem_error(exc):
            raise
        return _ScannedSkill(
            entry=None,
            warning=(
                "warning: skipping invalid skill at "
                f"{_escape_control_characters(str(skill_dir))}: "
                f"{_escape_control_characters(str(exc))}"
            ),
        )
```

Replace the per-candidate loop in `scan_repo_for_index` with:

```python
    scanned = map_ordered(
        _iter_candidate_skill_files(root, includes, excludes, warn=warn),
        lambda skill_file: _scan_one_skill_for_index(skill_file, root),
    )
    entries: list[IndexSkillEntry] = []
    for result in scanned:
        if result.warning is not None and warn is not None:
            warn(result.warning)
        if result.entry is not None:
            entries.append(result.entry)
```

Keep the final `IndexDocument(...)` construction unchanged.

- [ ] **Step 6: Run read-only tests**

Run:

```bash
uv run pytest \
  tests/test_project.py::test_refresh_project_skill_local_states_hashes_skills_in_parallel \
  tests/test_index.py::test_scan_repo_for_index_hashes_candidate_skills_in_parallel \
  tests/test_project.py \
  tests/test_index.py \
  tests/test_cli_status_contracts.py \
  -q --no-cov
```

Expected: all pass. Invalid skill warnings from `sv index` must remain in candidate path order.

- [ ] **Step 7: Commit Task 7**

```bash
git add src/sv/project.py src/sv/index.py tests/test_project.py tests/test_index.py tests/test_cli_status_contracts.py
git commit -m "feat: parallelize skill hashing workflows"
```

---

### Task 8: Document parallelism and operational controls

**Files:**
- Modify: `README.md`
- Modify: `docs/commands.md`
- Modify: `docs/usage.md`
- Modify: `docs/testing.md`
- Modify: `docs/troubleshooting.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Add docs contract test for `SV_JOBS`**

Append this test to `tests/test_docs.py`:

```python
def test_docs_describe_sv_jobs_parallelism_control() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in _documented_paths())

    assert "SV_JOBS" in combined
    assert "SV_JOBS=1" in combined
    assert "parallel" in combined.lower()
    assert "64" in combined
```

- [ ] **Step 2: Run docs test and verify it fails**

Run:

```bash
uv run pytest tests/test_docs.py::test_docs_describe_sv_jobs_parallelism_control -q --no-cov
```

Expected: failure because docs do not mention `SV_JOBS`.

- [ ] **Step 3: Update README**

Add this paragraph to `README.md` after the global cache description:

```markdown
## Parallel work

`sv` runs independent source refreshes, bulk skill materialization preparation, and read-only skill hashing in parallel by default. Visible output, manifest writes, final skill-folder replacement, and cache metadata writes remain deterministic and serialized. Set `SV_JOBS=1` to disable worker threads for debugging, or set `SV_JOBS=N` to choose a worker count from 1 through 64. The default is bounded to at most 8 workers.
```

- [ ] **Step 4: Update command reference**

Add this section to `docs/commands.md` after the Global cache section:

```markdown
## Parallelism

Source-reading commands can refresh independent configured repos in parallel. Bulk `add --all`, `sync`, and `update` prepare independent skill trees in parallel, then commit final filesystem and manifest changes in stable order. `status` and `index` can hash independent skill folders in parallel.

Use `SV_JOBS=1` when you want fully sequential execution for debugging or reproducing a race. Use `SV_JOBS=N` with `N` from 1 through 64 to choose a specific worker count. Invalid values fail before command work starts with a clear error.
```

- [ ] **Step 5: Update usage and troubleshooting docs**

Add this paragraph to `docs/usage.md` near source refresh/add/sync guidance:

```markdown
`sv` parallelizes independent work by default, so multiple configured source repos and bulk skill operations should complete faster than a purely sequential run. The command output is still printed in stable order. For debugging, run `SV_JOBS=1 sv <command>` to force sequential execution.
```

Add this troubleshooting entry to `docs/troubleshooting.md`:

```markdown
## I want to disable parallel execution

Set `SV_JOBS=1` before the command:

```bash
SV_JOBS=1 sv list --refresh
SV_JOBS=1 sv update
```

`SV_JOBS` accepts integers from 1 through 64. Values outside that range, empty values, or non-integers fail with `SV_JOBS must be an integer between 1 and 64.`
```

- [ ] **Step 6: Update testing docs**

Add this note to `docs/testing.md` under Fast local test loops:

```markdown
When investigating ordering-sensitive failures, rerun the focused test with `SV_JOBS=1` to disable sv's internal worker pools:

```bash
SV_JOBS=1 uv run pytest tests/test_project.py -q --no-cov
```
```

- [ ] **Step 7: Run docs tests**

Run:

```bash
uv run pytest tests/test_docs.py -q --no-cov
```

Expected: all pass.

- [ ] **Step 8: Commit Task 8**

```bash
git add README.md docs/commands.md docs/usage.md docs/testing.md docs/troubleshooting.md tests/test_docs.py
git commit -m "docs: document sv parallelism controls"
```

---

### Task 9: Full verification and release-gate cleanup

**Files:**
- Verify entire repository.
- Modify only files needed to fix verification failures.

- [ ] **Step 1: Run focused non-coverage suite**

Run:

```bash
uv run pytest -m "not integration and not security" -q --no-cov
```

Expected: all tests pass. If a test asserts exact fake runner order for a function that now parallelizes independent repos, either:

1. set `SV_JOBS=1` in that test when it is testing command construction rather than scheduling, or
2. change the assertion to compare grouped per-repo command subsequences while preserving per-repo command order.

- [ ] **Step 2: Run integration tests**

Run:

```bash
uv run pytest -m integration -q --no-cov
```

Expected: all integration tests pass. These tests use local Git repositories and should not require network access.

- [ ] **Step 3: Run security tests**

Run:

```bash
uv run pytest -m security -q --no-cov
```

Expected: all security tests pass. Symlink rejection behavior must be unchanged.

- [ ] **Step 4: Run lint and type checks**

Run:

```bash
uv run ruff check .
uv run ty check src tests
```

Expected: both pass.

- [ ] **Step 5: Run full coverage gate**

Run:

```bash
uv run pytest --cov=sv --cov-report=term-missing
```

Expected: coverage passes the configured `--cov-fail-under=95` threshold.

- [ ] **Step 6: Build package**

Run:

```bash
rm -rf dist
uv build
```

Expected: exactly one wheel appears in `dist/` and build exits 0.

- [ ] **Step 7: Smoke-test built wheel**

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

Expected: `sv --help` prints command help and version assertion passes.

- [ ] **Step 8: Manual behavior spot checks**

Run these from temporary repos created by tests or a local scratch directory:

```bash
SV_JOBS=1 uv run sv list --refresh
SV_JOBS=2 uv run sv list --refresh
SV_JOBS=2 uv run sv add --all
SV_JOBS=2 uv run sv status
SV_JOBS=2 uv run sv update
SV_JOBS=2 uv run sv sync
```

Expected:

- Output order is stable between `SV_JOBS=1` and `SV_JOBS=2`.
- No raw tracebacks appear.
- `.pi/skills` contains no leftover `.sv-add-tmp`, `.sv-sync-tmp`, or `.sv-sync-backup` directories after successful commands.
- `.sv/manifest.toml` remains valid TOML.

- [ ] **Step 9: Inspect changed files**

Run:

```bash
git status --short
git diff --check
git diff --stat HEAD~8..HEAD
```

Expected:

- No whitespace errors.
- Only planned files changed.
- No generated coverage, build, or virtualenv artifacts staged.

- [ ] **Step 10: Commit verification fixes**

If verification required fixes after Task 8, commit them:

```bash
git add <fixed-files>
git commit -m "test: stabilize parallel sv workflows"
```

Skip this commit if no files changed after Task 8.

---

## Self-review checklist for implementer

- [ ] `SV_JOBS=1` runs every new parallel path inline.
- [ ] Invalid `SV_JOBS` values fail with `SV_JOBS must be an integer between 1 and 64.`
- [ ] `build_source_catalog_from_backends` parallelizes repos, not backends within one repo.
- [ ] Source backend fallback order is unchanged inside one repo.
- [ ] Warnings from catalog/index workers are printed in deterministic input order.
- [ ] No worker writes project/vault manifests.
- [ ] No worker renames temp trees into final skill targets.
- [ ] Same sparse Git cache path is locked across metadata checkout and materialization cleanup.
- [ ] Cache body writes, prune maintenance, and cached catalog hash writeback are serialized.
- [ ] Bulk add/sync/update output order is unchanged from the current sorted/input order.
- [ ] Status and index save one final TOML document after parallel hashing completes.
- [ ] Security symlink checks still happen before file traversal, copying, deletion, or manifest writes.
- [ ] All tests listed in Task 9 pass before claiming completion.

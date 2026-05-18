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
- Skill-body cache writes, body-cache hit touch/prune maintenance, catalog cache writes, and cached catalog hash writeback are serialized with one process-local `RLock`. This prevents lost cached metadata updates and same-process atomic-temp collisions when bulk materialization discovers multiple body hashes from the same repo.
- Project, vault, and global source-state manifest writes remain serial. No worker writes `.sv/manifest.toml`, `.pi/skills/.sv-manifest.toml`, or global source refresh state.
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
  - Serialize skill-body cache writes/prunes, body-cache hit materialization/touch, catalog cache writes, and cached catalog body-hash writeback.
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


def test_map_ordered_preserves_none_results_in_threaded_mode() -> None:
    assert map_ordered(["a", "b"], lambda _value: None, jobs=2) == [None, None]


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

from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from typing import TypeVar, cast

from sv.errors import SvError

T = TypeVar("T")
R = TypeVar("R")

_DEFAULT_MAX_JOBS = 8
_MAX_JOBS = 64
_MISSING = object()


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
    items: Iterable[T],
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

    results: list[R | object] = [_MISSING] * len(item_list)
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

    ordered: list[R] = []
    for result in results:
        if result is _MISSING:
            raise AssertionError("parallel worker result missing after successful join")
        ordered.append(cast(R, result))
    return ordered
```

- [ ] **Step 5: Run helper tests and quality checks**

Run:

```bash
uv run pytest tests/test_parallel.py -q --no-cov
uv run ruff check src/sv/parallel.py tests/test_parallel.py
uv run ty check src tests
```

Expected: all pass.

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

In `src/sv/catalog.py`, add this import:

```python
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

Expected: `ensure_source_repos` does not accept `jobs`. The CLI invalid-`SV_JOBS` test may already pass after Task 2 because lightweight source discovery uses `map_ordered`; keep it as a regression test for the CLI error contract.

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

In `src/sv/cli.py`, the helper `_ensure_source_repos_for_refresh` currently loops over repos. Replace that loop with `map_ordered`, but keep global source-state manifest writes in the caller after workers join. Workers must not call `_record_global_source_refresh_failure`, and the helper must not record failures itself; otherwise the outer `except SvError` path can overwrite a per-repo failure with an all-repos failure.

Add import:

```python
from sv.parallel import map_ordered
```

Change `_ensure_source_repos_for_refresh` to return the first deterministic repo failure instead of raising source-refresh failures directly:

```python
def _ensure_source_repos_for_refresh(
    repos: Sequence[RepoConfig],
    paths: SvPaths,
    git_runner,
    *,
    update: bool,
) -> tuple[RepoConfig, str] | None:
    def worker(repo: RepoConfig) -> tuple[RepoConfig, str | None]:
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
            return repo, str(exc)
        return repo, None

    results = map_ordered(list(repos), worker)
    for repo, error in results:
        if error is not None:
            return repo, error
    return None
```

Update the non-lightweight call site in `_update_sources_and_catalog_cache_refresh_from_repos` to record the returned failure serially, set `source_state_recorded = True`, and then raise. This prevents the outer exception handler from writing a second, broader failure record:

```python
            failure = _ensure_source_repos_for_refresh(
                repos,
                paths,
                git_runner,
                update=update,
            )
            if failure is not None:
                failed_repo, error = failure
                if record_global_source_state:
                    _record_global_source_refresh_failure(
                        paths, (failed_repo,), error, started_at
                    )
                    source_state_recorded = True
                raise SvError(error)
            catalog = build_source_catalog(repos, paths)
            refreshed_repo_ids = frozenset(repo.id for repo in repos)
```

This keeps failure reporting deterministic and lets invalid `SV_JOBS` errors bubble through the existing `handle` exception block without double-recording source state.

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
    first_inside = threading.Event()
    second_thread_started = threading.Event()
    second_inside = threading.Event()
    release = threading.Event()

    def fake_prepare(*args, **kwargs):
        return None

    def fake_copy(self, source_path, destination):
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
            if destination.name == "a":
                first_inside.set()
            else:
                second_inside.set()
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
            if destination.name == "b":
                second_thread_started.set()
            backend.materialize_folder("skills/alpha", destination)
        except BaseException as exc:  # noqa: BLE001 - test captures worker failures
            errors.append(exc)

    thread_a = threading.Thread(target=run_backend, args=(first, tmp_path / "a"))
    thread_b = threading.Thread(target=run_backend, args=(second, tmp_path / "b"))
    thread_a.start()
    assert first_inside.wait(2), "first materialization did not start"
    thread_b.start()
    assert second_thread_started.wait(2), "second materialization thread did not start"
    assert not second_inside.wait(0.25), "second materialization entered while first held same-repo lock"
    release.set()
    thread_a.join(2)
    thread_b.join(2)

    assert errors == []
    assert second_inside.is_set(), "second materialization never ran after first released"
    assert max_active == 1
```

- [ ] **Step 2: Add a cached catalog writeback concurrency test**

Append this test to `tests/test_source_cache.py` near `record_cached_skill_body_hash` tests:

```python
def test_record_cached_skill_body_hash_preserves_parallel_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    original_save_cached_catalog = source_cache.save_cached_catalog
    active_saves = 0
    active_saves_lock = threading.Lock()
    first_save_waiting = threading.Event()
    concurrent_save_entered = threading.Event()

    def coordinated_save_cached_catalog(
        save_paths: SvPaths,
        save_repo: RepoConfig,
        save_document: CachedCatalogDocument,
    ) -> None:
        nonlocal active_saves
        with active_saves_lock:
            active_saves += 1
            active_count = active_saves
            if active_count == 1:
                first_save_waiting.set()
            else:
                concurrent_save_entered.set()
        if active_count == 1:
            concurrent_save_entered.wait(0.5)
        else:
            assert first_save_waiting.is_set(), "first catalog save did not start"
        try:
            original_save_cached_catalog(save_paths, save_repo, save_document)
        finally:
            with active_saves_lock:
                active_saves -= 1
            concurrent_save_entered.set()

    monkeypatch.setattr(
        source_cache,
        "save_cached_catalog",
        coordinated_save_cached_catalog,
    )
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def record(entry: SourceSkill, content_hash: str, skill_file_hash: str) -> None:
        try:
            barrier.wait(2)
            record_cached_skill_body_hash(
                paths,
                repo,
                entry,
                content_hash=content_hash,
                skill_file_hash=skill_file_hash,
            )
        except BaseException as exc:  # noqa: BLE001 - test captures worker failures
            errors.append(exc)

    first = threading.Thread(target=record, args=(alpha, alpha_hash, alpha_skill_file_hash))
    second = threading.Thread(target=record, args=(beta, beta_hash, beta_skill_file_hash))
    first.start()
    second.start()
    first.join(2)
    second.join(2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
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
import shutil
import threading

import sv.source_cache as source_cache
from sv.catalog import SourceSkill
from sv.source_cache import CachedCatalogDocument, CachedCatalogEntry
```

If `_catalog_document` in `tests/test_source_cache.py` does not accept an `entries` keyword, extend that test helper so the default remains the current single `find-docs` entry and callers may pass a custom tuple of `CachedCatalogEntry` values.

Append this cached body miss fallback lock test to `tests/test_source_cache.py` near the other body-cache materialization tests:

```python
def test_cached_body_miss_source_fallback_serializes_same_repo_materialization(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    cached_alpha = replace(
        _source_skill(repo, paths, "alpha"),
        source_backend="cache:github-https-api",
        source_content_hash="sha256:" + "a" * 64,
    )
    cached_beta = replace(
        _source_skill(repo, paths, "beta"),
        source_backend="cache:github-https-api",
        source_content_hash="sha256:" + "b" * 64,
    )
    active = 0
    max_active = 0
    active_lock = threading.Lock()
    alpha_inside = threading.Event()
    beta_thread_started = threading.Event()
    beta_inside = threading.Event()
    release = threading.Event()

    def blocking_materializer(name: str):
        def materialize(destination: Path) -> None:
            nonlocal active, max_active
            with active_lock:
                active += 1
                max_active = max(max_active, active)
                if name == "alpha":
                    alpha_inside.set()
                else:
                    beta_inside.set()
            try:
                assert release.wait(2), "test did not release source fallback"
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "SKILL.md").write_text(
                    "---\n"
                    f"name: {name}\n"
                    "description: Fallback skill.\n"
                    "---\n"
                )
                (destination / "notes.md").write_text(f"{name} fallback\n")
            finally:
                with active_lock:
                    active -= 1

        return materialize

    refreshed_by_name = {
        "alpha": replace(
            cached_alpha,
            source_backend="fake-remote",
            source_content_hash=None,
            _materializer=blocking_materializer("alpha"),
        ),
        "beta": replace(
            cached_beta,
            source_backend="fake-remote",
            source_content_hash=None,
            _materializer=blocking_materializer("beta"),
        ),
    }
    wrapped = wrap_catalog_with_skill_body_cache(
        [cached_alpha, cached_beta],
        paths,
        now=lambda: datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        after_store=lambda *_args: None,
        refresh_entry_on_body_miss=lambda entry: refreshed_by_name[entry.name],
    )
    errors: list[BaseException] = []

    def run_materialize(entry: SourceSkill, destination: Path) -> None:
        try:
            if entry.name == "beta":
                beta_thread_started.set()
            entry.materialize_to(destination)
        except BaseException as exc:  # noqa: BLE001 - test captures worker failures
            errors.append(exc)

    first = threading.Thread(target=run_materialize, args=(wrapped[0], tmp_path / "alpha-dest"))
    second = threading.Thread(target=run_materialize, args=(wrapped[1], tmp_path / "beta-dest"))
    first.start()
    assert alpha_inside.wait(2), "first source fallback did not start"
    second.start()
    assert beta_thread_started.wait(2), "second source fallback thread did not start"
    assert not beta_inside.wait(0.25), "same-repo source fallback materialization overlapped"
    release.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert beta_inside.is_set(), "second source fallback never ran after first released"
    assert max_active == 1
    assert (tmp_path / "alpha-dest" / "notes.md").read_text() == "alpha fallback\n"
    assert (tmp_path / "beta-dest" / "notes.md").read_text() == "beta fallback\n"
```

Append this direct cached-entry source fallback lock test to `tests/test_source_cache.py` near `attach_source_materializers` tests:

```python
def test_attach_source_materializers_serializes_same_repo_cached_entries(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    source_root = tmp_path / "remote"
    _write_skill_tree(source_root / "skills", "alpha", "alpha fallback\n")
    _write_skill_tree(source_root / "skills", "beta", "beta fallback\n")
    cached_alpha = replace(
        _source_skill(repo, paths, "alpha"),
        source_backend="cache:github-https-api",
    )
    cached_beta = replace(
        _source_skill(repo, paths, "beta"),
        source_backend="cache:github-https-api",
    )
    active = 0
    max_active = 0
    active_lock = threading.Lock()
    alpha_inside = threading.Event()
    beta_thread_started = threading.Event()
    beta_inside = threading.Event()
    release = threading.Event()

    class BlockingBackend:
        name = "blocking"

        def read_index(self) -> bytes | None:
            raise AssertionError("fallback materialization test should not read indexes")

        def list_candidate_skill_files(self, configured_skills_paths=()):
            raise AssertionError("fallback materialization test should not list skills")

        def read_file(self, path: str) -> bytes:
            raise AssertionError("fallback materialization test should not read files")

        def materialize_folder(self, source_path: str, destination: Path) -> None:
            nonlocal active, max_active
            with active_lock:
                active += 1
                max_active = max(max_active, active)
                if source_path.endswith("/alpha"):
                    alpha_inside.set()
                else:
                    beta_inside.set()
            try:
                assert release.wait(2), "test did not release source fallback"
                source = source_root / Path(*source_path.split("/"))
                shutil.copytree(source, destination)
            finally:
                with active_lock:
                    active -= 1

    def backend_factory(selected_repo: RepoConfig):
        assert selected_repo == repo
        return (BlockingBackend(),)

    attached = attach_source_materializers(
        [cached_alpha, cached_beta],
        [repo],
        backend_factory=backend_factory,
    )
    errors: list[BaseException] = []

    def run_materialize(entry: SourceSkill, destination: Path) -> None:
        try:
            if entry.name == "beta":
                beta_thread_started.set()
            entry.materialize_to(destination)
        except BaseException as exc:  # noqa: BLE001 - test captures worker failures
            errors.append(exc)

    first = threading.Thread(target=run_materialize, args=(attached[0], tmp_path / "alpha-attached"))
    second = threading.Thread(target=run_materialize, args=(attached[1], tmp_path / "beta-attached"))
    first.start()
    assert alpha_inside.wait(2), "first attached fallback did not start"
    second.start()
    assert beta_thread_started.wait(2), "second attached fallback thread did not start"
    assert not beta_inside.wait(0.25), "same-repo attached fallback materialization overlapped"
    release.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert beta_inside.is_set(), "second attached fallback never ran after first released"
    assert max_active == 1
    assert (tmp_path / "alpha-attached" / "notes.md").read_text() == "alpha fallback\n"
    assert (tmp_path / "beta-attached" / "notes.md").read_text() == "beta fallback\n"
```

- [ ] **Step 3: Run the new lock tests and verify failures**

Run:

```bash
uv run pytest \
  tests/test_source.py::test_sparse_backend_materialization_serializes_same_repo_cache \
  tests/test_source_cache.py::test_record_cached_skill_body_hash_preserves_parallel_updates \
  tests/test_source_cache.py::test_cached_body_miss_source_fallback_serializes_same_repo_materialization \
  tests/test_source_cache.py::test_attach_source_materializers_serializes_same_repo_cached_entries \
  -q --no-cov
```

Expected: sparse materialization may overlap, the coordinated cached-catalog save wrapper should force two unlocked writebacks to save stale documents so one hash update is lost without a lock, and same-repo cached body miss/attached fallback materialization should overlap without the fallback lock.

- [ ] **Step 4: Add source repo cache locks**

In `src/sv/source.py`, add imports:

```python
from _thread import RLock as RLockType
import threading
```

Add these module-level helpers near constants:

```python
_SOURCE_REPO_LOCKS_GUARD = threading.Lock()
_SOURCE_REPO_LOCKS: dict[Path, RLockType] = {}


def _source_repo_lock_key(repo_path: Path) -> Path:
    try:
        return repo_path.resolve()
    except OSError:
        return repo_path.absolute()


def _source_repo_lock(repo_path: Path) -> RLockType:
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

Also lock the full direct sparse mutation lifecycle used by `ensure_source_repo()` and `_ensure_sparse_git_repo()`. Do not lock only `_ensure_sparse_git_repo_after_git_check`; the treeless failure cleanup, blobless fallback, and `_remove_failed_lightweight_checkout()` calls must stay inside the same repo lock.

Move each current public function body into a private locked helper and make the public function acquire `_source_repo_lock(repo_path)` first:

```python
def ensure_source_repo(
    repo_url: str,
    repo_path: Path,
    runner: Runner = default_runner,
    *,
    update: bool = True,
    configured_skills_paths: Sequence[str] = (),
) -> None:
    with _source_repo_lock(repo_path):
        _ensure_source_repo_locked(
            repo_url,
            repo_path,
            runner=runner,
            update=update,
            configured_skills_paths=configured_skills_paths,
        )


def _ensure_sparse_git_repo(
    repo_url: str,
    repo_path: Path,
    runner: Runner,
    *,
    filter_spec: str,
    sparse_patterns: Sequence[str],
    update: bool,
) -> None:
    with _source_repo_lock(repo_path):
        _ensure_sparse_git_repo_locked(
            repo_url,
            repo_path,
            runner,
            filter_spec=filter_spec,
            sparse_patterns=sparse_patterns,
            update=update,
        )


def _ensure_sparse_git_repo_after_git_check(
    repo_url: str,
    repo_path: Path,
    runner: Runner,
    *,
    filter_spec: str,
    sparse_patterns: Sequence[str],
    update: bool,
) -> None:
    with _source_repo_lock(repo_path):
        _ensure_sparse_git_repo_after_git_check_locked(
            repo_url,
            repo_path,
            runner,
            filter_spec=filter_spec,
            sparse_patterns=sparse_patterns,
            update=update,
        )
```

Create `_ensure_source_repo_locked(...)`, `_ensure_sparse_git_repo_locked(...)`, and `_ensure_sparse_git_repo_after_git_check_locked(...)` by moving the existing bodies unchanged. Nested calls may reacquire the same `RLock`. This central lock covers `ensure_source_repo()`, `ensure_source_repos()`, CLI non-lightweight refresh, sparse backend metadata checkout, sparse materialization cleanup, and failed-checkout cleanup.

- [ ] **Step 6: Add cache write lock**

In `src/sv/source_cache.py`, import `threading` and `RLockType`, then add this near constants:

```python
from _thread import RLock as RLockType
import threading

_CACHE_WRITE_LOCK = threading.RLock()
```

Serialize every skill-body cache metadata write, body-cache install/delete, prune, catalog cache write, and cached catalog hash writeback with `with _CACHE_WRITE_LOCK:`:

```python
def save_cached_catalog(...):
    with _CACHE_WRITE_LOCK:
        return _save_cached_catalog_locked(...)


def store_skill_body_cache(...):
    with _CACHE_WRITE_LOCK:
        return _store_skill_body_cache_locked(...)


def try_materialize_from_skill_body_cache(...):
    with _CACHE_WRITE_LOCK:
        return _try_materialize_from_skill_body_cache_locked(...)


def record_cached_skill_body_hash(...):
    with _CACHE_WRITE_LOCK:
        return _record_cached_skill_body_hash_locked(...)


def prune_skill_body_cache(...):
    with _CACHE_WRITE_LOCK:
        return _prune_skill_body_cache_locked(...)


def clean_cache(...):
    with _CACHE_WRITE_LOCK:
        return _clean_cache_locked(...)
```

Use private locked helpers for those functions. Move each existing function body into its helper without changing behavior. Because the lock is an `RLock`, `store_skill_body_cache()` can still call `_prune_after_body_cache_write()`, `try_materialize_from_skill_body_cache()` can still touch and prune after a hit, and `clean_cache()` can still call `prune_skill_body_cache()`.

Locking `save_cached_catalog()` is required because same-process concurrent refresh-on-body-miss paths can otherwise write the same catalog cache file through the same atomic temp name. Locking the full `try_materialize_from_skill_body_cache()` body is required because checking validity, copying from the body cache, touching metadata, and pruning must not race with another worker pruning or replacing the same body-cache entry.

Also keep `_touch_skill_body_cache()` itself protected by `_CACHE_WRITE_LOCK` for any internal callers. This prevents lost `use_count`/`last_used_at` updates and prune/delete races outside the store path. Add or update source-cache tests so concurrent cached body misses for two skills in the same repo do not collide on catalog cache writes.

Add a second lock family in `source_cache.py` for cached source fallback materialization by repo/source identity:

```python
_SOURCE_FALLBACK_LOCKS_GUARD = threading.Lock()
_SOURCE_FALLBACK_LOCKS: dict[tuple[str, str], RLockType] = {}


def _source_fallback_lock(entry: SourceSkill) -> RLockType:
    key = (entry.repo_id, repo_source_key(entry.repo_url))
    with _SOURCE_FALLBACK_LOCKS_GUARD:
        lock = _SOURCE_FALLBACK_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _SOURCE_FALLBACK_LOCKS[key] = lock
        return lock
```

Use this lock around the source-fallback branches that run after a cached body miss:

- In `attach_source_materializers()`, wrap the backend fallback loop for one cached entry with `with _source_fallback_lock(cached_entry):`.
- In `_wrap_source_skill()`, when `entry.source_backend.startswith("cache:")`, wrap `refresh_entry_on_body_miss(entry)` and the refreshed entry's `materialize_to(destination)` call with `with _source_fallback_lock(entry):`.

This keeps same-repo cached body misses from concurrently refreshing/writing catalog metadata or mutating sparse source cache paths through independent fallback backend instances.

- [ ] **Step 7: Attach materializer locks for in-memory backend instances**

In `src/sv/catalog.py`, add these imports and create one `threading.RLock()` per `_catalog_entries_from_backend` call:

```python
from _thread import RLock as RLockType
import threading
```

Pass that lock to entry materializers.

At the top of `_catalog_entries_from_backend`, after index handling begins, use:

```python
    materialize_lock = threading.RLock()
```

Change `_catalog_entries_from_index` to accept a `materialize_lock: RLockType` parameter, and update both `_catalog_entries_from_index` and non-index entry creation to pass that lock:

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
    lock: RLockType | None = None,
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
  tests/test_source_cache.py::test_cached_body_miss_source_fallback_serializes_same_repo_materialization \
  tests/test_source_cache.py::test_attach_source_materializers_serializes_same_repo_cached_entries \
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
- Modify: `tests/test_vault_mode_targets.py`

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
from sv.project import add_all_project_skills, add_all_vault_skills
```

- [ ] **Step 2: Add a no-final-target-on-prepare-failure test**

Append this test to `tests/test_project.py`:

```python
class FailingPrepareProjectSourceSkill(BlockingProjectSourceSkill):
    def materialize_to(self, destination: Path) -> None:
        self.started.set()
        assert self.peer_started.wait(2), "failing add-all materialization did not overlap"
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

Append this replacement-status/temp-path regression test to `tests/test_project.py`:

```python
def test_add_all_vault_skills_replaces_existing_targets_with_replace_temp(
    tmp_path: Path,
) -> None:
    alpha = make_source_skill(tmp_path / "source", "alpha")
    vault_skills = tmp_path / "vault" / "skills"
    add_vault_skill(alpha, vault_skills)
    (alpha.source_path / "notes.md").write_text("alpha replacement\n")

    result = add_all_vault_skills([alpha], vault_skills, replace_existing=True)

    assert [(item.skill, item.status) for item in result.results] == [
        ("alpha", "replaced")
    ]
    assert (vault_skills / "alpha" / "notes.md").read_text() == "alpha replacement\n"
    assert_no_partial_sv_dirs(vault_skills)
```

Append this duplicate-result regression test to `tests/test_project.py` so bulk planning keeps one output row per catalog item instead of collapsing by skill name:

```python
def test_add_all_project_skills_preserves_duplicate_name_result_rows(tmp_path: Path) -> None:
    first = make_source_skill(tmp_path / "source-a", "alpha", repo_id="Org/A")
    second = make_source_skill(tmp_path / "source-b", "alpha", repo_id="Org/B")
    project_skills = tmp_path / "project" / ".pi" / "skills"

    result = add_all_project_skills([first, second], project_skills)

    assert [(item.skill, item.status, item.repo_id, item.existing_repo_id) for item in result.results] == [
        ("alpha", "added", "Org/A", None),
        ("alpha", "exists", "Org/B", "Org/A"),
    ]
```

Append this vault duplicate-name replacement regression test so prompt decisions are keyed by catalog item, not only skill name:

```python
def test_add_all_vault_skills_honors_duplicate_name_replace_indexes(tmp_path: Path) -> None:
    installed = make_source_skill(tmp_path / "installed-source", "alpha", repo_id="Org/Installed")
    first = make_source_skill(tmp_path / "source-a", "alpha", repo_id="Org/A")
    second = make_source_skill(tmp_path / "source-b", "alpha", repo_id="Org/B")
    vault_skills = tmp_path / "vault" / "skills"
    add_vault_skill(installed, vault_skills)
    (second.source_path / "notes.md").write_text("second replacement\n")

    result = add_all_vault_skills(
        [first, second],
        vault_skills,
        replace_existing_indexes=frozenset({1}),
    )

    assert [(item.skill, item.status, item.repo_id, item.existing_repo_id) for item in result.results] == [
        ("alpha", "exists", "Org/A", "Org/Installed"),
        ("alpha", "replaced", "Org/B", None),
    ]
    assert (vault_skills / "alpha" / "notes.md").read_text() == "second replacement\n"
    assert_no_partial_sv_dirs(vault_skills)
```

- [ ] **Step 3: Run the new tests and verify they fail**

Run:

```bash
uv run pytest \
  tests/test_project.py::test_add_all_project_skills_prepares_new_skills_in_parallel \
  tests/test_project.py::test_add_all_project_skills_cleans_prepared_temps_when_one_prepare_fails \
  tests/test_project.py::test_add_all_vault_skills_replaces_existing_targets_with_replace_temp \
  tests/test_project.py::test_add_all_project_skills_preserves_duplicate_name_result_rows \
  tests/test_project.py::test_add_all_vault_skills_honors_duplicate_name_replace_indexes \
  -q --no-cov
```

Expected: first test fails because add-all is serial; second may fail because sequential add can commit `alpha` before `beta` fails; third fails until bulk replacement prepares into the sync temp and reports `replaced`; fourth protects per-catalog result ordering for duplicate names; fifth fails until replacement decisions can target a specific catalog item.

- [ ] **Step 4: Add add-all plan dataclasses**

In `src/sv/project.py`, import the helper:

```python
from sv.parallel import map_ordered
```

Add these dataclasses after `_MaterializedSkillMetadata`:

```python
@dataclass(frozen=True)
class _AddPlan:
    index: int
    entry: ProjectSourceSkill
    skill_name: str
    target: Path
    replace_existing_target: bool = False
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
    replace_existing_indexes: frozenset[int] = frozenset(),
) -> tuple[list[_AddPlan], list[_AddPlan]]:
    manifest = _load_manifest_for_target(project_skills_dir, target_style)
    existing_or_skipped: list[_AddPlan] = []
    to_prepare: list[_AddPlan] = []
    planned_targets: dict[str, ProjectSourceSkill] = {}
    for index, entry in enumerate(catalog):
        skill_name = normalize_skill_name(entry.name)
        if entry.source_backend == "local-cache" and not entry.source_path.is_dir():
            raise SvError(f"Skill '{skill_name}' was not found in source skills directory.")
        target = project_skills_dir / skill_name
        _reject_symlinked_project_skill(target, target_style)
        planned_entry = planned_targets.get(skill_name)
        if planned_entry is not None:
            existing_or_skipped.append(
                _AddPlan(
                    index=index,
                    entry=entry,
                    skill_name=skill_name,
                    target=target,
                    result=AddSkillResult(
                        skill=skill_name,
                        target=target,
                        status="exists",
                        repo_id=entry.repo_id,
                        existing_repo_id=planned_entry.repo_id,
                        source_reference=_source_reference_for(entry),
                        existing_source_reference=_source_reference_for(planned_entry),
                        target_kind=target_style.target_kind,
                    ),
                )
            )
            continue
        target_exists = target.exists() or target.is_symlink()
        should_replace = replace_existing or index in replace_existing_indexes
        if target_exists:
            _reject_symlinked_project_skill(target, target_style)
            if not target.is_dir():
                raise SvError(
                    f"Cannot add {target_style.skill_label} '{skill_name}': non-directory path already exists at {target}."
                )
            existing_entry = manifest.get(skill_name)
            if not should_replace:
                existing_or_skipped.append(
                    _AddPlan(
                        index=index,
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
        to_prepare.append(
            _AddPlan(
                index=index,
                entry=entry,
                skill_name=skill_name,
                target=target,
                replace_existing_target=target_exists and should_replace,
            )
        )
        planned_targets[skill_name] = entry
    return existing_or_skipped, to_prepare
```

- [ ] **Step 6: Add parallel prepare and serial commit helper**

Add these helpers near `_materialize_entry_for_add`:

```python
def _add_plan_temp_target(plan: _AddPlan) -> Path:
    if plan.replace_existing_target:
        return _sync_temp_target(plan.target)
    return _add_temp_target(plan.target)


def _prepare_add_plan(plan: _AddPlan, target_style: _TargetStyle) -> _PreparedAdd:
    if plan.replace_existing_target:
        metadata = _materialize_entry_for_replace(plan.entry, plan.target, target_style)
    else:
        metadata = _materialize_entry_for_add(plan.entry, plan.target, target_style)
    return _PreparedAdd(plan=plan, metadata=metadata)


def _cleanup_prepared_adds(prepared: Sequence[_PreparedAdd]) -> None:
    for item in prepared:
        remove_materialization_path(_add_plan_temp_target(item.plan), ignore_errors=True)
```

Add this bulk helper near `add_all_project_skills`:

```python
def _add_all_skills_parallel(
    catalog: Sequence[ProjectSourceSkill],
    project_skills_dir: Path,
    target_style: _TargetStyle,
    *,
    replace_existing: bool = False,
    replace_existing_indexes: frozenset[int] = frozenset(),
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
        replace_existing_indexes=replace_existing_indexes,
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
            remove_materialization_path(_add_plan_temp_target(plan), ignore_errors=True)
        raise

    ordered_results: list[AddSkillResult | None] = [None] * len(catalog)
    for plan in existing_or_skipped:
        if plan.result is not None:
            ordered_results[plan.index] = plan.result

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

            if plan.replace_existing_target:
                _replace_with_materialized_entry(
                    plan.target,
                    after_replace=update_manifest,
                    target_style=target_style,
                )
                status = "replaced"
            else:
                install_materialized_skill_folder(
                    _add_temp_target(plan.target),
                    plan.target,
                    error_message=f"Failed to add {target_style.skill_label} '{plan.skill_name}'",
                    after_install=update_manifest,
                )
                status = "added"
            ordered_results[plan.index] = AddSkillResult(
                skill=plan.skill_name,
                target=plan.target,
                status=status,
                repo_id=plan.entry.repo_id,
                source_reference=_source_reference_for(plan.entry),
                target_kind=target_style.target_kind,
            )
    except Exception:
        for item in prepared:
            remove_materialization_path(_add_plan_temp_target(item.plan), ignore_errors=True)
        raise

    results: list[AddSkillResult] = []
    for result in ordered_results:
        if result is None:
            raise AssertionError("missing add-all result after planning and commit")
        results.append(result)
    return AddAllSkillsResult(results=results)
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
    replace_existing_indexes: frozenset[int] = frozenset(),
) -> AddAllSkillsResult:
    return _add_all_skills_parallel(
        catalog,
        vault_skills_dir,
        _VAULT_TARGET,
        replace_existing=replace_existing,
        replace_existing_indexes=replace_existing_indexes,
    )
```

- [ ] **Step 8: Use `add_all_vault_skills` from CLI after replacement decisions**

In `src/sv/cli.py`, import `add_all_vault_skills` from `sv.project`.

Replace the skill-vault branch of `_add_all_skills_to_context` with serial replacement resolution followed by one bulk call. Keep the full catalog so "no" prompt decisions still report `exists`, and pass affirmative prompt decisions as catalog indexes so duplicate skill names remain distinct:

```python
    if context.is_skill_vault:
        replace_indexes: set[int] = set()
        for index, entry in enumerate(catalog):
            should_replace = _resolve_vault_replacement(
                entry,
                context.vault_skills_dir,
                replace_existing=replace_existing,
            )
            if should_replace:
                replace_indexes.add(index)
        return add_all_vault_skills(
            catalog,
            context.vault_skills_dir,
            replace_existing=replace_existing,
            replace_existing_indexes=frozenset(replace_indexes),
        )
```

Keep prompts serial. Do not prompt from workers, and do not discard per-catalog-item prompt decisions.

- [ ] **Step 9: Run add tests**

Run:

```bash
uv run pytest \
  tests/test_project.py::test_add_all_project_skills_prepares_new_skills_in_parallel \
  tests/test_project.py::test_add_all_project_skills_cleans_prepared_temps_when_one_prepare_fails \
  tests/test_project.py::test_add_all_vault_skills_replaces_existing_targets_with_replace_temp \
  tests/test_project.py::test_add_all_project_skills_preserves_duplicate_name_result_rows \
  tests/test_project.py::test_add_all_vault_skills_honors_duplicate_name_replace_indexes \
  tests/test_project.py \
  tests/test_cli_add_contracts.py \
  tests/test_vault_mode_targets.py \
  -q --no-cov
```

Expected: all pass. If any CLI add-all output ordering test fails, keep printing in the existing `result.results` order and fix only the result ordering in `_add_all_skills_parallel`.

- [ ] **Step 10: Commit Task 5**

```bash
git add src/sv/project.py src/sv/cli.py tests/test_project.py tests/test_cli_add_contracts.py tests/test_vault_mode_targets.py
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

Append this regression test to `tests/test_project.py` to protect source-hash-unavailable update behavior:

```python
def test_update_project_skills_keeps_unhashed_materialized_source_unchanged(
    tmp_path: Path,
) -> None:
    source_entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    add_project_skill(source_entry, project_skills)

    def materialize(destination: Path) -> None:
        shutil.copytree(source_entry.source_path, destination)

    remote_entry = SourceSkill(
        name="managed",
        description="Managed skill.",
        repo_id=source_entry.repo_id,
        repo_url=source_entry.repo_url,
        repo_path=tmp_path / "missing-source-cache",
        source_path=tmp_path / "missing-source-cache" / "skills" / "managed",
        source_relative_path="skills/managed",
        source_backend="fake-remote",
        _materializer=materialize,
    )

    result = update_project_skills([remote_entry], project_skills)

    assert result.updated == []
    assert [(skip.skill, skip.reason) for skip in result.skipped] == [
        ("managed", "unchanged")
    ]
    assert_no_partial_sv_dirs(project_skills)
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


@dataclass(frozen=True)
class _ReplacementWork:
    plan: _ReplacementPlan
    prepared: _PreparedReplacement | None = None
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


def _cleanup_replacement_work(work: Sequence[_ReplacementWork]) -> None:
    for item in work:
        remove_materialization_path(_sync_temp_target(item.plan.target), ignore_errors=True)


def _prepare_replacement_work_ordered(
    work: Sequence[_ReplacementWork],
) -> list[_PreparedReplacement]:
    plans_to_prepare = [item.plan for item in work if item.prepared is None]
    try:
        prepared_iter = iter(map_ordered(plans_to_prepare, _prepare_replacement_plan))
        prepared_replacements: list[_PreparedReplacement] = []
        for item in work:
            if item.prepared is not None:
                prepared_replacements.append(item.prepared)
            else:
                prepared_replacements.append(next(prepared_iter))
        return prepared_replacements
    except Exception:
        _cleanup_replacement_work(work)
        raise


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


def _commit_prepared_replacements(
    prepared_replacements: Sequence[_PreparedReplacement],
) -> None:
    try:
        for prepared in prepared_replacements:
            _commit_prepared_replacement(prepared)
    except Exception:
        for prepared in prepared_replacements:
            remove_materialization_path(
                _sync_temp_target(prepared.plan.target), ignore_errors=True
            )
        raise
```

- [ ] **Step 6: Update `_sync_skills` to collect replacement plans**

In `_sync_skills`, keep all existing skip/source-missing logic. Change only the branch that currently calls `_replace_tree_and_update_manifest(...)`.

Before the local skills loop, add:

```python
    replacement_work: list[_ReplacementWork] = []
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
            replacement_work.append(
                _ReplacementWork(
                    _ReplacementPlan(
                        entry=entry,
                        target=local_skill,
                        project_skills_dir=project_skills_dir,
                        target_style=target_style,
                    )
                )
            )
            updated.append(local_skill.name)
            continue
```

After the local skills loop and before returning `SyncResult`, add:

```python
    prepared_replacements = _prepare_replacement_work_ordered(replacement_work)
    _commit_prepared_replacements(prepared_replacements)
```

This preserves the `updated` list order because it is still appended during deterministic local skill iteration.

- [ ] **Step 7: Update `_update_skills` changed-source branches**

In `_update_skills`, add before the manifest loop:

```python
    replacement_work: list[_ReplacementWork] = []
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
            replacement_work.append(
                _ReplacementWork(
                    _ReplacementPlan(
                        entry=source_entry,
                        target=target,
                        project_skills_dir=project_skills_dir,
                        target_style=target_style,
                    )
                )
            )
            updated.append(skill_name)
            continue
```

For the final branch where `source_hash is None`, keep the existing materialize-and-compare step because it is the only way to know whether the source actually changed. Only queue a commit when the materialized content differs from the installed baseline. Replace only the old inline manifest-entry/`_replace_with_materialized_entry(...)` commit portion after the `metadata.content_hash == baseline_hash` unchanged branch with this queued prepared replacement:

```python
        replacement_plan = _ReplacementPlan(
            entry=source_entry,
            target=target,
            project_skills_dir=project_skills_dir,
            target_style=target_style,
        )
        replacement_work.append(
            _ReplacementWork(
                replacement_plan,
                _PreparedReplacement(replacement_plan, metadata),
            )
        )
        updated.append(skill_name)
```

Because this final branch prepares a temp tree during the manifest loop, wrap the manifest loop in `try/except Exception` and call `_cleanup_replacement_work(replacement_work)` before re-raising if a later serial compare materialization fails.

After the manifest loop and before returning `SyncResult`, add:

```python
    prepared_replacements = _prepare_replacement_work_ordered(replacement_work)
    _commit_prepared_replacements(prepared_replacements)
```

This commits every prepared replacement through the same manifest path, preserves manifest-order commits, preserves unchanged-source behavior when source hashes are unavailable, and avoids stale metadata variables from the old inline branch.

- [ ] **Step 8: Keep compare-only materialization behavior correct**

The branches in `_update_skills` that materialize only to discover `source_hash` and then discard unchanged temps must remain serial until they are refactored into a distinct compare plan. Keep these existing unchanged-source blocks intact:

```python
metadata = _materialize_entry_for_replace(source_entry, target, target_style)
remove_materialization_path(_sync_temp_target(target), ignore_errors=True)
```

For the final `source_hash is None` branch, do not replace the materialize/compare logic with a blind replacement plan. It must still check `metadata.content_hash == baseline_hash` and report `unchanged` without committing when the materialized source matches the installed baseline. Only the changed case should enqueue an already prepared `_PreparedReplacement` for the serial commit phase.

This avoids changing unchanged-source behavior while still parallelizing actual replacements with known source hashes. A separate compare-plan refactor can be done after this plan if profiling shows it matters.

- [ ] **Step 9: Run sync/update tests**

Run:

```bash
uv run pytest \
  tests/test_project.py::test_sync_project_skills_prepares_replacements_in_parallel \
  tests/test_project.py::test_update_project_skills_prepares_changed_replacements_in_parallel \
  tests/test_project.py::test_update_project_skills_keeps_unhashed_materialized_source_unchanged \
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
    _write_skill(tmp_path / "skills" / "alpha", "alpha", "Alpha skill.")
    _write_skill(tmp_path / "skills" / "beta", "beta", "Beta skill.")
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

Use the existing `_write_skill` helper in `tests/test_index.py` and keep the same assertions. Add imports:

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

````markdown
## I want to disable parallel execution

Set `SV_JOBS=1` before the command:

```bash
SV_JOBS=1 sv list --refresh
SV_JOBS=1 sv update
```

`SV_JOBS` accepts integers from 1 through 64. Values outside that range, empty values, or non-integers fail with `SV_JOBS must be an integer between 1 and 64.`
````

- [ ] **Step 6: Update testing docs**

Add this note to `docs/testing.md` under Fast local test loops:

````markdown
When investigating ordering-sensitive failures, rerun the focused test with `SV_JOBS=1` to disable sv's internal worker pools:

```bash
SV_JOBS=1 uv run pytest tests/test_project.py -q --no-cov
```
````

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

Run these from a local scratch project with a configured test source repo and at least one installed skill. Do not run them against an arbitrary developer project unless you intend to mutate that project. A scratch setup can reuse the same local Git source shape created by the integration tests.

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
- [ ] Cache body writes, body-cache hit touch/prune maintenance, catalog cache writes, and cached catalog hash writeback are serialized.
- [ ] Bulk add/sync/update output order is unchanged from the current sorted/input order.
- [ ] Status and index save one final TOML document after parallel hashing completes.
- [ ] Security symlink checks still happen before file traversal, copying, deletion, or manifest writes.
- [ ] All tests listed in Task 9 pass before claiming completion.

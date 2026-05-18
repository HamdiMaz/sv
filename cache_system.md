# Cache System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a uv-like global cache for `sv` source metadata and full skill folders so common commands are fast, resilient, and bounded in disk usage.

**Architecture:** Add a new `sv.source_cache` module that owns cache paths, metadata TTL policy, catalog serialization, content-addressed skill-body storage, stale fallback, and lazy pruning. Existing source backends remain the authority for refreshing metadata and materializing cache misses in normal/refresh modes; cache-only mode never calls source backends for metadata or skill bodies. Cached metadata is only used directly with a validated body-cache hit; if a cached catalog entry needs source materialization because the body cache misses, normal/refresh modes first refresh that repo's metadata and then materialize the refreshed entry so stale metadata is never paired with a newer source body. The cache manager verifies cached catalog hashes on load, preserves source backend/index-hash provenance, wraps catalog entries with cache-aware materializers, writes observed body hashes back into cached metadata for non-index sources after successful materialization, and treats non-security cache maintenance failures as warnings instead of failed installs.

**Tech Stack:** Python 3.14 standard library, existing `tomllib` plus `sv.tomlutil.atomic_write_text`/`toml_escape`, existing `SvPaths`, `SourceSkill`, `SourceBackend`, manifest hash helpers, pytest, ruff, ty.

---

## Defaults and behavior locked by this plan

- Metadata cache TTL: **24 hours**.
- Skill body cache retention: **30 days unused** and **256 MiB max total skill-body bytes**.
- Refresh model: **lazy and synchronous**. No background process, daemon, thread, or scheduled job.
- Expired metadata refresh failure policy is command-specific:
  - `list`, `search`, `add`, and `add --all` warn and use stale metadata when cached metadata exists.
  - `sync`, `update`, and source-aware `status` are refresh-first by default and fail closed on refresh errors so existing update/status semantics stay current.
- Skill bodies are content-addressed by source content hash; indexed sources provide the hash up front, and non-index sources learn it after the first successful materialization and write it back to cached metadata.
- Cached skill folders are validated by hash before use. Existing body-cache entries are reused only when both metadata and the cached `skill/` tree validate against the requested content hash.
- Cached catalog files are validated on load: schema, configured repo identity, every normalized field, and stored `catalog_hash` must match the serialized entries.
- Cached metadata + body-cache miss policy:
  - `--cached`: fail with guidance and never call source backends.
  - normal/`--refresh`: refresh that repo's metadata first, then materialize the refreshed catalog entry from source. Indexed entries still validate the materialized tree against the refreshed content hash.
- Cache filesystem access rejects symlinked cache roots, cache entry directories, metadata files, body `skill/` directories, and temp paths before reading, writing, copying, or deleting. Cache directories are created owner-private (`0700`) where supported.
- Cache pruning runs lazily after cache writes, at most once per day.
- Commands that read source metadata still route through the same cache manager path, but with different default policies:
  - `list`, `search`, `add`, and `add --all`: normal 24-hour TTL policy.
  - `sync`, `update`, and source-aware `status`: force-refresh policy by default.
- User overrides:
  - `--refresh`: force source metadata refresh before command work.
  - `--cached`: use cached metadata only and avoid network/Git refreshes. Commands that need to materialize a skill body in `--cached` mode must hit the body cache; otherwise they fail with guidance to rerun without `--cached` once.
- Cache writes that are not part of the user-visible install/update outcome are best-effort: corrupt cache metadata, prune marker issues, or quota cleanup errors warn and continue after the source/project operation succeeds. Security violations (for example symlinked cache paths) still fail closed.

## File structure

- Modify `src/sv/config.py`
  - Add cache path properties to `SvPaths`.
- Create `src/sv/source_cache.py`
  - Cache policy, catalog cache read/write, stale fallback, body cache read/write, cache-aware materializers, lazy pruning.
- Modify `src/sv/cli.py`
  - Add cache flags to source-consuming commands.
  - Route source catalog reads through cache manager.
  - Add `sv cache status` and `sv cache clean`.
- Leave `src/sv/catalog.py` unchanged in the first implementation pass.
  - Rebuild cached `SourceSkill` entries inside `source_cache.py` so catalog parsing and cache policy stay separate.
- Leave `src/sv/project.py` unchanged in the first implementation pass.
  - Wrap `SourceSkill.materialize_to` in `source_cache.py` so project/vault install logic continues to validate materialized trees through existing code.
- Create `tests/test_source_cache.py`
  - Unit tests for cache paths, serialization, freshness, stale fallback, body cache, and pruning.
- Modify CLI contract tests
  - `tests/test_cli_list_contracts.py`
  - `tests/test_cli_search_contracts.py`
  - `tests/test_cli_add_contracts.py`
  - `tests/test_cli_sync_contracts.py`
  - `tests/test_cli_update_contracts.py`
  - `tests/test_cli_status_contracts.py`
- Modify docs
  - `README.md`
  - `docs/commands.md`
  - `docs/usage.md`
  - `docs/troubleshooting.md`

---

### Task 1: Add cache paths and policy primitives

**Files:**
- Modify: `src/sv/config.py`
- Create: `src/sv/source_cache.py`
- Test: `tests/test_source_cache.py`

- [ ] **Step 1: Write tests for cache paths and default policy**

Add `tests/test_source_cache.py` with these initial tests:

```python
from __future__ import annotations

from pathlib import Path

from sv.config import SvPaths
from sv.source_cache import (
    CacheMode,
    CachePolicy,
    DEFAULT_METADATA_TTL_SECONDS,
    DEFAULT_SKILL_BODY_MAX_BYTES,
    DEFAULT_SKILL_BODY_MAX_UNUSED_SECONDS,
)


def test_sv_paths_expose_global_cache_paths(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)

    assert paths.cache_dir == tmp_path / ".sv" / "cache" / "v1"
    assert paths.catalog_cache_dir == paths.cache_dir / "catalog"
    assert paths.skill_body_cache_dir == paths.cache_dir / "skills"
    assert paths.cache_tmp_dir == paths.cache_dir / "tmp"
    assert paths.cache_prune_marker == paths.cache_dir / "last-prune.toml"


def test_default_cache_policy_matches_product_defaults() -> None:
    policy = CachePolicy.default()

    assert policy.mode is CacheMode.NORMAL
    assert policy.metadata_ttl_seconds == DEFAULT_METADATA_TTL_SECONDS
    assert policy.metadata_ttl_seconds == 24 * 60 * 60
    assert policy.allow_stale_on_error is True
    assert DEFAULT_SKILL_BODY_MAX_UNUSED_SECONDS == 30 * 24 * 60 * 60
    assert DEFAULT_SKILL_BODY_MAX_BYTES == 256 * 1024 * 1024
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
uv run pytest tests/test_source_cache.py -q --no-cov
```

Expected: failures because `sv.source_cache` and cache path properties do not exist.

- [ ] **Step 3: Add cache path properties to `SvPaths`**

In `src/sv/config.py`, add these properties to the existing `SvPaths` class:

```python
    @property
    def cache_dir(self) -> Path:
        return self.sv_home / "cache" / "v1"

    @property
    def catalog_cache_dir(self) -> Path:
        return self.cache_dir / "catalog"

    @property
    def skill_body_cache_dir(self) -> Path:
        return self.cache_dir / "skills"

    @property
    def cache_tmp_dir(self) -> Path:
        return self.cache_dir / "tmp"

    @property
    def cache_prune_marker(self) -> Path:
        return self.cache_dir / "last-prune.toml"
```

- [ ] **Step 4: Create cache policy primitives**

Create `src/sv/source_cache.py` with this starting content:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

DEFAULT_METADATA_TTL_SECONDS = 24 * 60 * 60
DEFAULT_SKILL_BODY_MAX_UNUSED_SECONDS = 30 * 24 * 60 * 60
DEFAULT_SKILL_BODY_MAX_BYTES = 256 * 1024 * 1024
CACHE_SCHEMA_VERSION = 1


class CacheMode(Enum):
    NORMAL = "normal"
    FORCE_REFRESH = "force-refresh"
    CACHE_ONLY = "cache-only"


@dataclass(frozen=True)
class CachePolicy:
    mode: CacheMode = CacheMode.NORMAL
    metadata_ttl_seconds: int = DEFAULT_METADATA_TTL_SECONDS
    allow_stale_on_error: bool = True

    @classmethod
    def default(cls) -> "CachePolicy":
        return cls()

    @classmethod
    def force_refresh(cls, *, allow_stale_on_error: bool = True) -> "CachePolicy":
        return cls(
            mode=CacheMode.FORCE_REFRESH,
            allow_stale_on_error=allow_stale_on_error,
        )

    @classmethod
    def cache_only(cls) -> "CachePolicy":
        return cls(mode=CacheMode.CACHE_ONLY, allow_stale_on_error=False)
```

- [ ] **Step 5: Run the focused tests**

Run:

```bash
uv run pytest tests/test_source_cache.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/sv/config.py src/sv/source_cache.py tests/test_source_cache.py
git commit -m "feat: add global cache paths and policy"
```

---

### Task 2: Implement catalog metadata cache serialization

**Files:**
- Modify: `src/sv/source_cache.py`
- Test: `tests/test_source_cache.py`

- [ ] **Step 1: Add tests for catalog cache save/load and freshness**

Append these tests to `tests/test_source_cache.py`:

```python
from datetime import UTC, datetime, timedelta
import os

import pytest

from sv.catalog import SourceSkill
from sv.config import RepoConfig
from sv.errors import SvError
from sv.source_cache import (
    CachedCatalogEntry,
    CachedCatalogDocument,
    catalog_cache_path,
    cached_catalog_is_fresh,
    load_cached_catalog,
    save_cached_catalog,
)


def _repo() -> RepoConfig:
    return RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")


def _catalog_document(refreshed_at: str) -> CachedCatalogDocument:
    return CachedCatalogDocument(
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        source_key="github:org/skills",
        skills_paths=(),
        backend="github-https-api",
        refreshed_at=refreshed_at,
        catalog_hash="sha256:b7e19923e6ccb927a2135016aee79cbb3c0d851b45bf53850669c2507ee20b0e",
        index_hash="sha256:a51a6c19a1ffc7416827e89adf20749d23ad42452c396cf7e627409f2896922c",
        entries=(
            CachedCatalogEntry(
                name="find-docs",
                description="Find documentation.",
                source_path="skills/find-docs",
                content_hash="sha256:25bf8e1a2393f1108d37029b3df5593236c755742ec93465bbafa9b290bddcf6",
                skill_file_hash="sha256:c651ccb96b0c0e490de4cc12b9b46d643e6dba87840fab27e2c8d4d5cc2037fa",
            ),
        ),
    )


def test_catalog_cache_round_trips_metadata(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    document = _catalog_document("2026-05-18T12:00:00Z")

    save_cached_catalog(paths, _repo(), document)

    assert catalog_cache_path(paths, _repo()).is_file()
    assert load_cached_catalog(paths, _repo()) == document


def test_cached_catalog_freshness_uses_ttl() -> None:
    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    fresh = _catalog_document("2026-05-18T11:30:00Z")
    stale = _catalog_document("2026-05-17T11:00:00Z")

    assert cached_catalog_is_fresh(fresh, now=now, ttl_seconds=24 * 60 * 60) is True
    assert cached_catalog_is_fresh(stale, now=now, ttl_seconds=24 * 60 * 60) is False


def test_catalog_cache_write_refuses_symlinked_temp_file(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    path = catalog_cache_path(paths, repo)
    path.parent.mkdir(parents=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    target = tmp_path / "attacker-target.toml"
    temp_path.symlink_to(target)

    with pytest.raises(SvError, match="symlinked"):
        save_cached_catalog(paths, repo, _catalog_document("2026-05-18T12:00:00Z"))

    assert not target.exists()


def test_catalog_cache_load_rejects_catalog_hash_mismatch(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    save_cached_catalog(paths, repo, _catalog_document("2026-05-18T12:00:00Z"))
    path = catalog_cache_path(paths, repo)
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            "sha256:b7e19923e6ccb927a2135016aee79cbb3c0d851b45bf53850669c2507ee20b0e",
            "sha256:41cf6794ba4200b839c53531555f0f3998df4cbb01a4d5cb0b94e3ca5e23947d",
        ),
        encoding="utf-8",
    )

    with pytest.raises(SvError, match="catalog_hash does not match entries"):
        load_cached_catalog(paths, repo)
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
uv run pytest tests/test_source_cache.py -q --no-cov
```

Expected: failures for missing catalog cache symbols.

- [ ] **Step 3: Add catalog cache dataclasses and path helpers**

In `src/sv/source_cache.py`, add imports and dataclasses:

```python
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import hashlib

from sv.catalog import normalize_source_relative_path
from sv.config import RepoConfig, SvPaths, repo_source_key
from sv.errors import SvError
from sv.hashformat import is_sha256_digest
from sv.project import normalize_skill_name
from sv.terminal import escape_terminal_controls
from sv.tomlutil import atomic_write_text, load_toml_document, toml_escape

_MAX_CATALOG_CACHE_BYTES = 1 * 1024 * 1024


@dataclass(frozen=True)
class CachedCatalogEntry:
    name: str
    description: str
    source_path: str
    content_hash: str | None = None
    skill_file_hash: str | None = None


@dataclass(frozen=True)
class CachedCatalogDocument:
    repo_id: str
    repo_url: str
    source_key: str
    skills_paths: tuple[str, ...]
    backend: str
    refreshed_at: str
    catalog_hash: str
    index_hash: str | None
    entries: tuple[CachedCatalogEntry, ...]
```

Add path helpers:

```python
def _cache_file_key(repo: RepoConfig) -> str:
    digest = hashlib.sha256()
    digest.update(repo_source_key(repo.url).encode("utf-8"))
    digest.update(b"\0")
    digest.update(repo.id.encode("utf-8"))
    digest.update(b"\0")
    for skills_path in repo.skills_paths:
        digest.update(skills_path.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def catalog_cache_path(paths: SvPaths, repo: RepoConfig) -> Path:
    return paths.catalog_cache_dir / f"{_cache_file_key(repo)}.toml"
```

- [ ] **Step 4: Add safe catalog cache loading**

Add this load function to `src/sv/source_cache.py`:

```python
def load_cached_catalog(paths: SvPaths, repo: RepoConfig) -> CachedCatalogDocument | None:
    path = catalog_cache_path(paths, repo)
    _reject_symlinked_cache_dir(path.parent)
    if path.is_symlink():
        raise SvError(f"Refusing to read symlinked sv catalog cache at {path}.")
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > _MAX_CATALOG_CACHE_BYTES:
            raise SvError(f"sv catalog cache at {path} exceeds size limit.")
    except OSError as exc:
        raise SvError(f"Failed to inspect sv catalog cache at {path}: {exc}") from exc
    data = load_toml_document(path, "sv catalog cache")
    document = _parse_cached_catalog_document(data, path)
    _validate_cached_catalog_matches_repo(document, repo, path)
    if document.catalog_hash != _cached_catalog_hash(document.entries):
        raise SvError(f"Failed to read sv catalog cache at {path}: catalog_hash does not match entries.")
    return document


def _validate_cached_catalog_matches_repo(
    document: CachedCatalogDocument, repo: RepoConfig, path: Path
) -> None:
    if document.repo_id != repo.id:
        raise SvError(f"Failed to read sv catalog cache at {path}: repo_id does not match configured repo.")
    if document.repo_url != repo.url:
        raise SvError(f"Failed to read sv catalog cache at {path}: repo_url does not match configured repo.")
    if document.source_key != repo_source_key(repo.url):
        raise SvError(f"Failed to read sv catalog cache at {path}: source_key does not match configured repo.")
    if document.skills_paths != tuple(repo.skills_paths):
        raise SvError(f"Failed to read sv catalog cache at {path}: skills_paths do not match configured repo.")


def _parse_cached_catalog_document(data: object, path: Path) -> CachedCatalogDocument:
    if not isinstance(data, dict):
        raise SvError(f"Failed to read sv catalog cache at {path}: document must be a table.")
    if data.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise SvError(f"Failed to read sv catalog cache at {path}: unsupported schema_version.")
    entries_value = data.get("skills", [])
    if not isinstance(entries_value, list):
        raise SvError(f"Failed to read sv catalog cache at {path}: skills must be a list.")
    entries = tuple(_parse_cached_catalog_entry(item, path, index) for index, item in enumerate(entries_value, start=1))
    document = CachedCatalogDocument(
        repo_id=_required_string(data, "repo_id", path),
        repo_url=_required_string(data, "repo_url", path),
        source_key=_required_string(data, "source_key", path),
        skills_paths=tuple(_string_list(data.get("skills_paths", []), "skills_paths", path)),
        backend=_required_string(data, "backend", path),
        refreshed_at=_required_string(data, "refreshed_at", path),
        catalog_hash=_required_hash(data, "catalog_hash", path),
        index_hash=_optional_hash(data, "index_hash", path),
        entries=entries,
    )
    _parse_utc(document.refreshed_at, path)
    return document
```

Add the small validation helpers used above:

```python
def _parse_cached_catalog_entry(item: object, path: Path, index: int) -> CachedCatalogEntry:
    if not isinstance(item, dict):
        raise SvError(f"Failed to read sv catalog cache at {path}: skill entry {index} must be a table.")
    raw_name = _required_string(item, "name", path)
    try:
        name = normalize_skill_name(raw_name)
    except SvError as exc:
        raise SvError(f"Failed to read sv catalog cache at {path}: invalid skill name in entry {index}: {exc}") from exc
    raw_source_path = _required_string(item, "source_path", path)
    try:
        source_path = normalize_source_relative_path(raw_source_path)
    except SvError as exc:
        raise SvError(f"Failed to read sv catalog cache at {path}: invalid source_path in entry {index}: {exc}") from exc
    return CachedCatalogEntry(
        name=name,
        description=escape_terminal_controls(_required_string(item, "description", path)),
        source_path=source_path,
        content_hash=_optional_hash(item, "content_hash", path),
        skill_file_hash=_optional_hash(item, "skill_file_hash", path),
    )


def _required_string(data: dict, field: str, path: Path) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise SvError(f"Failed to read sv catalog cache at {path}: field '{field}' must be a non-empty string.")
    return value


def _string_list(value: object, field: str, path: Path) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SvError(f"Failed to read sv catalog cache at {path}: field '{field}' must be a list of strings.")
    return list(value)


def _required_hash(data: dict, field: str, path: Path) -> str:
    value = _required_string(data, field, path)
    if not is_sha256_digest(value):
        raise SvError(f"Failed to read sv catalog cache at {path}: field '{field}' must be a sha256 digest.")
    return value


def _optional_hash(data: dict, field: str, path: Path) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not is_sha256_digest(value):
        raise SvError(f"Failed to read sv catalog cache at {path}: field '{field}' must be a sha256 digest.")
    return value


def _parse_utc(value: str, path: Path) -> datetime:
    if not value.endswith("Z"):
        raise SvError(f"Failed to read sv catalog cache at {path}: timestamp must end with Z.")
    try:
        return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise SvError(f"Failed to read sv catalog cache at {path}: timestamp is invalid.") from exc
```

- [ ] **Step 5: Add atomic catalog cache saving and freshness check**

Add these functions:

```python
def save_cached_catalog(paths: SvPaths, repo: RepoConfig, document: CachedCatalogDocument) -> None:
    path = catalog_cache_path(paths, repo)
    _ensure_private_cache_dir(path.parent)
    if (
        document.repo_id != repo.id
        or document.repo_url != repo.url
        or document.source_key != repo_source_key(repo.url)
        or document.skills_paths != tuple(repo.skills_paths)
    ):
        raise SvError("Refusing to write sv catalog cache for a different repo.")
    if document.catalog_hash != _cached_catalog_hash(document.entries):
        raise SvError("Refusing to write sv catalog cache with mismatched catalog_hash.")
    atomic_write_text(
        path,
        _format_cached_catalog_document(document),
        document_name="sv catalog cache",
        temp_path_description="sv catalog cache temp file",
    )


def cached_catalog_is_fresh(document: CachedCatalogDocument, *, now: datetime, ttl_seconds: int) -> bool:
    refreshed_at = _parse_utc(document.refreshed_at, Path("sv-catalog-cache"))
    return now - refreshed_at <= timedelta(seconds=ttl_seconds)
```

Add imports:

```python
from datetime import UTC, datetime, timedelta
```

Add formatter helpers:

```python
def _format_cached_catalog_document(document: CachedCatalogDocument) -> str:
    lines = [
        f"schema_version = {CACHE_SCHEMA_VERSION}",
        f"repo_id = {_toml_string(document.repo_id)}",
        f"repo_url = {_toml_string(document.repo_url)}",
        f"source_key = {_toml_string(document.source_key)}",
        f"skills_paths = [{', '.join(_toml_string(value) for value in document.skills_paths)}]",
        f"backend = {_toml_string(document.backend)}",
        f"refreshed_at = {_toml_string(document.refreshed_at)}",
        f"catalog_hash = {_toml_string(document.catalog_hash)}",
    ]
    if document.index_hash is not None:
        lines.append(f"index_hash = {_toml_string(document.index_hash)}")
    for entry in document.entries:
        lines.extend([
            "",
            "[[skills]]",
            f"name = {_toml_string(entry.name)}",
            f"description = {_toml_string(entry.description)}",
            f"source_path = {_toml_string(entry.source_path)}",
        ])
        if entry.content_hash is not None:
            lines.append(f"content_hash = {_toml_string(entry.content_hash)}")
        if entry.skill_file_hash is not None:
            lines.append(f"skill_file_hash = {_toml_string(entry.skill_file_hash)}")
    return "\n".join(lines) + "\n"


def _cached_catalog_hash(entries: Sequence[CachedCatalogEntry]) -> str:
    digest = hashlib.sha256()
    digest.update(b"sv-cached-catalog-v1\0")
    for entry in entries:
        for value in (entry.name, entry.description, entry.source_path, entry.content_hash or "", entry.skill_file_hash or ""):
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _toml_string(value: str) -> str:
    return f'"{toml_escape(value)}"'


def _ensure_private_cache_dir(path: Path) -> None:
    _reject_symlinked_cache_dir(path)
    try:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.chmod(0o700)
    except OSError as exc:
        raise SvError(f"Failed to prepare sv cache directory at {path}: {exc}") from exc
    _reject_symlinked_cache_dir(path)


def _reject_symlinked_cache_dir(path: Path) -> None:
    for candidate in (*reversed(path.parents), path):
        if candidate.is_symlink():
            raise SvError(f"Refusing to use symlinked sv cache directory at {candidate}.")
```

- [ ] **Step 6: Run the focused tests**

Run:

```bash
uv run pytest tests/test_source_cache.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```bash
git add src/sv/source_cache.py tests/test_source_cache.py
git commit -m "feat: persist source catalog metadata cache"
```

---

### Task 3: Add cache-aware source catalog provider

**Files:**
- Modify: `src/sv/source_cache.py`
- Test: `tests/test_source_cache.py`

- [ ] **Step 1: Write tests for TTL, forced refresh, cache-only, and stale fallback**

Append these tests:

```python
from collections.abc import Sequence

from sv.source_cache import get_catalog_with_cache


def test_get_catalog_with_cache_uses_fresh_metadata_without_refresh(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    save_cached_catalog(paths, repo, _catalog_document("2026-05-18T11:30:00Z"))
    calls: list[Sequence[RepoConfig]] = []

    def refresh(repos: Sequence[RepoConfig]) -> list[SourceSkill]:
        calls.append(repos)
        raise AssertionError("fresh cache should avoid refresh")

    catalog = get_catalog_with_cache(
        [repo],
        paths,
        policy=CachePolicy.default(),
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        refresh_catalog=refresh,
        warn=lambda message: None,
    )

    assert calls == []
    assert [(entry.name, entry.description) for entry in catalog] == [("find-docs", "Find documentation.")]


def test_get_catalog_with_cache_refreshes_expired_metadata(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    save_cached_catalog(paths, repo, _catalog_document("2026-05-17T11:00:00Z"))
    refreshed_entry = SourceSkill(
        name="new-skill",
        description="New skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=paths.source_repo_for(repo.id),
        source_path=paths.source_repo_for(repo.id) / "skills" / "new-skill",
        source_relative_path="skills/new-skill",
        source_backend="fake",
        source_content_hash="sha256:41cf6794ba4200b839c53531555f0f3998df4cbb01a4d5cb0b94e3ca5e23947d",
    )

    def refresh(repos: Sequence[RepoConfig]) -> list[SourceSkill]:
        assert list(repos) == [repo]
        return [refreshed_entry]

    catalog = get_catalog_with_cache(
        [repo],
        paths,
        policy=CachePolicy.default(),
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        refresh_catalog=refresh,
        warn=lambda message: None,
    )

    assert [entry.name for entry in catalog] == ["new-skill"]
    cached_document = load_cached_catalog(paths, repo)
    assert cached_document is not None
    assert cached_document.entries[0].name == "new-skill"


def test_get_catalog_with_cache_warns_and_uses_stale_metadata_when_refresh_fails(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    save_cached_catalog(paths, repo, _catalog_document("2026-05-17T11:00:00Z"))
    warnings: list[str] = []

    def refresh(repos: Sequence[RepoConfig]) -> list[SourceSkill]:
        raise SvError("network unavailable")

    catalog = get_catalog_with_cache(
        [repo],
        paths,
        policy=CachePolicy.default(),
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        refresh_catalog=refresh,
        warn=warnings.append,
    )

    assert [entry.name for entry in catalog] == ["find-docs"]
    assert warnings == ["warning: using stale cached metadata for Org/Skills; refresh failed: network unavailable"]


def test_get_catalog_with_cache_can_fail_closed_when_refresh_fails(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    save_cached_catalog(paths, repo, _catalog_document("2026-05-17T11:00:00Z"))

    def refresh(repos: Sequence[RepoConfig]) -> list[SourceSkill]:
        raise SvError("network unavailable")

    with pytest.raises(SvError, match="network unavailable"):
        get_catalog_with_cache(
            [repo],
            paths,
            policy=CachePolicy.force_refresh(allow_stale_on_error=False),
            now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
            refresh_catalog=refresh,
            warn=lambda message: None,
        )


def test_get_catalog_with_cache_cache_only_fails_without_cached_metadata(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()

    def refresh(repos: Sequence[RepoConfig]) -> list[SourceSkill]:
        raise AssertionError("cache-only mode must not refresh")

    with pytest.raises(SvError, match="No cached metadata found for Org/Skills"):
        get_catalog_with_cache(
            [repo],
            paths,
            policy=CachePolicy.cache_only(),
            now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
            refresh_catalog=refresh,
            warn=lambda message: None,
        )


def test_get_catalog_with_cache_refreshes_when_normal_mode_cache_is_corrupt(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    catalog_cache_path(paths, repo).parent.mkdir(parents=True)
    catalog_cache_path(paths, repo).write_text("not = [valid", encoding="utf-8")
    warnings: list[str] = []
    refreshed_entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=paths.source_repo_for(repo.id),
        source_path=paths.source_repo_for(repo.id) / "skills" / "alpha",
        source_relative_path="skills/alpha",
        source_backend="fake",
    )

    def refresh(repos: Sequence[RepoConfig]) -> list[SourceSkill]:
        assert list(repos) == [repo]
        return [refreshed_entry]

    catalog = get_catalog_with_cache(
        [repo],
        paths,
        policy=CachePolicy.default(),
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        refresh_catalog=refresh,
        warn=warnings.append,
    )

    assert [entry.name for entry in catalog] == ["alpha"]
    assert len(warnings) == 1
    assert warnings[0].startswith("warning: ignoring invalid cached metadata for Org/Skills: Failed to read sv catalog cache")


def test_get_catalog_with_cache_cache_only_fails_when_cache_is_corrupt(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    catalog_cache_path(paths, repo).parent.mkdir(parents=True)
    catalog_cache_path(paths, repo).write_text("not = [valid", encoding="utf-8")

    def refresh(repos: Sequence[RepoConfig]) -> list[SourceSkill]:
        raise AssertionError("cache-only mode must not refresh")

    with pytest.raises(SvError, match="Failed to read sv catalog cache"):
        get_catalog_with_cache(
            [repo],
            paths,
            policy=CachePolicy.cache_only(),
            now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
            refresh_catalog=refresh,
            warn=lambda message: None,
        )
```

`pytest` was imported in Task 2; keep that import at the top of the file when adding these tests.

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
uv run pytest tests/test_source_cache.py -q --no-cov
```

Expected: failures for missing `get_catalog_with_cache` and conversion helpers.

- [ ] **Step 3: Add catalog conversion helpers**

In `src/sv/source_cache.py`, add imports, merging with the existing `sv.catalog` import from Task 2:

```python
from collections.abc import Callable, Mapping, Sequence
from dataclasses import field, replace

from sv.catalog import SourceSkill, normalize_source_relative_path
```

Add conversion functions:

```python
def _catalog_document_from_entries(
    repo: RepoConfig,
    entries: Sequence[SourceSkill],
    *,
    refreshed_at: str,
    backend: str | None = None,
    index_hash: str | None = None,
) -> CachedCatalogDocument:
    cached_entries = tuple(
        CachedCatalogEntry(
            name=entry.name,
            description=entry.description,
            source_path=entry.source_relative_path,
            content_hash=entry.source_content_hash,
            skill_file_hash=entry.source_skill_file_hash,
        )
        for entry in sorted(entries, key=lambda item: (item.name, item.source_relative_path))
    )
    return CachedCatalogDocument(
        repo_id=repo.id,
        repo_url=repo.url,
        source_key=repo_source_key(repo.url),
        skills_paths=tuple(repo.skills_paths),
        backend=backend or _catalog_backend(entries),
        refreshed_at=refreshed_at,
        catalog_hash=_cached_catalog_hash(cached_entries),
        index_hash=index_hash,
        entries=cached_entries,
    )


def _catalog_backend(entries: Sequence[SourceSkill]) -> str:
    backends = sorted({entry.source_backend for entry in entries})
    return backends[0] if len(backends) == 1 else "mixed"


# `_cached_catalog_hash` was added in Task 2 and is reused here for refreshed catalog writes.


def _source_skills_from_cached_document(
    repo: RepoConfig,
    paths: SvPaths,
    document: CachedCatalogDocument,
) -> list[SourceSkill]:
    repo_path = paths.source_repo_for(repo.id)
    entries: list[SourceSkill] = []
    for entry in document.entries:
        source_relative_path = normalize_source_relative_path(entry.source_path)
        entries.append(
            SourceSkill(
                name=normalize_skill_name(entry.name),
                description=entry.description,
                repo_id=repo.id,
                repo_url=repo.url,
                repo_path=repo_path,
                source_path=repo_path / Path(*source_relative_path.split("/")),
                source_relative_path=source_relative_path,
                repo_aliases=repo.aliases,
                source_backend=f"cache:{document.backend}",
                source_content_hash=entry.content_hash,
                source_skill_file_hash=entry.skill_file_hash,
            )
        )
    return entries
```

- [ ] **Step 4: Add `get_catalog_with_cache`**

Add this public function:

```python
@dataclass(frozen=True)
class CacheRefreshResult:
    entries: tuple[SourceSkill, ...]
    refreshed_backends_by_repo: Mapping[str, str] = field(default_factory=dict)
    index_hashes_by_repo: Mapping[str, str | None] = field(default_factory=dict)


CacheRefreshValue = CacheRefreshResult | Sequence[SourceSkill]
RefreshCatalog = Callable[[Sequence[RepoConfig]], CacheRefreshValue]
Warn = Callable[[str], None]


def get_catalog_with_cache(
    repos: Sequence[RepoConfig],
    paths: SvPaths,
    *,
    policy: CachePolicy,
    now: datetime,
    refresh_catalog: RefreshCatalog,
    warn: Warn,
) -> list[SourceSkill]:
    cached: dict[str, CachedCatalogDocument] = {}
    repos_to_refresh: list[RepoConfig] = []
    result_entries: list[SourceSkill] = []

    for repo in repos:
        try:
            document = load_cached_catalog(paths, repo)
        except SvError as exc:
            if policy.mode is CacheMode.CACHE_ONLY:
                raise
            warn(f"warning: ignoring invalid cached metadata for {repo.id}: {exc}")
            document = None
        if document is not None:
            cached[repo.id] = document
        if policy.mode is CacheMode.CACHE_ONLY:
            if document is None:
                raise SvError(f"No cached metadata found for {repo.id}.")
            result_entries.extend(_source_skills_from_cached_document(repo, paths, document))
            continue
        if policy.mode is CacheMode.FORCE_REFRESH or document is None:
            repos_to_refresh.append(repo)
            continue
        if cached_catalog_is_fresh(document, now=now, ttl_seconds=policy.metadata_ttl_seconds):
            result_entries.extend(_source_skills_from_cached_document(repo, paths, document))
        else:
            repos_to_refresh.append(repo)

    if repos_to_refresh:
        refreshed_at = _utc_timestamp(now)
        try:
            refresh_result = _coerce_refresh_result(refresh_catalog(repos_to_refresh))
            refreshed_entries = list(refresh_result.entries)
        except SvError as exc:
            if not policy.allow_stale_on_error:
                raise
            stale_entries: list[SourceSkill] = []
            missing_repos: list[str] = []
            for repo in repos_to_refresh:
                document = cached.get(repo.id)
                if document is None:
                    missing_repos.append(repo.id)
                    continue
                warn(f"warning: using stale cached metadata for {repo.id}; refresh failed: {exc}")
                stale_entries.extend(_source_skills_from_cached_document(repo, paths, document))
            if missing_repos:
                raise
            result_entries.extend(stale_entries)
        else:
            entries_by_repo = _entries_by_repo(refreshed_entries)
            for repo in repos_to_refresh:
                repo_entries = entries_by_repo.get(repo.id, [])
                document = _catalog_document_from_entries(
                    repo,
                    repo_entries,
                    refreshed_at=refreshed_at,
                    backend=refresh_result.refreshed_backends_by_repo.get(repo.id),
                    index_hash=refresh_result.index_hashes_by_repo.get(repo.id),
                )
                save_cached_catalog(paths, repo, document)
                result_entries.extend(repo_entries)

    return sorted(result_entries, key=lambda entry: (entry.name, entry.repo_id, entry.source_relative_path))


def _coerce_refresh_result(value: CacheRefreshValue) -> CacheRefreshResult:
    if isinstance(value, CacheRefreshResult):
        return value
    return CacheRefreshResult(entries=tuple(value))


def _entries_by_repo(entries: Sequence[SourceSkill]) -> dict[str, list[SourceSkill]]:
    grouped: dict[str, list[SourceSkill]] = {}
    for entry in entries:
        grouped.setdefault(entry.repo_id, []).append(entry)
    return grouped


def _utc_timestamp(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
```

- [ ] **Step 5: Run the focused tests**

Run:

```bash
uv run pytest tests/test_source_cache.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/sv/source_cache.py tests/test_source_cache.py
git commit -m "feat: add cache-aware source catalog provider"
```

---

### Task 4: Add full skill body cache

**Files:**
- Modify: `src/sv/source_cache.py`
- Test: `tests/test_source_cache.py`

- [ ] **Step 1: Write tests for body cache store, hit, validation, and touch**

Append tests:

```python
from sv.hashing import sha256_skill_directory
from sv.source_cache import (
    load_skill_body_metadata,
    skill_body_cache_path,
    store_skill_body_cache,
    try_materialize_from_skill_body_cache,
)


def _write_skill_tree(root: Path, name: str, body: str = "body\n") -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Cached skill.\n---\n", encoding="utf-8")
    (skill / "notes.md").write_text(body, encoding="utf-8")
    return skill


def test_skill_body_cache_stores_and_materializes_valid_skill(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")

    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
    )

    destination = tmp_path / "destination" / "alpha"
    assert try_materialize_from_skill_body_cache(
        paths,
        content_hash=content_hash,
        skill_name="alpha",
        destination=destination,
        now=datetime(2026, 5, 18, 13, 0, tzinfo=UTC),
    ) is True
    assert (destination / "notes.md").read_text(encoding="utf-8") == "body\n"
    metadata = load_skill_body_metadata(paths, content_hash)
    assert metadata.use_count == 2
    assert metadata.last_used_at == "2026-05-18T13:00:00Z"


def test_skill_body_cache_miss_returns_false(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    destination = tmp_path / "destination" / "alpha"

    assert try_materialize_from_skill_body_cache(
        paths,
        content_hash="sha256:41cf6794ba4200b839c53531555f0f3998df4cbb01a4d5cb0b94e3ca5e23947d",
        skill_name="alpha",
        destination=destination,
        now=datetime(2026, 5, 18, 13, 0, tzinfo=UTC),
    ) is False
    assert not destination.exists()


def test_store_skill_body_cache_refuses_symlinked_tmp_dir(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    attacker_target = tmp_path / "attacker-cache-tmp"
    attacker_target.mkdir()
    paths.cache_dir.mkdir(parents=True)
    paths.cache_tmp_dir.symlink_to(attacker_target, target_is_directory=True)

    with pytest.raises(SvError, match="symlinked"):
        store_skill_body_cache(
            paths,
            source_skill,
            skill_name="alpha",
            content_hash=content_hash,
            source_reference="Org/Skills:skills/alpha",
            now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        )

    assert list(attacker_target.iterdir()) == []


def test_store_skill_body_cache_refuses_symlinked_body_root(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    attacker_target = tmp_path / "attacker-body-root"
    attacker_target.mkdir()
    paths.skill_body_cache_dir.mkdir(parents=True)
    (paths.skill_body_cache_dir / "sha256").symlink_to(attacker_target, target_is_directory=True)

    with pytest.raises(SvError, match="symlinked"):
        store_skill_body_cache(
            paths,
            source_skill,
            skill_name="alpha",
            content_hash=content_hash,
            source_reference="Org/Skills:skills/alpha",
            now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        )

    assert list(attacker_target.iterdir()) == []


def test_skill_body_cache_metadata_hash_mismatch_is_treated_as_miss(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
    )
    metadata_path = skill_body_cache_path(paths, content_hash) / "metadata.toml"
    text = metadata_path.read_text(encoding="utf-8")
    wrong_hash = (
        "sha256:41cf6794ba4200b839c53531555f0f3998df4cbb01a4d5cb0b94e3ca5e23947d"
    )
    metadata_path.write_text(
        text.replace(content_hash, wrong_hash),
        encoding="utf-8",
    )
    destination = tmp_path / "destination" / "alpha"

    assert try_materialize_from_skill_body_cache(
        paths,
        content_hash=content_hash,
        skill_name="alpha",
        destination=destination,
        now=datetime(2026, 5, 18, 13, 0, tzinfo=UTC),
    ) is False
    assert not destination.exists()
    assert not skill_body_cache_path(paths, content_hash).exists()


def test_skill_body_cache_corrupt_metadata_is_treated_as_miss(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
    )
    metadata_path = skill_body_cache_path(paths, content_hash) / "metadata.toml"
    metadata_path.write_text("not = [valid", encoding="utf-8")
    destination = tmp_path / "destination" / "alpha"

    assert try_materialize_from_skill_body_cache(
        paths,
        content_hash=content_hash,
        skill_name="alpha",
        destination=destination,
        now=datetime(2026, 5, 18, 13, 0, tzinfo=UTC),
    ) is False
    assert not destination.exists()
    assert not skill_body_cache_path(paths, content_hash).exists()


def test_skill_body_cache_corrupt_body_is_treated_as_miss(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
    )
    (skill_body_cache_path(paths, content_hash) / "skill" / "notes.md").write_text(
        "tampered\n", encoding="utf-8"
    )
    destination = tmp_path / "destination" / "alpha"

    assert try_materialize_from_skill_body_cache(
        paths,
        content_hash=content_hash,
        skill_name="alpha",
        destination=destination,
        now=datetime(2026, 5, 18, 13, 0, tzinfo=UTC),
    ) is False
    assert not destination.exists()
    assert not skill_body_cache_path(paths, content_hash).exists()


def test_store_skill_body_cache_replaces_corrupt_existing_metadata(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
    )
    metadata_path = skill_body_cache_path(paths, content_hash) / "metadata.toml"
    metadata_path.write_text("not = [valid", encoding="utf-8")

    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 13, 0, tzinfo=UTC),
    )

    metadata = load_skill_body_metadata(paths, content_hash)
    assert metadata.last_used_at == "2026-05-18T13:00:00Z"
    assert metadata.use_count == 1
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_source_cache.py -q --no-cov
```

Expected: failures for missing body cache functions.

- [ ] **Step 3: Add skill body metadata dataclass and paths**

In `src/sv/source_cache.py`, add imports:

```python
import shutil
import uuid

from sv.hashformat import SHA256_PREFIX
from sv.hashing import sha256_file, sha256_skill_directory
from sv.materialization import remove_materialization_path
from sv.skills import parse_skill_file
```

Add dataclass and path helpers:

```python
@dataclass(frozen=True)
class SkillBodyMetadata:
    content_hash: str
    skill_name: str
    source_reference: str
    size_bytes: int
    created_at: str
    last_used_at: str
    use_count: int


def skill_body_cache_path(paths: SvPaths, content_hash: str) -> Path:
    if not is_sha256_digest(content_hash):
        raise SvError("Skill body cache content hash must be a sha256 digest.")
    digest = content_hash.removeprefix(SHA256_PREFIX)
    return paths.skill_body_cache_dir / "sha256" / digest


def _skill_body_folder(paths: SvPaths, content_hash: str) -> Path:
    return skill_body_cache_path(paths, content_hash) / "skill"


def _skill_body_metadata_path(paths: SvPaths, content_hash: str) -> Path:
    return skill_body_cache_path(paths, content_hash) / "metadata.toml"
```

- [ ] **Step 4: Add metadata load/save for body cache**

Add:

```python
def load_skill_body_metadata(paths: SvPaths, content_hash: str) -> SkillBodyMetadata:
    path = _skill_body_metadata_path(paths, content_hash)
    _reject_symlinked_cache_dir(path.parent)
    if path.is_symlink():
        raise SvError(f"Refusing to read symlinked skill body cache metadata at {path}.")
    data = load_toml_document(path, "sv skill body cache metadata")
    if not isinstance(data, dict) or data.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise SvError(f"Failed to read sv skill body cache metadata at {path}: unsupported schema_version.")
    metadata = SkillBodyMetadata(
        content_hash=_required_hash(data, "content_hash", path),
        skill_name=normalize_skill_name(_required_string(data, "skill_name", path)),
        source_reference=_required_string(data, "source_reference", path),
        size_bytes=_required_int(data, "size_bytes", path),
        created_at=_required_string(data, "created_at", path),
        last_used_at=_required_string(data, "last_used_at", path),
        use_count=_required_int(data, "use_count", path),
    )
    if metadata.content_hash != content_hash:
        raise SvError(
            f"Failed to read sv skill body cache metadata at {path}: content_hash does not match cache path."
        )
    _parse_utc(metadata.created_at, path)
    _parse_utc(metadata.last_used_at, path)
    return metadata


def _required_int(data: dict, field: str, path: Path) -> int:
    value = data.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SvError(f"Failed to read sv cache metadata at {path}: field '{field}' must be a non-negative integer.")
    return value


def _save_skill_body_metadata_at(path: Path, metadata: SkillBodyMetadata) -> None:
    _ensure_private_cache_dir(path.parent)
    text = "\n".join([
        f"schema_version = {CACHE_SCHEMA_VERSION}",
        f"content_hash = {_toml_string(metadata.content_hash)}",
        f"skill_name = {_toml_string(metadata.skill_name)}",
        f"source_reference = {_toml_string(metadata.source_reference)}",
        f"size_bytes = {metadata.size_bytes}",
        f"created_at = {_toml_string(metadata.created_at)}",
        f"last_used_at = {_toml_string(metadata.last_used_at)}",
        f"use_count = {metadata.use_count}",
        "",
    ])
    atomic_write_text(
        path,
        text,
        document_name="sv skill body cache metadata",
        temp_path_description="sv skill body cache metadata temp file",
    )


def _save_skill_body_metadata(paths: SvPaths, metadata: SkillBodyMetadata) -> None:
    _save_skill_body_metadata_at(
        _skill_body_metadata_path(paths, metadata.content_hash),
        metadata,
    )
```

- [ ] **Step 5: Add store and materialize functions**

Add:

```python
def store_skill_body_cache(
    paths: SvPaths,
    source_skill_dir: Path,
    *,
    skill_name: str,
    content_hash: str,
    source_reference: str,
    now: datetime,
) -> None:
    skill_name = normalize_skill_name(skill_name)
    parse_skill_file(source_skill_dir / "SKILL.md", expected_folder=skill_name)
    if sha256_skill_directory(source_skill_dir, expected_name=skill_name) != content_hash:
        raise SvError(f"Refusing to cache skill '{skill_name}': content hash mismatch.")
    cache_root = skill_body_cache_path(paths, content_hash)
    _ensure_private_cache_dir(paths.cache_tmp_dir)
    _ensure_private_cache_dir(cache_root.parent)
    if cache_root.is_symlink():
        raise SvError(f"Refusing to use symlinked skill body cache path at {cache_root}.")
    if cache_root.exists():
        if _cached_skill_body_is_valid(paths, content_hash, skill_name=skill_name):
            _touch_skill_body_cache(paths, content_hash, now=now)
            return
        remove_materialization_path(cache_root, ignore_errors=True)

    temp_root = _unique_skill_body_temp_root(paths, content_hash)
    try:
        temp_skill = temp_root / "skill"
        temp_skill.parent.mkdir(parents=True, exist_ok=False)
        shutil.copytree(source_skill_dir, temp_skill, symlinks=True)
        parse_skill_file(temp_skill / "SKILL.md", expected_folder=skill_name)
        if sha256_skill_directory(temp_skill, expected_name=skill_name) != content_hash:
            raise SvError(f"Refusing to cache skill '{skill_name}': copied content hash mismatch.")
        timestamp = _utc_timestamp(now)
        metadata = SkillBodyMetadata(
            content_hash=content_hash,
            skill_name=skill_name,
            source_reference=source_reference,
            size_bytes=_directory_size(temp_skill),
            created_at=timestamp,
            last_used_at=timestamp,
            use_count=1,
        )
        _save_skill_body_metadata_at(temp_root / "metadata.toml", metadata)
        if sha256_skill_directory(temp_skill, expected_name=skill_name) != content_hash:
            raise SvError(f"Refusing to cache skill '{skill_name}': staged content hash mismatch.")
        temp_root.replace(cache_root)
    except Exception:
        remove_materialization_path(temp_root, ignore_errors=True)
        if cache_root.exists() and not _cached_skill_body_is_valid(paths, content_hash, skill_name=skill_name):
            remove_materialization_path(cache_root, ignore_errors=True)
        raise


def try_materialize_from_skill_body_cache(
    paths: SvPaths,
    *,
    content_hash: str,
    skill_name: str,
    destination: Path,
    now: datetime,
) -> bool:
    skill_name = normalize_skill_name(skill_name)
    if not _cached_skill_body_is_valid(paths, content_hash, skill_name=skill_name):
        return False
    skill_folder = _skill_body_folder(paths, content_hash)
    if destination.exists() or destination.is_symlink():
        remove_materialization_path(destination, ignore_errors=True)
    try:
        shutil.copytree(skill_folder, destination, symlinks=True)
        if sha256_skill_directory(destination, expected_name=skill_name) != content_hash:
            raise SvError(f"Cached skill '{skill_name}' failed validation after copy.")
        try:
            _touch_skill_body_cache(paths, content_hash, now=now)
        except SvError:
            remove_materialization_path(skill_body_cache_path(paths, content_hash), ignore_errors=True)
            remove_materialization_path(destination, ignore_errors=True)
            return False
    except Exception:
        remove_materialization_path(destination, ignore_errors=True)
        raise
    return True
```

Add helpers:

```python
def _touch_skill_body_cache(paths: SvPaths, content_hash: str, *, now: datetime) -> None:
    metadata = load_skill_body_metadata(paths, content_hash)
    _save_skill_body_metadata(
        paths,
        replace(
            metadata,
            last_used_at=_utc_timestamp(now),
            use_count=metadata.use_count + 1,
        ),
    )


def _cached_skill_body_is_valid(
    paths: SvPaths,
    content_hash: str,
    *,
    skill_name: str,
) -> bool:
    cache_root = skill_body_cache_path(paths, content_hash)
    skill_folder = _skill_body_folder(paths, content_hash)
    _reject_symlinked_cache_dir(cache_root.parent)
    if cache_root.is_symlink():
        raise SvError(f"Refusing to use symlinked skill body cache path at {cache_root}.")
    if skill_folder.is_symlink():
        raise SvError(f"Refusing to use symlinked cached skill body folder at {skill_folder}.")
    if not skill_folder.is_dir():
        if cache_root.exists():
            remove_materialization_path(cache_root, ignore_errors=True)
        return False
    try:
        metadata = load_skill_body_metadata(paths, content_hash)
        if metadata.skill_name != skill_name:
            raise SvError("cached skill body metadata skill_name mismatch")
        if sha256_skill_directory(skill_folder, expected_name=skill_name) != content_hash:
            raise SvError("cached skill body hash mismatch")
    except SvError:
        remove_materialization_path(cache_root, ignore_errors=True)
        return False
    return True


def _unique_skill_body_temp_root(paths: SvPaths, content_hash: str) -> Path:
    digest = content_hash.removeprefix(SHA256_PREFIX)
    for _ in range(100):
        candidate = paths.cache_tmp_dir / f"skill-{digest}-{uuid.uuid4().hex}"
        if candidate.exists() or candidate.is_symlink():
            continue
        return candidate
    raise SvError("Failed to allocate unique sv skill body cache temp path.")


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_symlink():
            raise SvError(f"Refusing to size symlinked cached skill path at {item}.")
        if item.is_file():
            total += item.stat().st_size
    return total
```

- [ ] **Step 6: Run focused cache tests**

Run:

```bash
uv run pytest tests/test_source_cache.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/sv/source_cache.py tests/test_source_cache.py
git commit -m "feat: cache materialized skill bodies globally"
```

---

### Task 5: Wrap catalog entries with cache-aware materializers

**Files:**
- Modify: `src/sv/source_cache.py`
- Test: `tests/test_source_cache.py`

- [ ] **Step 1: Write tests for wrapper hit and miss behavior**

Append:

```python
from dataclasses import replace

from sv.hashing import sha256_file
from sv.source import FakeSourceBackend
from sv.source_cache import (
    attach_source_materializers,
    record_cached_skill_body_hash,
    wrap_catalog_with_skill_body_cache,
)


def test_cache_aware_materializer_uses_body_cache_before_source(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    cached_skill = _write_skill_tree(tmp_path / "cache-source", "alpha", body="cached\n")
    content_hash = sha256_skill_directory(cached_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        cached_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
    )

    def fail_materializer(destination: Path) -> None:
        raise AssertionError("body cache hit should avoid source materializer")

    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=paths.source_repo_for("Org/Skills"),
        source_path=paths.source_repo_for("Org/Skills") / "skills" / "alpha",
        source_relative_path="skills/alpha",
        source_backend="fake",
        source_content_hash=content_hash,
        _materializer=fail_materializer,
    )

    [wrapped] = wrap_catalog_with_skill_body_cache(
        [entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, 0, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: None,
    )
    destination = tmp_path / "destination" / "alpha"
    wrapped.materialize_to(destination)

    assert (destination / "notes.md").read_text(encoding="utf-8") == "cached\n"


def test_cache_aware_materializer_stores_source_materialization_on_miss(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha", body="remote\n")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")

    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=tmp_path / "source",
        source_path=source_skill,
        source_relative_path="skills/alpha",
        source_backend="local-cache",
        source_content_hash=content_hash,
    )

    stored: list[tuple[SourceSkill, str, str]] = []
    [wrapped] = wrap_catalog_with_skill_body_cache(
        [entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, 0, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: stored.append((entry, content_hash, skill_file_hash)),
    )
    destination = tmp_path / "destination" / "alpha"
    wrapped.materialize_to(destination)

    assert stored == [(entry, content_hash, sha256_file(destination / "SKILL.md"))]
    assert (destination / "notes.md").read_text(encoding="utf-8") == "remote\n"
    cached_copy = tmp_path / "cached-copy" / "alpha"
    assert try_materialize_from_skill_body_cache(
        paths,
        content_hash=content_hash,
        skill_name="alpha",
        destination=cached_copy,
        now=datetime(2026, 5, 18, 14, 0, tzinfo=UTC),
    ) is True
    assert (cached_copy / "notes.md").read_text(encoding="utf-8") == "remote\n"


def test_cache_aware_materializer_removes_destination_on_hash_mismatch(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha", body="remote\n")
    wrong_hash = "sha256:41cf6794ba4200b839c53531555f0f3998df4cbb01a4d5cb0b94e3ca5e23947d"
    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=tmp_path / "source",
        source_path=source_skill,
        source_relative_path="skills/alpha",
        source_backend="local-cache",
        source_content_hash=wrong_hash,
    )
    [wrapped] = wrap_catalog_with_skill_body_cache(
        [entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, 0, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: None,
    )
    destination = tmp_path / "destination" / "alpha"

    with pytest.raises(SvError, match="hash did not match"):
        wrapped.materialize_to(destination)

    assert not destination.exists()


def test_cache_only_materializer_fails_on_body_miss_without_source_call(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)

    def fail_materializer(destination: Path) -> None:
        raise AssertionError("cache-only mode must not call source materializer")

    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=paths.source_repo_for("Org/Skills"),
        source_path=paths.source_repo_for("Org/Skills") / "skills" / "alpha",
        source_relative_path="skills/alpha",
        source_backend="cache:github-https-api",
        source_content_hash="sha256:41cf6794ba4200b839c53531555f0f3998df4cbb01a4d5cb0b94e3ca5e23947d",
        _materializer=fail_materializer,
    )

    [wrapped] = wrap_catalog_with_skill_body_cache(
        [entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, 0, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: None,
        allow_source_fallback=False,
    )

    with pytest.raises(SvError, match="Cached skill body for Org/Skills:alpha was not found"):
        wrapped.materialize_to(tmp_path / "destination" / "alpha")


def test_cached_metadata_materializer_refreshes_before_source_body_miss(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha", body="remote\n")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    repo = _repo()
    cached_entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=paths.source_repo_for(repo.id),
        source_path=paths.source_repo_for(repo.id) / "skills" / "alpha",
        source_relative_path="skills/alpha",
        source_backend="cache:github-https-api",
        source_content_hash=content_hash,
    )
    backend = FakeSourceBackend(
        {
            "skills/alpha/SKILL.md": "---\nname: alpha\ndescription: Alpha skill.\n---\n",
            "skills/alpha/notes.md": "remote\n",
        }
    )
    refreshed_with_source = replace(
        cached_entry,
        source_backend="fake",
        _materializer=lambda destination: backend.materialize_folder("skills/alpha", destination),
    )
    refresh_calls: list[SourceSkill] = []

    [wrapped] = wrap_catalog_with_skill_body_cache(
        [cached_entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, 0, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: None,
        refresh_entry_on_body_miss=lambda entry: refresh_calls.append(entry) or refreshed_with_source,
    )
    destination = tmp_path / "destination" / "alpha"
    wrapped.materialize_to(destination)

    assert refresh_calls == [cached_entry]
    assert (destination / "notes.md").read_text(encoding="utf-8") == "remote\n"
    cached_copy = tmp_path / "cached-copy-from-fallback" / "alpha"
    assert try_materialize_from_skill_body_cache(
        paths,
        content_hash=content_hash,
        skill_name="alpha",
        destination=cached_copy,
        now=datetime(2026, 5, 18, 14, 0, tzinfo=UTC),
    ) is True


def test_record_cached_skill_body_hash_updates_non_index_cached_metadata(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    document = _catalog_document("2026-05-18T12:00:00Z")
    document = replace(
        document,
        catalog_hash="sha256:28c7cd425c65b4157604a799935b1444db9b0b8c1debf303d76e475e5519d89a",
        entries=(replace(document.entries[0], content_hash=None, skill_file_hash=None),),
    )
    save_cached_catalog(paths, repo, document)
    source_skill = _write_skill_tree(tmp_path / "source", "find-docs")
    content_hash = sha256_skill_directory(source_skill, expected_name="find-docs")
    skill_file_hash = sha256_file(source_skill / "SKILL.md")
    entry = SourceSkill(
        name="find-docs",
        description="Find documentation.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=paths.source_repo_for(repo.id),
        source_path=paths.source_repo_for(repo.id) / "skills" / "find-docs",
        source_relative_path="skills/find-docs",
        source_backend="cache:git-local-source",
    )

    record_cached_skill_body_hash(
        paths,
        repo,
        entry,
        content_hash=content_hash,
        skill_file_hash=skill_file_hash,
    )

    updated = load_cached_catalog(paths, repo)
    assert updated is not None
    assert updated.entries[0].content_hash == content_hash
    assert updated.entries[0].skill_file_hash == skill_file_hash
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_source_cache.py -q --no-cov
```

Expected: missing `attach_source_materializers`, `record_cached_skill_body_hash`, and `wrap_catalog_with_skill_body_cache`.

- [ ] **Step 3: Implement cache-aware wrapping**

Add imports:

```python
from sv.source import SourceBackend, SourceBackendError
```

Add source fallback materializers and body-cache wrapping to `src/sv/source_cache.py`:

```python
Now = Callable[[], datetime]
BackendFactory = Callable[[RepoConfig], Sequence[SourceBackend]]
AfterStore = Callable[[SourceSkill, str, str], None]
RefreshEntryOnBodyMiss = Callable[[SourceSkill], SourceSkill | None]


def attach_source_materializers(
    catalog: Sequence[SourceSkill],
    repos: Sequence[RepoConfig],
    *,
    backend_factory: BackendFactory,
) -> list[SourceSkill]:
    repos_by_id = {repo.id: repo for repo in repos}
    return [
        _attach_source_materializer(entry, repos_by_id, backend_factory=backend_factory)
        for entry in catalog
    ]


def _attach_source_materializer(
    entry: SourceSkill,
    repos_by_id: dict[str, RepoConfig],
    *,
    backend_factory: BackendFactory,
) -> SourceSkill:
    if not entry.source_backend.startswith("cache:"):
        return entry
    repo = repos_by_id.get(entry.repo_id)
    if repo is None:
        raise SvError(f"Cached source metadata references unconfigured repo {entry.repo_id}.")

    def materialize(destination: Path) -> None:
        failures: list[str] = []
        for backend in backend_factory(repo):
            try:
                backend.materialize_folder(entry.source_relative_path, destination)
                return
            except SourceBackendError as exc:
                failures.append(f"{backend.name}: {exc.detail}")
        details = "; ".join(failures) if failures else "no source backends were available"
        raise SvError(
            f"Failed to materialize cached source skill '{entry.name}' from {entry.qualified_reference}: {details}"
        )

    return replace(entry, _materializer=materialize)


def record_cached_skill_body_hash(
    paths: SvPaths,
    repo: RepoConfig,
    entry: SourceSkill,
    *,
    content_hash: str,
    skill_file_hash: str,
) -> None:
    document = load_cached_catalog(paths, repo)
    if document is None:
        return
    updated_entries: list[CachedCatalogEntry] = []
    changed = False
    for cached_entry in document.entries:
        if cached_entry.name == entry.name and cached_entry.source_path == entry.source_relative_path:
            replacement = replace(
                cached_entry,
                content_hash=content_hash,
                skill_file_hash=skill_file_hash,
            )
            updated_entries.append(replacement)
            changed = changed or replacement != cached_entry
        else:
            updated_entries.append(cached_entry)
    if not changed:
        return
    updated_document = replace(
        document,
        catalog_hash=_cached_catalog_hash(updated_entries),
        entries=tuple(updated_entries),
    )
    save_cached_catalog(paths, repo, updated_document)


def wrap_catalog_with_skill_body_cache(
    catalog: Sequence[SourceSkill],
    paths: SvPaths,
    *,
    now: Now,
    after_store: AfterStore,
    allow_source_fallback: bool = True,
    refresh_entry_on_body_miss: RefreshEntryOnBodyMiss | None = None,
    warn: Warn | None = None,
) -> list[SourceSkill]:
    return [
        _wrap_source_skill(
            entry,
            paths,
            now=now,
            after_store=after_store,
            allow_source_fallback=allow_source_fallback,
            refresh_entry_on_body_miss=refresh_entry_on_body_miss,
            warn=warn,
        )
        for entry in catalog
    ]


def _wrap_source_skill(
    entry: SourceSkill,
    paths: SvPaths,
    *,
    now: Now,
    after_store: AfterStore,
    allow_source_fallback: bool,
    refresh_entry_on_body_miss: RefreshEntryOnBodyMiss | None,
    warn: Warn | None,
) -> SourceSkill:
    original_materialize = entry.materialize_to

    def materialize(destination: Path) -> None:
        content_hash = entry.source_content_hash
        current_time = now()
        if content_hash is not None and try_materialize_from_skill_body_cache(
            paths,
            content_hash=content_hash,
            skill_name=entry.name,
            destination=destination,
            now=current_time,
        ):
            return
        if not allow_source_fallback:
            if content_hash is None:
                raise SvError(
                    f"No cached skill body hash is available for {entry.qualified_reference}. "
                    "Run the command without --cached once to populate the body cache."
                )
            raise SvError(
                f"Cached skill body for {entry.qualified_reference} was not found. "
                "Run the command without --cached once to populate the body cache."
            )
        if entry.source_backend.startswith("cache:"):
            if refresh_entry_on_body_miss is None:
                raise SvError(
                    f"Cached metadata for {entry.qualified_reference} requires a source refresh before materialization."
                )
            refreshed_entry = refresh_entry_on_body_miss(entry)
            if refreshed_entry is None:
                raise SvError(f"Refreshed source metadata did not include {entry.qualified_reference}.")
            refreshed_wrapped = _wrap_source_skill(
                refreshed_entry,
                paths,
                now=now,
                after_store=after_store,
                allow_source_fallback=True,
                refresh_entry_on_body_miss=None,
                warn=warn,
            )
            refreshed_wrapped.materialize_to(destination)
            return
        try:
            original_materialize(destination)
            # Preserve existing project/vault validation ordering: surface invalid SKILL.md
            # errors before reporting an indexed content-hash mismatch.
            parse_skill_file(destination / "SKILL.md", expected_folder=entry.name)
            actual_hash = sha256_skill_directory(destination, expected_name=entry.name)
            actual_skill_file_hash = sha256_file(destination / "SKILL.md")
            if content_hash is not None and actual_hash != content_hash:
                raise SvError(
                    f"Materialized skill '{entry.name}' hash did not match refreshed source metadata."
                )
        except Exception:
            remove_materialization_path(destination, ignore_errors=True)
            raise
        try:
            store_skill_body_cache(
                paths,
                destination,
                skill_name=entry.name,
                content_hash=actual_hash,
                source_reference=entry.qualified_reference,
                now=current_time,
            )
        except SvError as exc:
            if _cache_write_error_must_fail(exc):
                remove_materialization_path(destination, ignore_errors=True)
                raise
            if warn is not None:
                warn(f"warning: failed to update skill body cache for {entry.qualified_reference}: {exc}")
        after_store(entry, actual_hash, actual_skill_file_hash)

    return replace(entry, _materializer=materialize)


def _cache_write_error_must_fail(exc: SvError) -> bool:
    text = str(exc).casefold()
    return "symlink" in text or "hash mismatch" in text or "failed validation" in text
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/test_source_cache.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```bash
git add src/sv/source_cache.py tests/test_source_cache.py
git commit -m "feat: wrap source skills with global body cache"
```

---

### Task 6: Add lazy pruning for skill body cache

**Files:**
- Modify: `src/sv/source_cache.py`
- Test: `tests/test_source_cache.py`

- [ ] **Step 1: Write pruning tests**

Append:

```python
from sv.source_cache import prune_skill_body_cache


def test_prune_skill_body_cache_removes_entries_unused_for_more_than_30_days(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    old_skill = _write_skill_tree(tmp_path / "old", "old-skill")
    old_hash = sha256_skill_directory(old_skill, expected_name="old-skill")
    store_skill_body_cache(
        paths,
        old_skill,
        skill_name="old-skill",
        content_hash=old_hash,
        source_reference="Org/Skills:skills/old-skill",
        now=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
    )
    fresh_skill = _write_skill_tree(tmp_path / "fresh", "fresh-skill")
    fresh_hash = sha256_skill_directory(fresh_skill, expected_name="fresh-skill")
    store_skill_body_cache(
        paths,
        fresh_skill,
        skill_name="fresh-skill",
        content_hash=fresh_hash,
        source_reference="Org/Skills:skills/fresh-skill",
        now=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
    )

    prune_skill_body_cache(
        paths,
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        max_unused_seconds=30 * 24 * 60 * 60,
        max_bytes=256 * 1024 * 1024,
        force=True,
    )

    assert not skill_body_cache_path(paths, old_hash).exists()
    assert skill_body_cache_path(paths, fresh_hash).exists()


def test_prune_skill_body_cache_enforces_size_cap_by_lru_then_use_count(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    first = _write_skill_tree(tmp_path / "first", "first-skill", body="a" * 100)
    first_hash = sha256_skill_directory(first, expected_name="first-skill")
    store_skill_body_cache(
        paths,
        first,
        skill_name="first-skill",
        content_hash=first_hash,
        source_reference="Org/Skills:skills/first-skill",
        now=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
    )
    second = _write_skill_tree(tmp_path / "second", "second-skill", body="b" * 100)
    second_hash = sha256_skill_directory(second, expected_name="second-skill")
    store_skill_body_cache(
        paths,
        second,
        skill_name="second-skill",
        content_hash=second_hash,
        source_reference="Org/Skills:skills/second-skill",
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
    )

    second_size = load_skill_body_metadata(paths, second_hash).size_bytes

    prune_skill_body_cache(
        paths,
        now=datetime(2026, 5, 18, 13, 0, tzinfo=UTC),
        max_unused_seconds=30 * 24 * 60 * 60,
        max_bytes=second_size,
        force=True,
    )

    assert not skill_body_cache_path(paths, first_hash).exists()
    assert skill_body_cache_path(paths, second_hash).exists()


def test_prune_skill_body_cache_refuses_symlinked_marker_temp_file(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.cache_dir.mkdir(parents=True)
    temp_path = paths.cache_prune_marker.with_name(f".{paths.cache_prune_marker.name}.{os.getpid()}.tmp")
    target = tmp_path / "attacker-marker.toml"
    temp_path.symlink_to(target)

    with pytest.raises(SvError, match="symlinked"):
        prune_skill_body_cache(
            paths,
            now=datetime(2026, 5, 18, 13, 0, tzinfo=UTC),
            force=True,
        )

    assert not target.exists()


def test_prune_skill_body_cache_ignores_corrupt_prune_marker(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.cache_dir.mkdir(parents=True)
    paths.cache_prune_marker.write_text("not = [valid", encoding="utf-8")

    prune_skill_body_cache(
        paths,
        now=datetime(2026, 5, 18, 13, 0, tzinfo=UTC),
        force=False,
    )

    assert "last_pruned_at" in paths.cache_prune_marker.read_text(encoding="utf-8")


def test_prune_skill_body_cache_refuses_symlinked_marker_file(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.cache_dir.mkdir(parents=True)
    target = tmp_path / "attacker-marker.toml"
    target.write_text('last_pruned_at = "2026-05-18T12:00:00Z"\n', encoding="utf-8")
    paths.cache_prune_marker.symlink_to(target)

    with pytest.raises(SvError, match="symlinked"):
        prune_skill_body_cache(
            paths,
            now=datetime(2026, 5, 18, 13, 0, tzinfo=UTC),
            force=False,
        )
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_source_cache.py -q --no-cov
```

Expected: missing `prune_skill_body_cache`.

- [ ] **Step 3: Implement pruning**

Add:

```python
@dataclass(frozen=True)
class _SkillBodyCacheEntry:
    path: Path
    metadata: SkillBodyMetadata


def prune_skill_body_cache(
    paths: SvPaths,
    *,
    now: datetime,
    max_unused_seconds: int = DEFAULT_SKILL_BODY_MAX_UNUSED_SECONDS,
    max_bytes: int = DEFAULT_SKILL_BODY_MAX_BYTES,
    force: bool = False,
) -> None:
    if not force and not _should_prune(paths, now=now):
        return
    entries = _skill_body_cache_entries(paths)
    cutoff = now.timestamp() - max_unused_seconds
    kept: list[_SkillBodyCacheEntry] = []
    for entry in entries:
        last_used = _parse_utc(entry.metadata.last_used_at, entry.path).timestamp()
        if last_used < cutoff:
            remove_materialization_path(entry.path, ignore_errors=True)
        else:
            kept.append(entry)
    total = sum(entry.metadata.size_bytes for entry in kept)
    if total > max_bytes:
        for entry in sorted(kept, key=lambda item: (item.metadata.last_used_at, item.metadata.use_count, -item.metadata.size_bytes)):
            if total <= max_bytes:
                break
            remove_materialization_path(entry.path, ignore_errors=True)
            total -= entry.metadata.size_bytes
    _write_prune_marker(paths, now=now)


def _skill_body_cache_entries(paths: SvPaths) -> list[_SkillBodyCacheEntry]:
    root = paths.skill_body_cache_dir / "sha256"
    _reject_symlinked_cache_dir(root.parent)
    if root.is_symlink():
        raise SvError(f"Refusing to inspect symlinked sv skill body cache root at {root}.")
    if not root.is_dir():
        return []
    entries: list[_SkillBodyCacheEntry] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if not child.is_dir() or child.is_symlink():
            continue
        content_hash = f"sha256:{child.name}"
        try:
            metadata = load_skill_body_metadata(paths, content_hash)
        except SvError:
            remove_materialization_path(child, ignore_errors=True)
            continue
        entries.append(_SkillBodyCacheEntry(path=child, metadata=metadata))
    return entries


def _should_prune(paths: SvPaths, *, now: datetime) -> bool:
    marker = paths.cache_prune_marker
    _reject_symlinked_cache_dir(marker.parent)
    if marker.is_symlink():
        raise SvError(f"Refusing to read symlinked sv cache prune marker at {marker}.")
    if not marker.is_file():
        return True
    try:
        data = load_toml_document(marker, "sv cache prune marker")
        value = data.get("last_pruned_at") if isinstance(data, dict) else None
        if not isinstance(value, str):
            return True
        last_pruned = _parse_utc(value, marker)
    except SvError:
        return True
    return now - last_pruned >= timedelta(days=1)


def _write_prune_marker(paths: SvPaths, *, now: datetime) -> None:
    path = paths.cache_prune_marker
    _ensure_private_cache_dir(path.parent)
    atomic_write_text(
        path,
        f"schema_version = {CACHE_SCHEMA_VERSION}\nlast_pruned_at = {_toml_string(_utc_timestamp(now))}\n",
        document_name="sv cache prune marker",
        temp_path_description="sv cache prune marker temp file",
    )
```

- [ ] **Step 4: Call pruning after body-cache writes**

Modify `store_skill_body_cache` now that `prune_skill_body_cache` exists. Pruning is part of the body-cache write API, not only the CLI wrapper, so direct callers cannot bypass the 30-day/256 MiB bounds.

Add this helper:

```python
def _prune_after_body_cache_write(paths: SvPaths, *, now: datetime) -> None:
    prune_skill_body_cache(paths, now=now)
```

In the existing-valid-cache branch, prune after touching metadata:

```python
    if cache_root.exists():
        if _cached_skill_body_is_valid(paths, content_hash, skill_name=skill_name):
            _touch_skill_body_cache(paths, content_hash, now=now)
            _prune_after_body_cache_write(paths, now=now)
            return
        remove_materialization_path(cache_root, ignore_errors=True)
```

After the staged cache entry is atomically moved into place, prune as well:

```python
        temp_root.replace(cache_root)
        _prune_after_body_cache_write(paths, now=now)
```

Keep CLI `after_store` for catalog metadata writeback and warning output, but do not make it the only pruning path.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/test_source_cache.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 6: Commit Task 6**

```bash
git add src/sv/source_cache.py tests/test_source_cache.py
git commit -m "feat: prune global skill body cache lazily"
```

---

### Task 7: Integrate metadata cache into CLI catalog reads

**Files:**
- Modify: `src/sv/cli.py`
- Test: `tests/test_cli_list_contracts.py`
- Test: `tests/test_cli_search_contracts.py`
- Test: `tests/test_cli_add_contracts.py`

- [ ] **Step 1: Write CLI tests for list/search cache behavior**

Add to `tests/test_cli_list_contracts.py`:

```python
def test_list_uses_fresh_cached_metadata_without_refresh(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    first = run_sv(["list"], cwd=project, home=home)
    assert first.exit_code == 0
    shutil.rmtree(source / ".git")

    def fail_git(args, cwd=None):
        raise AssertionError(f"fresh cache should avoid Git/source refresh: {args}")

    second = run_sv(["list"], cwd=project, home=home, git_runner=fail_git)

    assert second.exit_code == 0
    assert "alpha" in second.stdout
```

Add to `tests/test_cli_search_contracts.py` (also import `shutil`, `configure_source`, and `make_source_repo` if this file does not already import them):

```python
def test_search_uses_cached_metadata_after_list(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert run_sv(["list"], cwd=project, home=home).exit_code == 0
    shutil.rmtree(source / ".git")

    def fail_git(args, cwd=None):
        raise AssertionError(f"fresh cache should avoid Git/source refresh: {args}")

    result = run_sv(["search", "alpha"], cwd=project, home=home, git_runner=fail_git)

    assert result.exit_code == 0
    assert "alpha" in result.stdout
```

- [ ] **Step 2: Run the new CLI tests and verify they fail**

Run:

```bash
uv run pytest tests/test_cli_list_contracts.py::test_list_uses_fresh_cached_metadata_without_refresh tests/test_cli_search_contracts.py::test_search_uses_cached_metadata_after_list -q --no-cov
```

Expected: failure because commands still refresh every time.

- [ ] **Step 3: Add CLI cache policy imports and helper**

In `src/sv/cli.py`, add imports:

```python
from sv.source_cache import (
    CacheMode,
    CachePolicy,
    CacheRefreshResult,
    get_catalog_with_cache,
    record_cached_skill_body_hash,
    wrap_catalog_with_skill_body_cache,
)
```

Add helper functions near the existing source refresh helpers:

```python
def _add_cache_policy_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--refresh",
        action="store_true",
        help="Force refresh source metadata before using the cache.",
    )
    group.add_argument(
        "--cached",
        action="store_true",
        help="Use cached source metadata only and do not refresh sources.",
    )


def _cache_policy_from_args(
    args: argparse.Namespace,
    *,
    default_mode: CacheMode = CacheMode.NORMAL,
    allow_stale_on_error: bool = True,
) -> CachePolicy:
    if getattr(args, "refresh", False):
        return CachePolicy.force_refresh(allow_stale_on_error=allow_stale_on_error)
    if getattr(args, "cached", False):
        return CachePolicy.cache_only()
    return CachePolicy(mode=default_mode, allow_stale_on_error=allow_stale_on_error)
```

- [ ] **Step 4: Add cache flags to source-consuming parsers**

In `build_parser`, call `_add_cache_policy_args` for:

```python
list_parser = subparsers.add_parser(...)
_add_cache_policy_args(list_parser)

search_parser = subparsers.add_parser(...)
_add_cache_policy_args(search_parser)

add_parser = subparsers.add_parser(...)
_add_cache_policy_args(add_parser)

sync_parser = subparsers.add_parser(...)
_add_cache_policy_args(sync_parser)

update_parser = subparsers.add_parser(...)
_add_cache_policy_args(update_parser)

status_parser = subparsers.add_parser(...)
_add_cache_policy_args(status_parser)
```

For existing `subparsers.add_parser("list", ...)`, assign the parser to a variable before adding cache args.

- [ ] **Step 5: Add cache-aware catalog helper in CLI**

Before adding the wrapper, refactor the existing source-refresh helper so cache refreshes can preserve source provenance. Extract the lightweight refresh result (`refreshed_backends_by_repo` and `index_hashes_by_repo`) from the existing `build_source_catalog_from_backends(...)` path and return it as `CacheRefreshResult`. Keep the existing `_update_sources_and_catalog_from_repos(...) -> list[SourceSkill]` API for non-cache callers by having it call the extracted helper and return only `.entries`.

Add this wrapper in `src/sv/cli.py`:

```python
def _catalog_for_source_command(
    repos: Sequence[RepoConfig],
    paths: SvPaths,
    git_runner,
    *,
    policy: CachePolicy,
    record_global_source_state: bool,
    lightweight_discovery: bool = True,
    allow_partial_failures: bool = False,
    update: bool = True,
) -> list[SourceSkill]:
    def refresh(selected_repos: Sequence[RepoConfig]) -> CacheRefreshResult:
        return _update_sources_and_catalog_cache_refresh_from_repos(
            selected_repos,
            paths,
            git_runner,
            update=update,
            record_global_source_state=record_global_source_state,
            lightweight_discovery=lightweight_discovery,
            allow_partial_failures=allow_partial_failures,
            warn=lambda message: print(message, file=sys.stderr),
        )

    catalog = get_catalog_with_cache(
        repos,
        paths,
        policy=policy,
        now=datetime.now(UTC),
        refresh_catalog=refresh,
        warn=lambda message: print(message, file=sys.stderr),
    )
    allow_source_fallback = policy.mode is not CacheMode.CACHE_ONLY
    repos_by_id = {repo.id: repo for repo in repos}

    def refresh_entry_on_body_miss(entry: SourceSkill) -> SourceSkill | None:
        repo = repos_by_id.get(entry.repo_id)
        if repo is None:
            return None
        refreshed_catalog = get_catalog_with_cache(
            [repo],
            paths,
            policy=CachePolicy.force_refresh(allow_stale_on_error=False),
            now=datetime.now(UTC),
            refresh_catalog=refresh,
            warn=lambda message: print(message, file=sys.stderr),
        )
        for candidate in refreshed_catalog:
            if candidate.name == entry.name and candidate.source_relative_path == entry.source_relative_path:
                return candidate
        return None

    def after_store(entry: SourceSkill, content_hash: str, skill_file_hash: str) -> None:
        repo = repos_by_id.get(entry.repo_id)
        if repo is None:
            return
        try:
            record_cached_skill_body_hash(
                paths,
                repo,
                entry,
                content_hash=content_hash,
                skill_file_hash=skill_file_hash,
            )
        except SvError as exc:
            if "symlink" in str(exc).casefold():
                raise
            print(f"warning: failed to update cached metadata for {entry.qualified_reference}: {exc}", file=sys.stderr)

    return wrap_catalog_with_skill_body_cache(
        catalog,
        paths,
        now=lambda: datetime.now(UTC),
        after_store=after_store,
        allow_source_fallback=allow_source_fallback,
        refresh_entry_on_body_miss=refresh_entry_on_body_miss if allow_source_fallback else None,
        warn=lambda message: print(message, file=sys.stderr),
    )
```

- [ ] **Step 6: Route list/search through cache helper**

In `handle`, replace the direct `_update_sources_and_catalog_from_repos` calls for `list` and `search` with:

```python
catalog = _catalog_for_source_command(
    config.repos,
    paths,
    git_runner,
    policy=_cache_policy_from_args(args),
    record_global_source_state=record_global_source_state,
    lightweight_discovery=True,
)
```

Keep the existing no-source-repos behavior unchanged.

- [ ] **Step 7: Run focused CLI tests**

Run:

```bash
uv run pytest tests/test_cli_list_contracts.py::test_list_uses_fresh_cached_metadata_without_refresh tests/test_cli_search_contracts.py::test_search_uses_cached_metadata_after_list -q --no-cov
```

Expected: PASS.

- [ ] **Step 8: Add tests for `--refresh` and `--cached`**

Add to `tests/test_cli_list_contracts.py`:

```python
def test_list_refresh_flag_bypasses_fresh_cache(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert run_sv(["list"], cwd=project, home=home).exit_code == 0
    write_source_skill(source, "gamma", "Gamma skill.", "gamma v1\n")
    run_git(["add", "skills/gamma"], source)
    run_git(["commit", "-m", "add gamma"], source)

    result = run_sv(["list", "--refresh"], cwd=project, home=home)

    assert result.exit_code == 0
    assert "gamma" in result.stdout


def test_list_cached_flag_fails_without_cache(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    result = run_sv(["list", "--cached"], cwd=project, home=home)

    assert result.exit_code == 1
    assert "No cached metadata found" in result.stderr
```

- [ ] **Step 9: Run list/search contract tests**

Run:

```bash
uv run pytest tests/test_cli_list_contracts.py tests/test_cli_search_contracts.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 10: Commit Task 7**

```bash
git add src/sv/cli.py tests/test_cli_list_contracts.py tests/test_cli_search_contracts.py
git commit -m "feat: use metadata cache for list and search"
```

---

### Task 8: Integrate cache with add, sync, update, and status

**Files:**
- Modify: `src/sv/cli.py`
- Test: `tests/test_cli_add_contracts.py`
- Test: `tests/test_cli_sync_contracts.py`
- Test: `tests/test_cli_update_contracts.py`
- Test: `tests/test_cli_status_contracts.py`

- [ ] **Step 1: Add tests for `sv add` using cached metadata and skill body cache**

Add to `tests/test_cli_add_contracts.py`:

```python
def test_add_uses_cached_metadata_and_cached_skill_body_when_source_unavailable(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    assert run_sv(["add", "alpha"], cwd=project, home=home).exit_code == 0
    shutil.rmtree(project / ".pi" / "skills" / "alpha")
    shutil.rmtree(source / ".git")

    def fail_git(args, cwd=None):
        raise AssertionError(f"--cached must not call Git or GitHub backends: {args}")

    result = run_sv(["add", "alpha", "--cached"], cwd=project, home=home, git_runner=fail_git)

    assert result.exit_code == 0
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == "alpha v1\n"


def test_add_cached_fails_without_cached_skill_body_and_does_not_call_source(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert run_sv(["list"], cwd=project, home=home).exit_code == 0
    shutil.rmtree(source / ".git")

    def fail_git(args, cwd=None):
        raise AssertionError(f"--cached must not call Git or GitHub backends: {args}")

    result = run_sv(["add", "alpha", "--cached"], cwd=project, home=home, git_runner=fail_git)

    assert result.exit_code == 1
    assert "cached skill body" in result.stderr.lower()
```

Add `import shutil` if the file does not already import it.

- [ ] **Step 2: Route `add` catalog reads through cache helper**

In `handle`, replace catalog reads in the `add` branch:

- `args.interactive` path currently calling `_update_sources_and_catalog(... update=False ...)`
- `args.all` path currently calling `_update_sources_and_catalog_for_add_all(...)`
- single skill path currently calling `_update_sources_and_catalog(... update=True ...)`

Use cache helper with the loaded config. Preserve the existing interactive behavior by passing `update=False` for `sv add -l`; use `update=True` for single-skill add and `add --all`:

```python
config = _load_config_for_source_command(paths)
catalog = _catalog_for_source_command(
    config.repos,
    paths,
    git_runner,
    policy=_cache_policy_from_args(args),
    record_global_source_state=record_global_source_state,
    lightweight_discovery=True,
    update=not args.interactive,
)
```

For `sv add --all --repo REPO_ID`, filter `config.repos` to the requested repo ID before calling `_catalog_for_source_command`, preserving the existing missing repo error message.

- [ ] **Step 3: Run add-focused tests**

Run:

```bash
uv run pytest tests/test_cli_add_contracts.py::test_add_uses_cached_metadata_and_cached_skill_body_when_source_unavailable tests/test_cli_add_contracts.py::test_add_cached_fails_without_cached_skill_body_and_does_not_call_source -q --no-cov
```

Expected: PASS.

- [ ] **Step 4: Route `sync` and `update` through cache helper with refresh-first defaults**

In `handle` for `sync`, replace `_update_sources_and_catalog(...)` with config load plus `_catalog_for_source_command(...)`. Preserve existing sync semantics by forcing refresh and failing closed unless the user explicitly passes `--cached`:

```python
config = _load_config_for_source_command(paths)
sync_policy = _cache_policy_from_args(
    args,
    default_mode=CacheMode.FORCE_REFRESH,
    allow_stale_on_error=False,
)
catalog = _catalog_for_source_command(
    config.repos,
    paths,
    git_runner,
    policy=sync_policy,
    record_global_source_state=record_global_source_state,
    allow_partial_failures=False,
)
```

In `_handle_update`, replace `_update_sources_and_catalog_from_repos(...)` with `_catalog_for_source_command(...)` using the policy passed from `handle`. Change `_handle_update` signature to accept `cache_policy: CachePolicy`, and pass this from `handle`:

```python
update_policy = _cache_policy_from_args(
    args,
    default_mode=CacheMode.FORCE_REFRESH,
    allow_stale_on_error=False,
)
return _handle_update(
    cwd=cwd,
    paths=paths,
    adapter=adapter,
    git_runner=git_runner,
    record_global_source_state=record_global_source_state,
    context=local_context,
    cache_policy=update_policy,
)
```

Concrete `_handle_update` signature:

```python
def _handle_update(
    cwd: Path,
    paths: SvPaths,
    adapter: PiAdapter,
    git_runner,
    record_global_source_state: bool,
    context: LocalContext | None = None,
    *,
    cache_policy: CachePolicy,
) -> int:
```

Inside `_handle_update`, keep the existing user-facing `print("Updating source repos...")`, then call `_catalog_for_source_command(...)` with `policy=cache_policy`.

- [ ] **Step 5: Route source-aware status refresh through cache helper with refresh-first defaults**

Change `_status_catalog_if_configured` to accept `cache_policy: CachePolicy`. Replace its direct `_update_sources_and_catalog_from_repos(...)` call with `_catalog_for_source_command(...)`.

Thread this policy from `handle` into `_handle_status`, `_handle_vault_status`, and `_status_catalog_if_configured`:

```python
status_policy = _cache_policy_from_args(
    args,
    default_mode=CacheMode.FORCE_REFRESH,
    allow_stale_on_error=False,
)
```

This preserves existing `sv status` behavior: if managed installed skills require source comparison, status refreshes source metadata instead of trusting a 24-hour-old cache. `sv status --cached` remains available for offline/cache-only status checks and must not call source backends.

- [ ] **Step 6: Add tests that mutating/status commands do not reuse fresh metadata by default**

Add to `tests/test_cli_sync_contracts.py`:

```python
def test_sync_refreshes_sources_even_when_metadata_cache_is_fresh(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert run_sv(["add", "alpha"], cwd=project, home=home).exit_code == 0
    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)

    result = run_sv(["sync"], cwd=project, home=home)

    assert result.exit_code == 0
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == "alpha v2\n"
```

Add to `tests/test_cli_update_contracts.py`:

```python
def test_update_refreshes_sources_even_when_metadata_cache_is_fresh(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert run_sv(["add", "alpha"], cwd=project, home=home).exit_code == 0
    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)

    result = run_sv(["update"], cwd=project, home=home)

    assert result.exit_code == 0
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == "alpha v2\n"
```

Add to `tests/test_cli_status_contracts.py`:

```python
def test_status_refreshes_sources_even_when_metadata_cache_is_fresh(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert run_sv(["add", "alpha"], cwd=project, home=home).exit_code == 0
    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)

    result = run_sv(["status"], cwd=project, home=home)

    assert result.exit_code == 0
    assert "alpha" in result.stdout
    assert "update available" in result.stdout
```

Add cache-only and failure-closed coverage in the same files:

```python
def test_sync_cached_uses_cached_body_and_does_not_refresh_source(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert run_sv(["add", "alpha"], cwd=project, home=home).exit_code == 0
    (project / ".pi" / "skills" / "alpha" / "notes.md").write_text("local edit\n")
    shutil.rmtree(source / ".git")

    def fail_git(args, cwd=None):
        raise AssertionError(f"--cached must not call Git/source refresh: {args}")

    result = run_sv(["sync", "--cached"], cwd=project, home=home, git_runner=fail_git)

    assert result.exit_code == 0
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == "alpha v1\n"


def test_status_cached_does_not_refresh_source(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert run_sv(["add", "alpha"], cwd=project, home=home).exit_code == 0
    shutil.rmtree(source / ".git")

    def fail_git(args, cwd=None):
        raise AssertionError(f"--cached must not call Git/source refresh: {args}")

    result = run_sv(["status", "--cached"], cwd=project, home=home, git_runner=fail_git)

    assert result.exit_code == 0
    assert "alpha" in result.stdout


def test_update_cached_does_not_refresh_source(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert run_sv(["add", "alpha"], cwd=project, home=home).exit_code == 0
    shutil.rmtree(source / ".git")

    def fail_git(args, cwd=None):
        raise AssertionError(f"--cached must not call Git/source refresh: {args}")

    result = run_sv(["update", "--cached"], cwd=project, home=home, git_runner=fail_git)

    assert result.exit_code == 0


def test_sync_fails_closed_when_refresh_fails_even_with_cache(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert run_sv(["add", "alpha"], cwd=project, home=home).exit_code == 0
    shutil.rmtree(source / ".git")

    result = run_sv(["sync"], cwd=project, home=home)

    assert result.exit_code == 1
    assert "source" in result.stderr.lower() or "refresh" in result.stderr.lower()
```

Also add CLI stale-fallback tests for `sv add alpha` and `sv add --all`: create an expired catalog cache with an available cached body, force source refresh to fail, assert the command warns about stale cached metadata and succeeds from cache. Do not add a test that expects `sv update --refresh` to use stale metadata after refresh failure. Mutating/status source comparisons are intentionally failure-closed by default; keep the existing unreachable-source contract tests.

- [ ] **Step 7: Run affected CLI contract tests**

Run:

```bash
uv run pytest tests/test_cli_add_contracts.py tests/test_cli_sync_contracts.py tests/test_cli_update_contracts.py tests/test_cli_status_contracts.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 8: Commit Task 8**

```bash
git add src/sv/cli.py tests/test_cli_add_contracts.py tests/test_cli_sync_contracts.py tests/test_cli_update_contracts.py tests/test_cli_status_contracts.py
git commit -m "feat: use global cache for mutating source commands"
```

---

### Task 9: Add `sv cache status` and `sv cache clean`

**Files:**
- Modify: `src/sv/cli.py`
- Modify: `src/sv/source_cache.py`
- Test: `tests/test_cli_help.py`
- Test: `tests/test_source_cache.py`

- [ ] **Step 1: Add cache summary helpers**

In `src/sv/source_cache.py`, add:

```python
@dataclass(frozen=True)
class CacheSummary:
    catalog_files: int
    skill_bodies: int
    skill_body_bytes: int


def cache_summary(paths: SvPaths) -> CacheSummary:
    catalog_files = 0
    _reject_symlinked_cache_dir(paths.catalog_cache_dir.parent)
    if paths.catalog_cache_dir.is_symlink():
        raise SvError(f"Refusing to inspect symlinked sv catalog cache directory at {paths.catalog_cache_dir}.")
    if paths.catalog_cache_dir.is_dir():
        catalog_files = sum(
            1
            for path in paths.catalog_cache_dir.glob("*.toml")
            if path.is_file() and not path.is_symlink()
        )
    skill_entries = _skill_body_cache_entries(paths)
    return CacheSummary(
        catalog_files=catalog_files,
        skill_bodies=len(skill_entries),
        skill_body_bytes=sum(entry.metadata.size_bytes for entry in skill_entries),
    )


def clean_cache(paths: SvPaths, *, now: datetime) -> CacheSummary:
    prune_skill_body_cache(paths, now=now, force=True)
    return cache_summary(paths)
```

- [ ] **Step 2: Add parser entries**

In `build_parser`, add:

```python
cache_parser = subparsers.add_parser(
    "cache",
    help="Inspect or clean the global sv cache.",
)
cache_subparsers = cache_parser.add_subparsers(dest="cache_command", required=True)
cache_subparsers.add_parser("status", help="Show global cache usage.")
cache_subparsers.add_parser("clean", help="Prune expired and over-budget cached skill bodies.")
```

- [ ] **Step 3: Add handler**

Import helpers:

```python
from sv.source_cache import cache_summary, clean_cache
```

In `handle`, before source-consuming command branches, add:

```python
if args.command == "cache":
    return _handle_cache(args, paths)
```

Add handler:

```python
def _handle_cache(args: argparse.Namespace, paths: SvPaths) -> int:
    if args.cache_command == "status":
        summary = cache_summary(paths)
        print("Global sv cache")
        print(
            format_table(
                ["Catalog files", "Skill bodies", "Skill body bytes"],
                [[str(summary.catalog_files), str(summary.skill_bodies), str(summary.skill_body_bytes)]],
                max_widths={"Catalog files": 14, "Skill bodies": 12, "Skill body bytes": 16},
                min_widths={"Catalog files": 14, "Skill bodies": 12, "Skill body bytes": 16},
                max_table_width=_table_width(),
            )
        )
        return 0
    if args.cache_command == "clean":
        summary = clean_cache(paths, now=datetime.now(UTC))
        print(
            f"Cleaned global sv cache. Skill bodies: {summary.skill_bodies}; bytes: {summary.skill_body_bytes}."
        )
        return 0
    raise SvError(f"Unknown cache command: {args.cache_command}")
```

- [ ] **Step 4: Add help and behavior tests**

In `tests/test_cli_help.py`, add assertions that `sv --help` includes `cache` and `sv cache --help` includes `status` and `clean` using the existing help-test style in that file.

In `tests/test_source_cache.py`, add:

```python
from sv.source_cache import cache_summary, clean_cache


def test_cache_summary_counts_catalogs_and_skill_bodies(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    save_cached_catalog(paths, _repo(), _catalog_document("2026-05-18T12:00:00Z"))
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
    )

    summary = cache_summary(paths)
    cleaned = clean_cache(paths, now=datetime(2026, 5, 18, 13, 0, tzinfo=UTC))

    assert summary.catalog_files == 1
    assert summary.skill_bodies == 1
    assert summary.skill_body_bytes > 0
    assert cleaned == summary
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/test_source_cache.py tests/test_cli_help.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 6: Commit Task 9**

```bash
git add src/sv/cli.py src/sv/source_cache.py tests/test_source_cache.py tests/test_cli_help.py
git commit -m "feat: add cache inspection commands"
```

---

### Task 10: Update documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/commands.md`
- Modify: `docs/usage.md`
- Modify: `docs/troubleshooting.md`
- Test: `tests/test_docs.py`

- [ ] **Step 1: Update command docs**

In `docs/commands.md`, add a section after Source discovery and refresh:

```markdown
## Global cache

`sv` keeps a global cache under `~/.sv/cache/v1`. Source metadata is cached for 24 hours for normal browsing/install commands (`sv list`, `sv search`, `sv add`, and `sv add --all`). These commands use fresh cached metadata, refresh lazily when it expires, and warn while using stale metadata if refresh fails and cached metadata exists.

`sv sync`, `sv update`, and source-aware `sv status` are refresh-first by default so they continue to report and apply current source changes. They still use the cache manager for `--cached`, cache writes, body-cache reuse, and cache-only error handling, but they do not silently trust 24-hour-old metadata by default.

Materialized skill folders are cached by content hash. Indexed sources provide the content hash in source metadata; non-index sources learn and record the hash after the first successful add/sync/update materialization. Reinstalling or syncing a skill can reuse a cached skill body when the cached hash matches the source metadata. If normal mode has cached metadata but no matching cached body, `sv` refreshes that repo's metadata before materializing from source, so stale metadata is not paired with a newer source body. Cached skill bodies are pruned lazily after cache writes: entries unused for more than 30 days are removed, and the cache is reduced to 256 MiB when it grows beyond that size.

Use `--refresh` on source-reading commands to force metadata refresh. Use `--cached` to avoid network and Git refreshes. Commands that need to materialize a skill with `--cached` require a cached skill body; if the body is missing, rerun once without `--cached` to populate it. Use `sv cache status` to inspect cache usage and `sv cache clean` to prune cached skill bodies immediately.
```

- [ ] **Step 2: Update README storage section**

In `README.md`, extend “Where sv stores files” with:

```markdown
Global cache metadata and skill bodies live under:

```text
~/.sv/cache/v1/
```

Source metadata for normal browsing/install commands is cached for 24 hours. `sv sync`, `sv update`, and source-aware `sv status` refresh metadata by default to preserve current-source semantics. Skill bodies are cached by content hash and pruned lazily after 30 days unused or when the skill-body cache exceeds 256 MiB. For non-index sources, the body hash is recorded after the first successful materialization. If cached metadata points at a skill whose body is not cached, normal mode refreshes that repo before source materialization; `--cached` fails instead of refreshing.
```

- [ ] **Step 3: Update usage and troubleshooting**

In `docs/usage.md`, add examples:

```bash
sv list --refresh
sv search docs --cached
sv cache status
sv cache clean
```

In `docs/troubleshooting.md`, add notes for:

- stale metadata warning means refresh failed and cached metadata was used by a command that allows stale fallback (`list`, `search`, `add`, or `add --all`)
- `--refresh` forces a new source check
- `sync`, `update`, and source-aware `status` refresh by default and fail closed on refresh errors unless `--cached` is used
- `--cached` requires existing metadata cache
- `sv add --cached`, `sv sync --cached`, and `sv update --cached` also require matching cached skill bodies for any skill they need to materialize
- if a cached skill body is missing in `--cached` mode, rerun once without `--cached` to populate the body cache
- if a cached skill body is missing in normal mode, `sv` refreshes the source metadata before materializing from source
- invalid or tampered cache files are ignored or rejected; symlinked cache paths are refused
- `sv cache clean` removes expired and over-budget skill bodies

- [ ] **Step 4: Run docs tests**

Run:

```bash
uv run pytest tests/test_docs.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit Task 10**

```bash
git add README.md docs/commands.md docs/usage.md docs/troubleshooting.md tests/test_docs.py
git commit -m "docs: document global cache behavior"
```

---

### Task 11: Full verification and cleanup

**Files:**
- Review: all files changed in previous tasks

- [ ] **Step 1: Run focused non-integration tests**

Run:

```bash
uv run pytest -m "not integration and not security" -q --no-cov
```

Expected: PASS.

- [ ] **Step 2: Run integration tests**

Run:

```bash
uv run pytest -m integration -q --no-cov
```

Expected: PASS.

- [ ] **Step 3: Run security tests**

Run:

```bash
uv run pytest -m security -q --no-cov
```

Expected: PASS.

- [ ] **Step 4: Run lint and type checks**

Run:

```bash
uv run ruff check .
uv run ty check src tests
```

Expected: both commands PASS.

- [ ] **Step 5: Run full coverage gate**

Run:

```bash
uv run pytest --cov=sv --cov-report=term-missing
```

Expected: PASS with coverage at or above the configured threshold.

- [ ] **Step 6: Inspect cache-related user output manually**

Run these in a temporary project configured with a local source repo:

```bash
sv list
sv list
sv list --refresh
sv search alpha --cached
sv add alpha
rm -rf .pi/skills/alpha
sv add alpha --cached
sv cache status
sv cache clean
```

Expected:

- second `sv list` uses cached metadata and completes without source refresh
- `sv list --refresh` refreshes metadata
- `sv search alpha --cached` works after metadata exists
- `sv add alpha --cached` works only after a previous successful materialization populated the skill-body cache
- cache status prints catalog and skill-body counts
- cache clean prints final skill-body count and byte total

- [ ] **Step 7: Commit final verification note if docs or tests changed during cleanup**

If Step 1-6 required edits, commit them:

```bash
git add README.md docs/commands.md docs/usage.md docs/troubleshooting.md src/sv/config.py src/sv/source_cache.py src/sv/cli.py tests/test_source_cache.py tests/test_cli_help.py tests/test_cli_list_contracts.py tests/test_cli_search_contracts.py tests/test_cli_add_contracts.py tests/test_cli_sync_contracts.py tests/test_cli_update_contracts.py tests/test_cli_status_contracts.py tests/test_docs.py
git commit -m "test: verify global cache system"
```

If `git status --short` shows no changes after Step 1-6, do not create an empty commit.

---

## Self-review checklist for implementer

- Metadata TTL is exactly 24 hours by default.
- Expired metadata refresh failure warns and uses stale metadata for `list`, `search`, `add`, and `add --all` when stale metadata exists.
- `sync`, `update`, and source-aware `status` refresh by default and fail closed on refresh errors unless `--cached` is explicitly used.
- `--cached` never calls Git, GitHub API, or HTTPS source refresh code.
- `--cached` materialization fails with guidance when metadata or a matching cached skill body is missing.
- Normal/`--refresh` materialization from cached metadata refreshes that repo first on body-cache miss.
- `--refresh` bypasses fresh metadata cache.
- Cached catalog loads verify `catalog_hash` against normalized entries and preserve backend/index-hash provenance when refreshed metadata is cached.
- Non-index sources write the observed content hash back into cached metadata after a successful materialization.
- Skill bodies are copied from cache only when the content hash matches.
- Body cache entries update `last_used_at` and `use_count` on hit and store.
- Lazy pruning respects 30 days unused and 256 MiB body-cache cap.
- All cache reads, writes, summaries, pruning deletes, and body materializations reject symlinked cache roots/entries and use unique temp files/directories followed by atomic replace of complete cache entries.
- Failed source materialization or post-materialization validation removes the destination/temp folder before returning an error.
- Source materialization still surfaces existing project/vault validation errors before hash-mismatch errors where those errors are more specific.
- Read-only and mutating source commands use the same cache manager path, with command-specific default policies.

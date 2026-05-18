from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, cast
import hashlib
import stat
import uuid

from sv.catalog import SourceSkill, normalize_source_relative_path
from sv.config import RepoConfig, SvPaths, repo_source_key
from sv.errors import SvError
from sv.hashformat import SHA256_PREFIX, is_sha256_digest
from sv.hashing import sha256_file, sha256_skill_directory
from sv.materialization import (
    copy_skill_folder_to_temp,
    remove_materialization_path,
    validate_materialization_source_tree,
)
from sv.project import normalize_skill_name
from sv.skills import parse_skill_file
from sv.source import SourceBackend, SourceBackendError
from sv.terminal import escape_terminal_controls
from sv.tomlutil import atomic_write_text, load_toml_document, toml_escape

DEFAULT_METADATA_TTL_SECONDS = 24 * 60 * 60
DEFAULT_SKILL_BODY_MAX_UNUSED_SECONDS = 30 * 24 * 60 * 60
DEFAULT_SKILL_BODY_MAX_BYTES = 256 * 1024 * 1024
CACHE_SCHEMA_VERSION = 1
_MAX_CATALOG_CACHE_BYTES = 1 * 1024 * 1024
_CATALOG_CACHE_DOCUMENT = "sv catalog cache"


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


@dataclass(frozen=True)
class CacheRefreshResult:
    entries: tuple[SourceSkill, ...]
    refreshed_backends_by_repo: Mapping[str, str] = field(default_factory=dict)
    index_hashes_by_repo: Mapping[str, str | None] = field(default_factory=dict)
    refreshed_repo_ids: frozenset[str] | None = None


CacheRefreshValue = CacheRefreshResult | Sequence[SourceSkill]
RefreshCatalog = Callable[[Sequence[RepoConfig]], CacheRefreshValue]
Warn = Callable[[str], None]
Now = Callable[[], datetime]
BackendFactory = Callable[[RepoConfig], Sequence[SourceBackend]]
AfterStore = Callable[[SourceSkill, str, str], None]
RefreshEntryOnBodyMiss = Callable[[SourceSkill], SourceSkill | None]


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
    index_hash: str | None = None
    entries: tuple[CachedCatalogEntry, ...] = ()


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


def load_skill_body_metadata(paths: SvPaths, content_hash: str) -> SkillBodyMetadata:
    path = _skill_body_metadata_path(paths, content_hash)
    _reject_symlinked_cache_dir(path.parent)
    if path.is_symlink():
        raise SvError(
            f"Refusing to read symlinked skill body cache metadata at {path}."
        )
    data = load_toml_document(path, "sv skill body cache metadata")
    metadata = _parse_skill_body_metadata(data, path)
    if metadata.content_hash != content_hash:
        raise SvError(
            f"Invalid skill body cache metadata at {path}: content_hash does not match requested hash."
        )
    return metadata


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
    if not is_sha256_digest(content_hash):
        raise SvError("Skill body cache content hash must be a sha256 digest.")
    validate_materialization_source_tree(source_skill_dir)
    parse_skill_file(source_skill_dir / "SKILL.md", expected_folder=skill_name)
    if (
        sha256_skill_directory(source_skill_dir, expected_name=skill_name)
        != content_hash
    ):
        raise SvError("Skill body cache content hash mismatch for source skill.")

    cache_root = skill_body_cache_path(paths, content_hash)
    _ensure_private_cache_dir(paths.cache_tmp_dir)
    _ensure_private_cache_dir(cache_root.parent)
    _reject_symlinked_cache_dir(cache_root)

    if cache_root.exists():
        if _cached_skill_body_is_valid(paths, content_hash, skill_name):
            _touch_skill_body_cache(paths, content_hash, now)
            return
        remove_materialization_path(cache_root, ignore_errors=True)

    temp_root = _unique_skill_body_temp_root(paths, content_hash)
    try:
        temp_root.mkdir(mode=0o700)
        copied = copy_skill_folder_to_temp(
            source_skill_dir,
            temp_root / "skill",
            error_message="Failed to stage skill body cache",
        )
        parse_skill_file(copied / "SKILL.md", expected_folder=skill_name)
        if sha256_skill_directory(copied, expected_name=skill_name) != content_hash:
            raise SvError("Skill body cache copied content hash mismatch.")
        timestamp = _utc_timestamp(now)
        metadata = SkillBodyMetadata(
            content_hash=content_hash,
            skill_name=skill_name,
            source_reference=source_reference,
            size_bytes=_directory_size(copied),
            created_at=timestamp,
            last_used_at=timestamp,
            use_count=1,
        )
        _save_skill_body_metadata_at(temp_root / "metadata.toml", metadata)
        if sha256_skill_directory(copied, expected_name=skill_name) != content_hash:
            raise SvError("Skill body cache staged content hash mismatch.")
        _reject_symlinked_cache_dir(cache_root)
        temp_root.replace(cache_root)
    except Exception:
        remove_materialization_path(temp_root, ignore_errors=True)
        if cache_root.exists() and not cache_root.is_symlink():
            try:
                if not _cached_skill_body_is_valid(paths, content_hash, skill_name):
                    remove_materialization_path(cache_root, ignore_errors=True)
            except SvError:
                pass
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
    if not _cached_skill_body_is_valid(paths, content_hash, skill_name):
        return False
    if destination.exists() or destination.is_symlink():
        remove_materialization_path(destination)
    try:
        copy_skill_folder_to_temp(
            _skill_body_folder(paths, content_hash),
            destination,
            error_message="Failed to materialize skill from body cache",
        )
        if (
            sha256_skill_directory(destination, expected_name=skill_name)
            != content_hash
        ):
            remove_materialization_path(
                skill_body_cache_path(paths, content_hash), ignore_errors=True
            )
            remove_materialization_path(destination, ignore_errors=True)
            return False
        try:
            _touch_skill_body_cache(paths, content_hash, now)
        except SvError as exc:
            remove_materialization_path(destination, ignore_errors=True)
            if _cache_error_is_symlink_violation(exc):
                raise
            remove_materialization_path(
                skill_body_cache_path(paths, content_hash), ignore_errors=True
            )
            return False
    except SvError:
        remove_materialization_path(destination, ignore_errors=True)
        raise
    return True


def _parse_skill_body_metadata(data: dict[str, Any], path: Path) -> SkillBodyMetadata:
    if not isinstance(data, dict):
        raise SvError(
            f"Invalid skill body cache metadata at {path}: document must be a table."
        )
    version = data.get("schema_version")
    if version != CACHE_SCHEMA_VERSION or isinstance(version, bool):
        raise SvError(
            f"Invalid skill body cache metadata at {path}: schema_version must be {CACHE_SCHEMA_VERSION}."
        )
    created_at = _required_string(data, "created_at", path)
    last_used_at = _required_string(data, "last_used_at", path)
    _parse_utc(created_at, path, "created_at")
    _parse_utc(last_used_at, path, "last_used_at")
    skill_name = normalize_skill_name(_required_string(data, "skill_name", path))
    return SkillBodyMetadata(
        content_hash=_required_hash(data, "content_hash", path),
        skill_name=skill_name,
        source_reference=_required_string(data, "source_reference", path),
        size_bytes=_required_int(data, "size_bytes", path),
        created_at=created_at,
        last_used_at=last_used_at,
        use_count=_required_int(data, "use_count", path),
    )


def _required_int(data: Mapping[str, Any], field: str, path: Path) -> int:
    value = data.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SvError(
            f"Invalid skill body cache metadata at {path}: {field} must be a non-negative integer."
        )
    return value


def _save_skill_body_metadata_at(path: Path, metadata: SkillBodyMetadata) -> None:
    _ensure_private_cache_dir(path.parent)
    text = (
        "\n".join(
            [
                f"schema_version = {CACHE_SCHEMA_VERSION}",
                f'content_hash = "{toml_escape(metadata.content_hash)}"',
                f'skill_name = "{toml_escape(metadata.skill_name)}"',
                f'source_reference = "{toml_escape(metadata.source_reference)}"',
                f"size_bytes = {metadata.size_bytes}",
                f'created_at = "{toml_escape(metadata.created_at)}"',
                f'last_used_at = "{toml_escape(metadata.last_used_at)}"',
                f"use_count = {metadata.use_count}",
            ]
        )
        + "\n"
    )
    atomic_write_text(
        path,
        text,
        document_name="sv skill body cache metadata",
        temp_path_description="skill body cache metadata temporary file",
        create_parent=False,
    )


def _save_skill_body_metadata(paths: SvPaths, metadata: SkillBodyMetadata) -> None:
    _save_skill_body_metadata_at(
        _skill_body_metadata_path(paths, metadata.content_hash), metadata
    )


def _touch_skill_body_cache(paths: SvPaths, content_hash: str, now: datetime) -> None:
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
    paths: SvPaths, content_hash: str, skill_name: str
) -> bool:
    cache_root = skill_body_cache_path(paths, content_hash)
    if cache_root.is_symlink():
        raise SvError(f"Refusing to use symlinked skill body cache at {cache_root}.")
    if not cache_root.exists():
        return False
    try:
        _reject_symlinked_cache_dir(cache_root)
        metadata = load_skill_body_metadata(paths, content_hash)
        if metadata.skill_name != skill_name:
            raise SvError(
                f"Invalid skill body cache metadata at {cache_root}: skill_name does not match requested skill."
            )
        folder = _skill_body_folder(paths, content_hash)
        validate_materialization_source_tree(folder)
        parse_skill_file(folder / "SKILL.md", expected_folder=skill_name)
        if sha256_skill_directory(folder, expected_name=skill_name) != content_hash:
            raise SvError(
                f"Invalid skill body cache at {cache_root}: hash did not match."
            )
    except SvError as exc:
        if _cache_error_is_symlink_violation(exc):
            raise
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
    raise SvError("Failed to allocate unique skill body cache temporary directory.")


def _directory_size(path: Path) -> int:
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_symlink():
                raise SvError(
                    f"Skill body cache source must not contain symlinks; contains a symlink at {entry}."
                )
            if entry.is_file():
                total += entry.stat().st_size
    except OSError as exc:
        raise SvError(
            f"Failed to inspect skill body cache directory {path}: {exc}"
        ) from exc
    return total


def _cache_file_key(repo: RepoConfig) -> str:
    hasher = hashlib.sha256()
    for label, value in (
        ("source_key", repo_source_key(repo.url)),
        ("repo_id", repo.id),
    ):
        _hash_labeled_value(hasher, label, value)
    for skills_path in repo.skills_paths:
        _hash_labeled_value(hasher, "skills_path", skills_path)
    return hasher.hexdigest()


def catalog_cache_path(paths: SvPaths, repo: RepoConfig) -> Path:
    return paths.catalog_cache_dir / f"{_cache_file_key(repo)}.toml"


def load_cached_catalog(
    paths: SvPaths, repo: RepoConfig
) -> CachedCatalogDocument | None:
    path = catalog_cache_path(paths, repo)
    _reject_symlinked_cache_dir(path.parent)
    if path.is_symlink():
        raise SvError(f"Refusing to read symlinked catalog cache file at {path}.")
    if not path.exists():
        return None
    _repair_existing_private_cache_dirs(path.parent)
    try:
        if path.stat().st_size > _MAX_CATALOG_CACHE_BYTES:
            raise SvError(
                f"Failed to read sv catalog cache at {path}: document exceeds size "
                f"limit ({_MAX_CATALOG_CACHE_BYTES} bytes)."
            )
    except OSError as exc:
        raise SvError(f"Failed to inspect sv catalog cache at {path}: {exc}") from exc

    data = load_toml_document(path, _CATALOG_CACHE_DOCUMENT)
    document = _parse_cached_catalog_document(data, path)
    _validate_cached_catalog_matches_repo(document, repo, path)
    if document.catalog_hash != _cached_catalog_hash(document):
        raise SvError(
            f"Invalid sv catalog cache at {path}: catalog_hash does not match document."
        )
    return document


def save_cached_catalog(
    paths: SvPaths, repo: RepoConfig, document: CachedCatalogDocument
) -> None:
    path = catalog_cache_path(paths, repo)
    _validate_cached_catalog_matches_repo(document, repo, path)
    if document.catalog_hash != _cached_catalog_hash(document):
        raise SvError(
            "Refusing to write sv catalog cache with mismatched catalog_hash."
        )
    text = _format_cached_catalog_document(document)
    _ensure_private_cache_dir(path.parent)
    try:
        atomic_write_text(
            path,
            text,
            document_name=_CATALOG_CACHE_DOCUMENT,
            temp_path_description="catalog cache temporary file",
            create_parent=False,
        )
    except SvError as exc:
        if _cache_write_error_must_fail(exc):
            raise
        raise
    _reject_symlinked_cache_dir(path.parent)


def cached_catalog_is_fresh(
    document: CachedCatalogDocument, *, now: datetime, ttl_seconds: int
) -> bool:
    refreshed_at = _parse_utc(
        document.refreshed_at, Path("<cached catalog>"), "refreshed_at"
    )
    age = now.astimezone(UTC) - refreshed_at
    return timedelta(seconds=0) <= age <= timedelta(seconds=ttl_seconds)


def get_catalog_with_cache(
    repos: Sequence[RepoConfig],
    paths: SvPaths,
    *,
    policy: CachePolicy,
    now: datetime,
    refresh_catalog: RefreshCatalog,
    warn: Warn,
) -> list[SourceSkill]:
    documents: dict[str, CachedCatalogDocument] = {}
    cached_entries: list[SourceSkill] = []
    repos_to_refresh: list[RepoConfig] = []

    for repo in repos:
        try:
            document = load_cached_catalog(paths, repo)
        except SvError as exc:
            if (
                _cache_error_is_symlink_violation(exc)
                or policy.mode is CacheMode.CACHE_ONLY
            ):
                raise
            warn(f"warning: ignoring invalid cached metadata for {repo.id}: {exc}")
            repos_to_refresh.append(repo)
            continue

        if document is None:
            if policy.mode is CacheMode.CACHE_ONLY:
                raise SvError(f"No cached metadata found for {repo.id}.")
            repos_to_refresh.append(repo)
            continue

        documents[repo.id] = document
        if policy.mode is CacheMode.CACHE_ONLY:
            cached_entries.extend(
                _source_skills_from_cached_document(repo, paths, document)
            )
            continue
        if policy.mode is CacheMode.FORCE_REFRESH or not cached_catalog_is_fresh(
            document, now=now, ttl_seconds=policy.metadata_ttl_seconds
        ):
            repos_to_refresh.append(repo)
            continue
        cached_entries.extend(
            _source_skills_from_cached_document(repo, paths, document)
        )

    refreshed_entries: list[SourceSkill] = []
    if repos_to_refresh:
        try:
            refresh_result = _coerce_refresh_result(refresh_catalog(repos_to_refresh))
            _ensure_requested_repos_refreshed(refresh_result, repos_to_refresh)
        except SvError as exc:
            if not policy.allow_stale_on_error:
                raise
            fallback_entries: list[SourceSkill] = []
            for repo in repos_to_refresh:
                document = documents.get(repo.id)
                if document is None:
                    raise
                warn(
                    f"warning: using stale cached metadata for {repo.id}; "
                    f"refresh failed: {exc}"
                )
                fallback_entries.extend(
                    _source_skills_from_cached_document(repo, paths, document)
                )
            refreshed_entries = fallback_entries
        else:
            entries_by_repo = _entries_by_repo(refresh_result.entries)
            for repo in repos_to_refresh:
                repo_entries = entries_by_repo.get(repo.id, ())
                backend = refresh_result.refreshed_backends_by_repo.get(repo.id)
                index_hash = refresh_result.index_hashes_by_repo.get(repo.id)
                document = _catalog_document_from_entries(
                    repo,
                    repo_entries,
                    _utc_timestamp(now),
                    backend=backend,
                    index_hash=index_hash,
                )
                try:
                    save_cached_catalog(paths, repo, document)
                except SvError as exc:
                    if _cache_write_error_must_fail(exc):
                        raise
                    warn(
                        f"warning: failed to write cached metadata for {repo.id}: {exc}"
                    )
                refreshed_entries.extend(repo_entries)

    final_entries = [*cached_entries, *refreshed_entries]
    return sorted(
        final_entries,
        key=lambda entry: (entry.name, entry.repo_id, entry.source_relative_path),
    )


def attach_source_materializers(
    catalog: Sequence[SourceSkill],
    repos: Sequence[RepoConfig],
    *,
    backend_factory: BackendFactory,
) -> list[SourceSkill]:
    repos_by_id = {repo.id: repo for repo in repos}
    attached: list[SourceSkill] = []
    for entry in catalog:
        if not entry.source_backend.startswith("cache:"):
            attached.append(entry)
            continue
        repo = repos_by_id.get(entry.repo_id)
        if repo is None:
            raise SvError(
                f"No source repo configured for cached entry {entry.repo_id}."
            )

        def materialize(
            destination: Path,
            *,
            cached_entry: SourceSkill = entry,
            selected_repo: RepoConfig = repo,
        ) -> None:
            failures: list[str] = []
            for backend in backend_factory(selected_repo):
                try:
                    backend.materialize_folder(
                        cached_entry.source_relative_path, destination
                    )
                    return
                except SourceBackendError as exc:
                    failures.append(f"{backend.name}: {exc.detail}")
            details = (
                "; ".join(failures) if failures else "no source backends were available"
            )
            raise SvError(
                f"Failed to materialize {cached_entry.qualified_reference} from source: {details}."
            )

        attached.append(replace(entry, _materializer=materialize))
    return attached


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
        if (
            cached_entry.name == entry.name
            and cached_entry.source_path == entry.source_relative_path
        ):
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
    unhashed = replace(
        document,
        entries=tuple(updated_entries),
        catalog_hash="sha256:" + ("0" * 64),
    )
    save_cached_catalog(
        paths, repo, replace(unhashed, catalog_hash=_cached_catalog_hash(unhashed))
    )


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
        current_time = now()
        if (
            entry.source_content_hash is not None
            and try_materialize_from_skill_body_cache(
                paths,
                content_hash=entry.source_content_hash,
                skill_name=entry.name,
                destination=destination,
                now=current_time,
            )
        ):
            return

        if not allow_source_fallback:
            if entry.source_content_hash is None:
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
                    f"Cached skill body for {entry.qualified_reference} was not found and no source refresh was provided."
                )
            refreshed = refresh_entry_on_body_miss(entry)
            if refreshed is None:
                raise SvError(
                    f"Refreshed source metadata did not include {entry.qualified_reference}."
                )
            _wrap_source_skill(
                refreshed,
                paths,
                now=now,
                after_store=after_store,
                allow_source_fallback=True,
                refresh_entry_on_body_miss=None,
                warn=warn,
            ).materialize_to(destination)
            return

        try:
            original_materialize(destination)
            parse_skill_file(destination / "SKILL.md", expected_folder=entry.name)
            actual_hash = sha256_skill_directory(destination, expected_name=entry.name)
            actual_skill_file_hash = sha256_file(destination / "SKILL.md")
            if (
                entry.source_content_hash is not None
                and actual_hash != entry.source_content_hash
            ):
                raise SvError(
                    f"Source skill {entry.qualified_reference} hash did not match expected {entry.source_content_hash}; got {actual_hash}."
                )
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
                    warn(
                        f"warning: failed to write skill body cache for {entry.qualified_reference}: {exc}"
                    )
            after_store(entry, actual_hash, actual_skill_file_hash)
        except Exception:
            remove_materialization_path(destination, ignore_errors=True)
            raise

    return replace(entry, _materializer=materialize)


def _catalog_document_from_entries(
    repo: RepoConfig,
    entries: Sequence[SourceSkill],
    refreshed_at: str,
    *,
    backend: str | None = None,
    index_hash: str | None = None,
) -> CachedCatalogDocument:
    cached_entries = tuple(
        sorted(
            (
                CachedCatalogEntry(
                    name=normalize_skill_name(entry.name),
                    description=escape_terminal_controls(entry.description),
                    source_path=normalize_source_relative_path(
                        entry.source_relative_path
                    ),
                    content_hash=entry.source_content_hash,
                    skill_file_hash=entry.source_skill_file_hash,
                )
                for entry in entries
            ),
            key=lambda entry: (entry.name, entry.source_path),
        )
    )
    document = CachedCatalogDocument(
        repo_id=repo.id,
        repo_url=repo.url,
        source_key=repo_source_key(repo.url),
        skills_paths=tuple(repo.skills_paths),
        backend=backend or _catalog_backend(entries),
        refreshed_at=refreshed_at,
        catalog_hash="sha256:" + ("0" * 64),
        index_hash=index_hash,
        entries=cached_entries,
    )
    return replace(document, catalog_hash=_cached_catalog_hash(document))


def _catalog_backend(entries: Sequence[SourceSkill]) -> str:
    backends = {entry.source_backend for entry in entries}
    if len(backends) == 1:
        return next(iter(backends))
    return "mixed"


def _source_skills_from_cached_document(
    repo: RepoConfig, paths: SvPaths, document: CachedCatalogDocument
) -> list[SourceSkill]:
    repo_path = paths.source_repo_for(repo.id)
    entries: list[SourceSkill] = []
    for entry in document.entries:
        source_relative_path = normalize_source_relative_path(entry.source_path)
        entries.append(
            SourceSkill(
                name=normalize_skill_name(entry.name),
                description=escape_terminal_controls(entry.description),
                repo_id=repo.id,
                repo_url=repo.url,
                repo_path=repo_path,
                source_path=repo_path / source_relative_path,
                source_relative_path=source_relative_path,
                repo_aliases=repo.aliases,
                source_backend=f"cache:{document.backend}",
                source_content_hash=entry.content_hash,
                source_skill_file_hash=entry.skill_file_hash,
            )
        )
    return entries


def _coerce_refresh_result(value: CacheRefreshValue) -> CacheRefreshResult:
    if isinstance(value, CacheRefreshResult):
        return value
    return CacheRefreshResult(entries=tuple(value))


def _ensure_requested_repos_refreshed(
    refresh_result: CacheRefreshResult, repos_to_refresh: Sequence[RepoConfig]
) -> None:
    if refresh_result.refreshed_repo_ids is None:
        return
    for repo in repos_to_refresh:
        if repo.id not in refresh_result.refreshed_repo_ids:
            raise SvError(f"refresh did not return metadata for {repo.id}")


def _entries_by_repo(
    entries: Sequence[SourceSkill],
) -> dict[str, tuple[SourceSkill, ...]]:
    grouped: dict[str, list[SourceSkill]] = {}
    for entry in entries:
        grouped.setdefault(entry.repo_id, []).append(entry)
    return {repo_id: tuple(repo_entries) for repo_id, repo_entries in grouped.items()}


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return (
        value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _validate_cached_catalog_matches_repo(
    document: CachedCatalogDocument, repo: RepoConfig, path: Path
) -> None:
    expected_source_key = repo_source_key(repo.url)
    expected_skills_paths = tuple(repo.skills_paths)
    mismatches: list[str] = []
    if document.repo_id != repo.id:
        mismatches.append("repo_id")
    if document.repo_url != repo.url:
        mismatches.append("repo_url")
    if document.source_key != expected_source_key:
        mismatches.append("source_key")
    if document.skills_paths != expected_skills_paths:
        mismatches.append("skills_paths")
    if mismatches:
        fields = ", ".join(mismatches)
        raise SvError(
            f"Invalid sv catalog cache at {path}: different repo metadata ({fields})."
        )


def _parse_cached_catalog_document(
    data: dict[str, Any], path: Path
) -> CachedCatalogDocument:
    if not isinstance(data, dict):
        raise SvError(f"Invalid sv catalog cache at {path}: document must be a table.")
    version = data.get("schema_version")
    if version != CACHE_SCHEMA_VERSION or isinstance(version, bool):
        raise SvError(
            f"Invalid sv catalog cache at {path}: schema_version must be {CACHE_SCHEMA_VERSION}."
        )
    skills = data.get("skills")
    if not isinstance(skills, list):
        raise SvError(f"Invalid sv catalog cache at {path}: skills must be a list.")

    refreshed_at = _required_string(data, "refreshed_at", path)
    _parse_utc(refreshed_at, path, "refreshed_at")
    entries = tuple(
        _parse_cached_catalog_entry(item, path, index)
        for index, item in enumerate(skills)
    )
    return CachedCatalogDocument(
        repo_id=_required_string(data, "repo_id", path),
        repo_url=_required_string(data, "repo_url", path),
        source_key=_required_string(data, "source_key", path),
        skills_paths=_string_list(data.get("skills_paths"), "skills_paths", path),
        backend=_required_string(data, "backend", path),
        refreshed_at=refreshed_at,
        catalog_hash=_required_hash(data, "catalog_hash", path),
        index_hash=_optional_hash(data, "index_hash", path),
        entries=entries,
    )


def _parse_cached_catalog_entry(
    item: object, path: Path, index: int
) -> CachedCatalogEntry:
    if not isinstance(item, dict):
        raise SvError(
            f"Invalid sv catalog cache at {path}: skills[{index}] must be a table."
        )
    item_data = cast(dict[str, Any], item)
    name = normalize_skill_name(_required_string(item_data, "name", path, index=index))
    source_path = normalize_source_relative_path(
        _required_string(item_data, "source_path", path, index=index)
    )
    description = escape_terminal_controls(
        _required_string(item_data, "description", path, index=index)
    )
    return CachedCatalogEntry(
        name=name,
        description=description,
        source_path=source_path,
        content_hash=_optional_hash(item_data, "content_hash", path, index=index),
        skill_file_hash=_optional_hash(item_data, "skill_file_hash", path, index=index),
    )


def _required_string(
    data: Mapping[str, Any], field: str, path: Path, *, index: int | None = None
) -> str:
    value = data.get(field)
    if not isinstance(value, str):
        location = f"skills[{index}].{field}" if index is not None else field
        raise SvError(
            f"Invalid sv catalog cache at {path}: {location} must be a string."
        )
    return value


def _string_list(value: object, field: str, path: Path) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SvError(f"Invalid sv catalog cache at {path}: {field} must be a list.")
    strings: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise SvError(
                f"Invalid sv catalog cache at {path}: {field}[{index}] must be a string."
            )
        strings.append(normalize_source_relative_path(item))
    return tuple(strings)


def _required_hash(
    data: Mapping[str, Any], field: str, path: Path, *, index: int | None = None
) -> str:
    value = _required_string(data, field, path, index=index)
    if not is_sha256_digest(value):
        location = f"skills[{index}].{field}" if index is not None else field
        raise SvError(
            f"Invalid sv catalog cache at {path}: {location} must be a sha256 digest."
        )
    return value


def _optional_hash(
    data: Mapping[str, Any], field: str, path: Path, *, index: int | None = None
) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not is_sha256_digest(value):
        location = f"skills[{index}].{field}" if index is not None else field
        raise SvError(
            f"Invalid sv catalog cache at {path}: {location} must be a sha256 digest."
        )
    return value


def _parse_utc(value: str, path: Path, field: str) -> datetime:
    if not value.endswith("Z"):
        raise SvError(
            f"Invalid sv catalog cache at {path}: {field} must be a UTC timestamp ending in Z."
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SvError(
            f"Invalid sv catalog cache at {path}: {field} must be a UTC timestamp."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise SvError(
            f"Invalid sv catalog cache at {path}: {field} must be a UTC timestamp."
        )
    return parsed.astimezone(UTC)


def _format_cached_catalog_document(document: CachedCatalogDocument) -> str:
    lines = [
        f"schema_version = {CACHE_SCHEMA_VERSION}",
        f'repo_id = "{toml_escape(document.repo_id)}"',
        f'repo_url = "{toml_escape(document.repo_url)}"',
        f'source_key = "{toml_escape(document.source_key)}"',
        "skills_paths = ["
        + ", ".join(f'"{toml_escape(path)}"' for path in document.skills_paths)
        + "]",
        f'backend = "{toml_escape(document.backend)}"',
        f'refreshed_at = "{toml_escape(document.refreshed_at)}"',
        f'catalog_hash = "{toml_escape(document.catalog_hash)}"',
    ]
    if document.index_hash is not None:
        lines.append(f'index_hash = "{toml_escape(document.index_hash)}"')
    if not document.entries:
        lines.append("skills = []")
    for entry in document.entries:
        lines.extend(
            [
                "",
                "[[skills]]",
                f'name = "{toml_escape(entry.name)}"',
                f'description = "{toml_escape(entry.description)}"',
                f'source_path = "{toml_escape(entry.source_path)}"',
            ]
        )
        if entry.content_hash is not None:
            lines.append(f'content_hash = "{toml_escape(entry.content_hash)}"')
        if entry.skill_file_hash is not None:
            lines.append(f'skill_file_hash = "{toml_escape(entry.skill_file_hash)}"')
    return "\n".join(lines) + "\n"


def _cached_catalog_hash(document: CachedCatalogDocument) -> str:
    hasher = hashlib.sha256()
    hasher.update(b"sv-cached-catalog-v1\0")
    _hash_labeled_value(hasher, "schema_version", str(CACHE_SCHEMA_VERSION))
    _hash_labeled_value(hasher, "repo_id", document.repo_id)
    _hash_labeled_value(hasher, "repo_url", document.repo_url)
    _hash_labeled_value(hasher, "source_key", document.source_key)
    for skills_path in document.skills_paths:
        _hash_labeled_value(hasher, "skills_path", skills_path)
    _hash_labeled_value(hasher, "backend", document.backend)
    _hash_labeled_value(hasher, "refreshed_at", document.refreshed_at)
    _hash_labeled_value(hasher, "index_hash", document.index_hash or "")
    for entry in document.entries:
        _hash_labeled_value(hasher, "entry.name", entry.name)
        _hash_labeled_value(hasher, "entry.description", entry.description)
        _hash_labeled_value(hasher, "entry.source_path", entry.source_path)
        _hash_labeled_value(hasher, "entry.content_hash", entry.content_hash or "")
        _hash_labeled_value(
            hasher, "entry.skill_file_hash", entry.skill_file_hash or ""
        )
    return "sha256:" + hasher.hexdigest()


def _hash_labeled_value(hasher: Any, label: str, value: str) -> None:
    label_bytes = label.encode("utf-8")
    value_bytes = value.encode("utf-8")
    hasher.update(len(label_bytes).to_bytes(4, "big"))
    hasher.update(label_bytes)
    hasher.update(len(value_bytes).to_bytes(8, "big"))
    hasher.update(value_bytes)


def _toml_string(value: str) -> str:
    return f'"{toml_escape(value)}"'


def _ensure_private_cache_dir(path: Path) -> None:
    _reject_symlinked_cache_dir(path)
    chain = _cache_directory_chain(path)
    _ensure_private_cache_parent(chain[0].parent)
    for directory in chain:
        _reject_symlinked_cache_dir(directory)
        try:
            directory.mkdir(mode=0o700, exist_ok=True)
        except OSError as exc:
            raise SvError(
                f"Failed to create sv catalog cache directory {directory}: {exc}"
            ) from exc
        _restrict_private_cache_dir(directory)
    _reject_symlinked_cache_dir(path)


def _ensure_private_cache_parent(path: Path) -> None:
    try:
        if path.is_symlink():
            raise SvError(
                f"Refusing to use symlinked sv catalog cache directory at {path}."
            )
        path.mkdir(mode=0o700, exist_ok=True)
        if path.is_symlink():
            raise SvError(
                f"Refusing to use symlinked sv catalog cache directory at {path}."
            )
    except SvError:
        raise
    except OSError as exc:
        raise SvError(
            f"Failed to create sv catalog cache directory {path}: {exc}"
        ) from exc


def _repair_existing_private_cache_dirs(path: Path) -> None:
    _reject_symlinked_cache_dir(path)
    for directory in _cache_directory_chain(path):
        if not directory.exists():
            continue
        _restrict_private_cache_dir(directory)
    _reject_symlinked_cache_dir(path)


def _restrict_private_cache_dir(directory: Path) -> None:
    if directory.is_symlink():
        raise SvError(
            f"Refusing to use symlinked sv catalog cache directory at {directory}."
        )
    try:
        directory.chmod(0o700)
        mode = stat.S_IMODE(directory.stat().st_mode)
    except OSError as exc:
        raise SvError(
            f"Failed to restrict sv catalog cache directory {directory}: {exc}"
        ) from exc
    if mode != 0o700:
        raise SvError(
            f"Failed to restrict sv catalog cache directory {directory}: "
            f"mode is {mode:#o}."
        )
    if directory.is_symlink():
        raise SvError(
            f"Refusing to use symlinked sv catalog cache directory at {directory}."
        )


def _cache_directory_chain(path: Path) -> Sequence[Path]:
    parts = path.parts
    for index in range(len(parts) - 1, 0, -1):
        if parts[index] == "cache" and parts[index - 1] == ".sv":
            cache_root = Path(*parts[: index + 1])
            return tuple(
                cache_root.joinpath(*parts[index + 1 : end])
                for end in range(index + 1, len(parts) + 1)
            )
    raise SvError(f"Unsupported path for sv catalog cache directory: {path}.")


def _reject_symlinked_cache_dir(path: Path) -> None:
    for candidate in (*reversed(path.parents), path):
        try:
            if candidate.is_symlink():
                raise SvError(
                    f"Refusing to use symlinked sv catalog cache directory at {candidate}."
                )
        except OSError as exc:
            raise SvError(
                f"Failed to inspect sv catalog cache directory {candidate}: {exc}"
            ) from exc


def _cache_error_is_symlink_violation(error: SvError) -> bool:
    return "symlink" in str(error).lower()


def _cache_write_error_must_fail(error: SvError) -> bool:
    message = str(error).lower()
    fail_closed_phrases = (
        "symlink",
        "unsafe",
        "unsupported path",
        "must not contain",
        "contains a symlink",
        "outside skill directory",
        "different repo",
        "mismatched catalog_hash",
        "content hash mismatch",
        "copied content hash mismatch",
        "staged content hash mismatch",
        "hash did not match",
        "failed validation",
        "exceeds depth",
        "exceeds file",
        "exceeds byte",
    )
    return any(phrase in message for phrase in fail_closed_phrases)

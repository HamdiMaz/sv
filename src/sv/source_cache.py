from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, cast
import hashlib

from sv.catalog import normalize_source_relative_path
from sv.config import RepoConfig, SvPaths, repo_source_key
from sv.errors import SvError
from sv.hashformat import is_sha256_digest
from sv.project import normalize_skill_name
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
    _reject_symlinked_cache_dir(paths)
    if not path.exists():
        return None
    if path.is_symlink():
        raise SvError(f"Refusing to read symlinked catalog cache file at {path}.")
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
    document = _update_cache_hash_field(document)
    text = _format_cached_catalog_document(document)
    _ensure_private_cache_dir(paths)
    try:
        atomic_write_text(
            path,
            text,
            document_name=_CATALOG_CACHE_DOCUMENT,
            temp_path_description="catalog cache temporary file",
        )
    except SvError as exc:
        if _cache_write_error_must_fail(exc):
            raise
        raise
    _reject_symlinked_cache_dir(paths)


def cached_catalog_is_fresh(
    document: CachedCatalogDocument, *, now: datetime, ttl_seconds: int
) -> bool:
    refreshed_at = _parse_utc(
        document.refreshed_at, Path("<cached catalog>"), "refreshed_at"
    )
    age = now.astimezone(UTC) - refreshed_at
    return timedelta(seconds=0) <= age <= timedelta(seconds=ttl_seconds)


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
    document = _update_cache_hash_field(document)
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


def _update_cache_hash_field(document: CachedCatalogDocument) -> CachedCatalogDocument:
    return replace(document, catalog_hash=_cached_catalog_hash(document))


def _toml_string(value: str) -> str:
    return f'"{toml_escape(value)}"'


def _ensure_private_cache_dir(paths: SvPaths) -> None:
    _reject_symlinked_cache_dir(paths)
    for directory in _cache_directory_chain(paths):
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            with suppress(OSError):
                directory.chmod(0o700)
        except OSError as exc:
            raise SvError(
                f"Failed to create sv catalog cache directory {directory}: {exc}"
            ) from exc
        _reject_symlinked_cache_dir(paths)


def _cache_directory_chain(paths: SvPaths) -> Sequence[Path]:
    return (paths.cache_dir, paths.catalog_cache_dir)


def _reject_symlinked_cache_dir(paths: SvPaths) -> None:
    for directory in _cache_directory_chain(paths):
        try:
            if directory.is_symlink():
                raise SvError(
                    f"Refusing to use symlinked sv catalog cache directory at {directory}."
                )
        except OSError as exc:
            raise SvError(
                f"Failed to inspect sv catalog cache directory {directory}: {exc}"
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

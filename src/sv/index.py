from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import os
from pathlib import Path, PurePosixPath
import tomllib
from typing import Any, Literal, cast

from sv.catalog import normalize_source_relative_path
from sv.errors import SvError
from sv.hashing import sha256_file, sha256_skill_directory
from sv.project import normalize_skill_name
from sv.skills import InvalidSkillError, parse_skill_file
from sv.terminal import escape_terminal_controls
from sv.tomlutil import (
    atomic_write_text,
    load_toml_document,
    require_schema_version,
    toml_escape,
)

INDEX_SCHEMA_VERSION = 1
INDEX_DOCUMENT = "sv index"
README_DOCUMENT = "README"
INDEX_SCAN_CONFIG_DOCUMENT = "sv index scan config"
README_SKILLS_START_MARKER = "<!-- sv:skills:start -->"
README_SKILLS_END_MARKER = "<!-- sv:skills:end -->"
IndexKind = Literal["skill-vault", "project-index"]
_VALID_KINDS: tuple[IndexKind, ...] = ("skill-vault", "project-index")
_MAX_INDEX_SKILL_ENTRIES = 5000
_MAX_INDEX_FIELD_LENGTH = 8192


@dataclass(frozen=True)
class IndexSkillEntry:
    name: str
    description: str
    source_path: str
    content_hash: str
    skill_file_hash: str


@dataclass(frozen=True)
class IndexDocument:
    kind: IndexKind
    generated_by: str
    generated_at: str
    skills: tuple[IndexSkillEntry, ...] = field(default_factory=tuple)
    schema_version: int = INDEX_SCHEMA_VERSION


@dataclass(frozen=True)
class IndexScanConfig:
    include_paths: tuple[str, ...] = ()
    exclude_paths: tuple[str, ...] = ()
    schema_version: int = INDEX_SCHEMA_VERSION


def index_path(repo_root: Path) -> Path:
    return repo_root / ".sv" / "index.toml"


def readme_path(repo_root: Path) -> Path:
    return repo_root / "README.md"


def index_scan_config_path(repo_root: Path) -> Path:
    return repo_root / ".sv" / "index-config.toml"


def update_readme_skill_table(path: Path, document: IndexDocument) -> None:
    """Replace or create the sv-generated skill table in a README file."""
    if document.kind != "skill-vault":
        return

    _reject_symlinked_readme_path(path)
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        updated = _replace_or_append_readme_skill_block(text, document.skills)
        atomic_write_text(
            path,
            updated,
            document_name=README_DOCUMENT,
            temp_name=f".{path.name}.sv-tmp",
            temp_path_description="README",
        )
    except SvError:
        raise
    except OSError as exc:
        raise SvError(f"Failed to update {README_DOCUMENT} at {path}: {exc}") from exc


def readme_skill_table_is_fresh(path: Path, document: IndexDocument) -> bool:
    """Return whether README's sv-generated skill table matches a document."""
    if document.kind != "skill-vault":
        return True

    _reject_symlinked_readme_path(path)
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return text == _replace_or_append_readme_skill_block(text, document.skills)
    except SvError:
        raise
    except OSError as exc:
        raise SvError(f"Failed to read {README_DOCUMENT} at {path}: {exc}") from exc


def _replace_or_append_readme_skill_block(
    text: str, skills: Sequence[IndexSkillEntry]
) -> str:
    generated_content = _render_readme_skill_table(skills)
    start_count = text.count(README_SKILLS_START_MARKER)
    end_count = text.count(README_SKILLS_END_MARKER)

    if start_count == 0 and end_count == 0:
        return _append_readme_skill_block(text, generated_content)
    if start_count != 1 or end_count != 1:
        raise SvError(
            "README must contain exactly one sv skill table start marker and "
            "one end marker."
        )

    start_index = text.index(README_SKILLS_START_MARKER) + len(
        README_SKILLS_START_MARKER
    )
    end_index = text.index(README_SKILLS_END_MARKER)
    if end_index < start_index:
        raise SvError("README sv skill table end marker appears before start marker.")

    return f"{text[:start_index]}\n{generated_content}\n{text[end_index:]}"


def _append_readme_skill_block(text: str, generated_content: str) -> str:
    block = (
        f"{README_SKILLS_START_MARKER}\n"
        f"{generated_content}\n"
        f"{README_SKILLS_END_MARKER}\n"
    )
    if not text:
        return block
    if text.endswith("\n\n"):
        return f"{text}{block}"
    if text.endswith("\n"):
        return f"{text}\n{block}"
    return f"{text}\n\n{block}"


def _render_readme_skill_table(skills: Sequence[IndexSkillEntry]) -> str:
    lines = ["| Skill | Description |", "| --- | --- |"]
    for entry in _sorted_entries(list(skills)):
        name = _markdown_table_cell(entry.name)
        description = _markdown_table_cell(entry.description)
        lines.append(f"| {name} | {description} |")
    return "\n".join(lines)


def _markdown_table_cell(value: str) -> str:
    return (
        " ".join(value.split())
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", r"\|")
    )


def _reject_symlinked_readme_path(path: Path) -> None:
    try:
        if path.is_symlink():
            raise SvError(f"Refusing to update symlinked README at {path}.")
    except OSError as exc:
        raise SvError(f"Failed to inspect README path {path}: {exc}") from exc


def scan_repo_for_index(
    repo_root: Path,
    *,
    kind: IndexKind = "project-index",
    generated_at: str | None = None,
    warn: Callable[[str], None] | None = None,
    include_paths: Sequence[str] = (),
    exclude_paths: Sequence[str] = (),
) -> IndexDocument:
    """Recursively scan a repository for valid skills and build an index document."""
    root = repo_root.resolve()
    includes = _normalize_scan_paths(include_paths, field="include_paths")
    excludes = _normalize_scan_paths(exclude_paths, field="exclude_paths")
    entries: list[IndexSkillEntry] = []
    for skill_file in _iter_candidate_skill_files(root, includes, excludes):
        skill_dir = skill_file.parent
        try:
            metadata = parse_skill_file(skill_file, expected_folder=skill_dir.name)
            entries.append(
                IndexSkillEntry(
                    name=metadata.name,
                    description=metadata.description,
                    source_path=_repo_relative_path(skill_dir, root),
                    content_hash=sha256_skill_directory(skill_dir),
                    skill_file_hash=sha256_file(skill_file),
                )
            )
        except InvalidSkillError as exc:
            _warn_invalid_skill(warn, skill_dir, exc)
        except SvError as exc:
            if _is_filesystem_error(exc):
                raise
            _warn_invalid_skill(warn, skill_dir, exc)

    return IndexDocument(
        kind=kind,
        generated_by="sv",
        generated_at=generated_at or _utc_now(),
        skills=tuple(sorted(entries, key=lambda entry: entry.source_path)),
    )


_SKIPPED_SCAN_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".sv",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".tox",
    ".nox",
    ".cache",
    "htmlcov",
    "coverage",
    "target",
    "out",
    ".next",
    ".nuxt",
    ".turbo",
    ".parcel-cache",
}


def _iter_candidate_skill_files(
    root: Path, include_paths: Sequence[str], exclude_paths: Sequence[str]
) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()
    scan_roots = include_paths or ("",)
    for relative_root in scan_roots:
        path = root if not relative_root else root / relative_root
        if _is_excluded(relative_root, exclude_paths):
            continue
        _reject_unsafe_include_path(path, root)
        if not path.exists():
            continue
        if path.is_file():
            if path.name == "SKILL.md" and not _is_excluded(relative_root, exclude_paths):
                _append_candidate(path, root, candidates, seen)
            continue
        _collect_candidate_skill_files(path, root, candidates, exclude_paths, seen)
    return candidates


def _append_candidate(
    path: Path, root: Path, candidates: list[Path], seen: set[str]
) -> None:
    relative_path = _repo_relative_path(path, root)
    if relative_path in seen:
        return
    seen.add(relative_path)
    candidates.append(path)


def _collect_candidate_skill_files(
    path: Path,
    root: Path,
    candidates: list[Path],
    exclude_paths: Sequence[str],
    seen: set[str],
) -> None:
    try:
        with os.scandir(path) as entries:
            sorted_entries = sorted(entries, key=lambda entry: entry.name)
            for entry in sorted_entries:
                child = Path(entry.path)
                relative_child = _repo_relative_path(child, root)
                if _is_excluded(relative_child, exclude_paths):
                    continue
                if entry.name == "SKILL.md":
                    _append_candidate(child, root, candidates, seen)
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError as exc:
                    raise SvError(f"Failed to inspect path {child}: {exc}") from exc
                if is_dir:
                    if entry.name in _SKIPPED_SCAN_DIRS:
                        continue
                    _collect_candidate_skill_files(child, root, candidates, exclude_paths, seen)
    except OSError as exc:
        raise SvError(f"Failed to scan directory {path}: {exc}") from exc


def _reject_unsafe_include_path(path: Path, root: Path) -> None:
    _reject_symlinked_scan_path(path, root)
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise SvError(f"Failed to inspect include path {path}: {exc}") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SvError(
            f"Refusing to scan include path outside repository root: {path}."
        ) from exc


def _reject_symlinked_scan_path(path: Path, root: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise SvError(
            f"Refusing to scan include path outside repository root: {path}."
        ) from exc
    current = root
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                raise SvError(f"Refusing to scan symlinked include path at {current}.")
        except OSError as exc:
            raise SvError(f"Failed to inspect include path {current}: {exc}") from exc


def _normalize_scan_paths(paths: Sequence[str], *, field: str) -> tuple[str, ...]:
    normalized: list[str] = []
    for path in paths:
        if not isinstance(path, str):
            raise SvError(f"Invalid {field}: entries must be strings.")
        try:
            normalized_path = normalize_source_relative_path(path)
        except SvError as exc:
            raise SvError(f"Invalid {field} entry {path!r}: {exc}") from exc
        if normalized_path not in normalized:
            normalized.append(normalized_path)
    return tuple(normalized)


def _is_excluded(path: str, exclude_paths: Sequence[str]) -> bool:
    return any(path == excluded or path.startswith(f"{excluded}/") for excluded in exclude_paths)


def _repo_relative_path(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise SvError(f"Path {path} is outside repository root {root}.") from exc
    return normalize_source_relative_path(relative.as_posix())


def _warn_invalid_skill(
    warn: Callable[[str], None] | None, skill_dir: Path, error: Exception
) -> None:
    if warn is None:
        return
    warn(f"warning: skipping invalid skill at {_escape_control_characters(str(skill_dir))}: {_escape_control_characters(str(error))}")


def _is_filesystem_error(error: SvError) -> bool:
    message = str(error)
    return message.startswith(("Failed to scan", "Failed to inspect", "Failed to read"))


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _escape_control_characters(value: str) -> str:
    return escape_terminal_controls(value)


def load_index(path: Path) -> IndexDocument:
    raw_data = load_toml_document(path, INDEX_DOCUMENT)
    return _index_document_from_data(raw_data, path)


def load_index_scan_config(repo_root: Path) -> IndexScanConfig:
    path = index_scan_config_path(repo_root)
    if not path.exists():
        return IndexScanConfig()
    raw_data = load_toml_document(path, INDEX_SCAN_CONFIG_DOCUMENT)
    if "schema_version" not in raw_data:
        raise SvError(
            f"Invalid {INDEX_SCAN_CONFIG_DOCUMENT} at {path}: missing 'schema_version'."
        )
    data = require_schema_version(
        raw_data,
        path=path,
        document_name=INDEX_SCAN_CONFIG_DOCUMENT,
        current_version=INDEX_SCHEMA_VERSION,
    )
    return IndexScanConfig(
        include_paths=_parse_scan_path_list(data.get("include_paths", []), "include_paths", path),
        exclude_paths=_parse_scan_path_list(data.get("exclude_paths", []), "exclude_paths", path),
    )


def load_index_bytes(content: bytes, path: Path) -> IndexDocument:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SvError(f"Failed to read {INDEX_DOCUMENT} at {path}: not valid UTF-8") from exc
    try:
        raw_data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SvError(f"Failed to read {INDEX_DOCUMENT} at {path}: {exc}") from exc
    return _index_document_from_data(raw_data, path)


def _index_document_from_data(raw_data: dict[str, Any], path: Path) -> IndexDocument:
    if "schema_version" not in raw_data:
        raise SvError(
            f"Invalid {INDEX_DOCUMENT} at {path}: missing 'schema_version'."
        )
    data = require_schema_version(
        raw_data,
        path=path,
        document_name=INDEX_DOCUMENT,
        current_version=INDEX_SCHEMA_VERSION,
    )

    kind = _expect_kind(data.get("kind"), path)
    generated_by = _expect_top_level_string(data.get("generated_by"), "generated_by", path)
    generated_at = _expect_top_level_string(data.get("generated_at"), "generated_at", path)
    skills = _parse_skill_entries(data.get("skills", []), path)
    return IndexDocument(
        schema_version=INDEX_SCHEMA_VERSION,
        kind=kind,
        generated_by=generated_by,
        generated_at=generated_at,
        skills=skills,
    )


def save_index(path: Path, document: IndexDocument) -> None:
    entries = _validate_document(document, path)
    try:
        _reject_symlinked_index_dir(path.parent)
        lines: list[str] = [f"schema_version = {INDEX_SCHEMA_VERSION}"]
        lines.append(f'kind = "{toml_escape(document.kind)}"')
        lines.append(f'generated_by = "{toml_escape(document.generated_by)}"')
        lines.append(f'generated_at = "{toml_escape(document.generated_at)}"')
        for entry in _sorted_entries(entries):
            lines.append("")
            lines.append("[[skills]]")
            _append_skill_entry(lines, entry)
        atomic_write_text(
            path,
            "\n".join(lines) + "\n",
            document_name=INDEX_DOCUMENT,
            temp_name=f"{path.name}.tmp",
            temp_path_description="index",
        )
    except SvError:
        raise
    except OSError as exc:
        raise SvError(f"Failed to write {INDEX_DOCUMENT} at {path}: {exc}") from exc


def _parse_scan_path_list(raw_paths: Any, field: str, path: Path) -> tuple[str, ...]:
    if not isinstance(raw_paths, list):
        raise SvError(
            f"Invalid {INDEX_SCAN_CONFIG_DOCUMENT} at {path}: {field} must be a list."
        )
    paths: list[str] = []
    for index, raw_path in enumerate(raw_paths, start=1):
        if not isinstance(raw_path, str):
            raise SvError(
                f"Invalid {INDEX_SCAN_CONFIG_DOCUMENT} at {path}: "
                f"{field}[{index}] must be a string."
            )
        try:
            normalized_path = normalize_source_relative_path(raw_path)
        except SvError as exc:
            raise SvError(
                f"Invalid {INDEX_SCAN_CONFIG_DOCUMENT} at {path}: "
                f"{field}[{index}] is invalid: {exc}"
            ) from exc
        if normalized_path not in paths:
            paths.append(normalized_path)
    return tuple(paths)


def _parse_skill_entries(raw_skills: Any, path: Path) -> tuple[IndexSkillEntry, ...]:
    if not isinstance(raw_skills, list):
        raise SvError(f"Invalid {INDEX_DOCUMENT} at {path}: skills must be a list.")
    if len(raw_skills) > _MAX_INDEX_SKILL_ENTRIES:
        raise SvError(
            f"Invalid {INDEX_DOCUMENT} at {path}: skills exceeds skill entry limit "
            f"({_MAX_INDEX_SKILL_ENTRIES})."
        )

    entries: list[IndexSkillEntry] = []
    for index, item in enumerate(raw_skills, start=1):
        if not isinstance(item, dict):
            raise SvError(
                f"Invalid {INDEX_DOCUMENT} at {path}: skills[{index}] must be a table."
            )
        skill_item = cast("dict[str, Any]", item)
        source_path = _expect_skill_string(
            skill_item.get("source_path"), "source_path", index, path
        )
        try:
            normalized_source_path = normalize_source_relative_path(source_path)
        except SvError as exc:
            raise SvError(
                f"Invalid {INDEX_DOCUMENT} at {path}: skill entry {index} field 'source_path' is invalid: {exc}"
            ) from exc
        name = _expect_skill_string(skill_item.get("name"), "name", index, path)
        normalized_name = _normalize_index_skill_name(name, index, path)
        _validate_source_path_matches_name(
            normalized_source_path, normalized_name, index, path
        )
        entries.append(
            IndexSkillEntry(
                name=normalized_name,
                description=_expect_skill_string(
                    skill_item.get("description"), "description", index, path
                ),
                source_path=normalized_source_path,
                content_hash=_expect_skill_string(
                    skill_item.get("content_hash"), "content_hash", index, path
                ),
                skill_file_hash=_expect_skill_string(
                    skill_item.get("skill_file_hash"), "skill_file_hash", index, path
                ),
            )
        )
    return tuple(entries)


def _append_skill_entry(lines: list[str], entry: IndexSkillEntry) -> None:
    lines.append(f'name = "{toml_escape(entry.name)}"')
    lines.append(f'description = "{toml_escape(entry.description)}"')
    lines.append(f'source_path = "{toml_escape(entry.source_path)}"')
    lines.append(f'content_hash = "{toml_escape(entry.content_hash)}"')
    lines.append(f'skill_file_hash = "{toml_escape(entry.skill_file_hash)}"')


def _sorted_entries(entries: list[IndexSkillEntry]) -> list[IndexSkillEntry]:
    return sorted(
        entries,
        key=lambda entry: (
            entry.name,
            entry.source_path,
            entry.description,
            entry.content_hash,
            entry.skill_file_hash,
        ),
    )


def _validate_document(document: IndexDocument, path: Path) -> list[IndexSkillEntry]:
    if document.schema_version != INDEX_SCHEMA_VERSION:
        raise SvError(
            f"Invalid {INDEX_DOCUMENT} at {path}: schema_version must be {INDEX_SCHEMA_VERSION}."
        )
    _expect_kind(document.kind, path)
    _validate_top_level_string(document.generated_by, "generated_by", path)
    _validate_top_level_string(document.generated_at, "generated_at", path)
    if len(document.skills) > _MAX_INDEX_SKILL_ENTRIES:
        raise SvError(
            f"Invalid {INDEX_DOCUMENT} at {path}: skills exceeds skill entry limit "
            f"({_MAX_INDEX_SKILL_ENTRIES})."
        )
    entries: list[IndexSkillEntry] = []
    for index, entry in enumerate(document.skills, start=1):
        _validate_skill_string(entry.name, "name", index, path)
        _validate_skill_string(entry.description, "description", index, path)
        _validate_skill_string(entry.content_hash, "content_hash", index, path)
        _validate_skill_string(entry.skill_file_hash, "skill_file_hash", index, path)
        _validate_skill_string(entry.source_path, "source_path", index, path)
        normalized_name = _normalize_index_skill_name(entry.name, index, path)
        try:
            source_path = normalize_source_relative_path(entry.source_path)
        except SvError as exc:
            raise SvError(
                f"Invalid {INDEX_DOCUMENT} at {path}: skill entry {index} field 'source_path' is invalid: {exc}"
            ) from exc
        _validate_source_path_matches_name(source_path, normalized_name, index, path)
        entries.append(
            IndexSkillEntry(
                name=normalized_name,
                description=entry.description,
                source_path=source_path,
                content_hash=entry.content_hash,
                skill_file_hash=entry.skill_file_hash,
            )
        )
    return entries


def _normalize_index_skill_name(name: str, index: int, path: Path) -> str:
    try:
        return normalize_skill_name(name)
    except SvError as exc:
        raise SvError(
            f"Invalid {INDEX_DOCUMENT} at {path}: skill entry {index} field 'name' is invalid: {exc}"
        ) from exc


def _validate_source_path_matches_name(
    source_path: str, skill_name: str, index: int, path: Path
) -> None:
    if PurePosixPath(source_path).name == skill_name:
        return
    raise SvError(
        f"Invalid {INDEX_DOCUMENT} at {path}: skill entry {index} source_path "
        f"'{source_path}' does not match skill name '{skill_name}'."
    )


def _expect_kind(value: Any, path: Path) -> IndexKind:
    if not isinstance(value, str) or value not in _VALID_KINDS:
        raise SvError(
            f"Invalid {INDEX_DOCUMENT} at {path}: kind must be 'skill-vault' or 'project-index'."
        )
    return cast("IndexKind", value)


def _expect_top_level_string(value: Any, field: str, path: Path) -> str:
    if not isinstance(value, str):
        raise SvError(
            f"Invalid {INDEX_DOCUMENT} at {path}: field {field!r} must be a string."
        )
    _validate_index_field_length(value, field, path)
    return value


def _validate_top_level_string(value: str, field: str, path: Path) -> None:
    if not isinstance(value, str):
        raise SvError(
            f"Invalid {INDEX_DOCUMENT} at {path}: field {field!r} must be a string."
        )
    _validate_index_field_length(value, field, path)


def _expect_skill_string(value: Any, field: str, index: int, path: Path) -> str:
    if not isinstance(value, str):
        raise SvError(
            f"Invalid {INDEX_DOCUMENT} at {path}: skill entry {index} field {field!r} must be a string."
        )
    _validate_index_field_length(value, field, path, index=index)
    return value


def _validate_skill_string(value: str, field: str, index: int, path: Path) -> None:
    if not isinstance(value, str):
        raise SvError(
            f"Invalid {INDEX_DOCUMENT} at {path}: skill entry {index} field {field!r} must be a string."
        )
    _validate_index_field_length(value, field, path, index=index)


def _validate_index_field_length(
    value: str, field: str, path: Path, *, index: int | None = None
) -> None:
    if len(value) <= _MAX_INDEX_FIELD_LENGTH:
        return
    location = f"field {field!r}"
    if index is not None:
        location = f"skill entry {index} field {field!r}"
    raise SvError(
        f"Invalid {INDEX_DOCUMENT} at {path}: {location} exceeds field length limit "
        f"({_MAX_INDEX_FIELD_LENGTH})."
    )


def _reject_symlinked_index_dir(path: Path) -> None:
    try:
        if path.is_symlink():
            raise SvError(f"Refusing to use symlinked sv index directory at {path}.")
    except OSError as exc:
        raise SvError(f"Failed to inspect sv index directory {path}: {exc}") from exc

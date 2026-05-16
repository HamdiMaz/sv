from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from sv.errors import SvError
from sv.tomlutil import (
    atomic_write_text,
    load_toml_document,
    require_schema_version,
    toml_escape,
)

if TYPE_CHECKING:
    from sv.config import SvPaths

MANIFEST_SCHEMA_VERSION = 1
GLOBAL_MANIFEST_DOCUMENT = "sv global manifest"
CANONICAL_MANIFEST_DOCUMENT = "sv project manifest"
LEGACY_MANIFEST_DOCUMENT = "sv manifest"


@dataclass(frozen=True)
class GlobalSourceState:
    repo_id: str
    repo_url: str
    backend: str | None = None
    last_refresh_started_at: str | None = None
    last_refresh_finished_at: str | None = None
    last_refresh_status: str | None = None
    last_refresh_error: str | None = None
    source_commit: str | None = None
    source_tree: str | None = None
    index_hash: str | None = None
    catalog_hash: str | None = None
    catalog_skill_count: int | None = None
    health_status: str | None = None
    health_details: str | None = None


@dataclass(frozen=True)
class ManifestEntry:
    name: str
    repo_id: str
    repo_url: str
    source_path: str
    description: str
    target_kind: str = "project-agent"
    target_agent: str | None = "pi"
    target_path: str | None = None
    source_backend: str | None = None
    source_commit: str | None = None
    source_tree: str | None = None
    source_content_hash: str | None = None
    source_skill_file_hash: str | None = None
    installed_content_hash: str | None = None
    local_content_hash: str | None = None
    orphan: bool = False
    modified: bool = False
    update_available: bool = False

    def __post_init__(self) -> None:
        if self.target_path is None:
            object.__setattr__(self, "target_path", f".pi/skills/{self.name}")


def global_manifest_path(paths: SvPaths) -> Path:
    return paths.global_manifest_file


def load_global_manifest(paths: SvPaths) -> dict[str, GlobalSourceState]:
    path = global_manifest_path(paths)
    if not path.is_file():
        return {}

    data = require_schema_version(
        load_toml_document(path, GLOBAL_MANIFEST_DOCUMENT),
        path=path,
        document_name=GLOBAL_MANIFEST_DOCUMENT,
        current_version=MANIFEST_SCHEMA_VERSION,
    )
    if "skills" in data:
        raise SvError(
            f"Invalid {GLOBAL_MANIFEST_DOCUMENT} at {path}: must not contain project skill state."
        )
    return _parse_global_source_states(data.get("sources", []), path)


def _parse_global_source_states(
    raw_sources: Any, path: Path
) -> dict[str, GlobalSourceState]:
    if not isinstance(raw_sources, list):
        raise SvError(
            f"Invalid {GLOBAL_MANIFEST_DOCUMENT} at {path}: sources must be a list."
        )

    states: dict[str, GlobalSourceState] = {}
    for index, item in enumerate(raw_sources, start=1):
        if not isinstance(item, dict):
            raise SvError(
                f"Invalid {GLOBAL_MANIFEST_DOCUMENT} at {path}: sources[{index}] must be a table."
            )
        source_item = cast("dict[str, Any]", item)
        state = GlobalSourceState(
            repo_id=_expect_global_string(
                source_item.get("repo_id"), "repo_id", index, path
            ),
            repo_url=_expect_global_string(
                source_item.get("repo_url"), "repo_url", index, path
            ),
            backend=_optional_global_string(
                source_item.get("backend"), "backend", index, path
            ),
            last_refresh_started_at=_optional_global_string(
                source_item.get("last_refresh_started_at"),
                "last_refresh_started_at",
                index,
                path,
            ),
            last_refresh_finished_at=_optional_global_string(
                source_item.get("last_refresh_finished_at"),
                "last_refresh_finished_at",
                index,
                path,
            ),
            last_refresh_status=_optional_global_string(
                source_item.get("last_refresh_status"),
                "last_refresh_status",
                index,
                path,
            ),
            last_refresh_error=_optional_global_string(
                source_item.get("last_refresh_error"),
                "last_refresh_error",
                index,
                path,
            ),
            source_commit=_optional_global_string(
                source_item.get("source_commit"), "source_commit", index, path
            ),
            source_tree=_optional_global_string(
                source_item.get("source_tree"), "source_tree", index, path
            ),
            index_hash=_optional_global_string(
                source_item.get("index_hash"), "index_hash", index, path
            ),
            catalog_hash=_optional_global_string(
                source_item.get("catalog_hash"), "catalog_hash", index, path
            ),
            catalog_skill_count=_optional_global_int(
                source_item.get("catalog_skill_count"),
                "catalog_skill_count",
                index,
                path,
            ),
            health_status=_optional_global_string(
                source_item.get("health_status"), "health_status", index, path
            ),
            health_details=_optional_global_string(
                source_item.get("health_details"), "health_details", index, path
            ),
        )
        states[state.repo_id] = state
    return states


def save_global_manifest(
    paths: SvPaths, states: dict[str, GlobalSourceState]
) -> None:
    path = global_manifest_path(paths)
    try:
        _reject_symlinked_manifest_dir(path.parent)
        lines: list[str] = [f"schema_version = {MANIFEST_SCHEMA_VERSION}"]
        for state in sorted(states.values(), key=lambda item: item.repo_id):
            lines.append("")
            lines.append("[[sources]]")
            _append_global_source_state(lines, state)
        atomic_write_text(
            path,
            "\n".join(lines) + "\n",
            document_name=GLOBAL_MANIFEST_DOCUMENT,
            temp_name=f"{path.name}.tmp",
            temp_path_description="global manifest",
        )
    except SvError:
        raise
    except OSError as exc:
        raise SvError(f"Failed to write sv global manifest at {path}: {exc}") from exc


def _append_global_source_state(lines: list[str], state: GlobalSourceState) -> None:
    lines.append(f'repo_id = "{toml_escape(state.repo_id)}"')
    lines.append(f'repo_url = "{toml_escape(state.repo_url)}"')
    _append_optional_string(lines, "backend", state.backend)
    _append_optional_string(
        lines, "last_refresh_started_at", state.last_refresh_started_at
    )
    _append_optional_string(
        lines, "last_refresh_finished_at", state.last_refresh_finished_at
    )
    _append_optional_string(lines, "last_refresh_status", state.last_refresh_status)
    _append_optional_string(lines, "last_refresh_error", state.last_refresh_error)
    _append_optional_string(lines, "source_commit", state.source_commit)
    _append_optional_string(lines, "source_tree", state.source_tree)
    _append_optional_string(lines, "index_hash", state.index_hash)
    _append_optional_string(lines, "catalog_hash", state.catalog_hash)
    if state.catalog_skill_count is not None:
        lines.append(f"catalog_skill_count = {state.catalog_skill_count}")
    _append_optional_string(lines, "health_status", state.health_status)
    _append_optional_string(lines, "health_details", state.health_details)


def _expect_global_string(value: Any, field: str, index: int, path: Path) -> str:
    if not isinstance(value, str):
        raise SvError(
            f"Invalid {GLOBAL_MANIFEST_DOCUMENT} at {path}: source entry {index} field {field!r} must be a string."
        )
    return value


def _optional_global_string(
    value: Any, field: str, index: int, path: Path
) -> str | None:
    if value is None:
        return None
    return _expect_global_string(value, field, index, path)


def _optional_global_int(value: Any, field: str, index: int, path: Path) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise SvError(
            f"Invalid {GLOBAL_MANIFEST_DOCUMENT} at {path}: source entry {index} field {field!r} must be an integer."
        )
    return value


def project_manifest_path(project_root: Path) -> Path:
    return project_root / ".sv" / "manifest.toml"


def manifest_path(project_skills_dir: Path) -> Path:
    return project_manifest_path(_project_root_for_skills_dir(project_skills_dir))


def legacy_manifest_path(project_skills_dir: Path) -> Path:
    project_root = _project_root_for_skills_dir(project_skills_dir)
    return project_root / ".pi" / "skills" / ".sv-manifest.toml"


def load_manifest(project_skills_dir: Path) -> dict[str, ManifestEntry]:
    path = manifest_path(project_skills_dir)
    document_name = CANONICAL_MANIFEST_DOCUMENT
    if not path.is_file():
        if not _should_check_legacy_manifest(project_skills_dir):
            return {}
        path = legacy_manifest_path(project_skills_dir)
        document_name = LEGACY_MANIFEST_DOCUMENT
        if not path.is_file():
            return {}

    data = require_schema_version(
        load_toml_document(path, document_name),
        path=path,
        document_name=document_name,
        current_version=MANIFEST_SCHEMA_VERSION,
    )

    return _parse_manifest_entries(data.get("skills", []), path, document_name)


def _parse_manifest_entries(
    raw_skills: Any, path: Path, document_name: str
) -> dict[str, ManifestEntry]:
    if not isinstance(raw_skills, list):
        raise SvError(f"Invalid {document_name} at {path}: skills must be a list.")

    entries: dict[str, ManifestEntry] = {}
    for index, item in enumerate(raw_skills, start=1):
        if not isinstance(item, dict):
            raise SvError(
                f"Invalid {document_name} at {path}: skills[{index}] must be a table."
            )
        skill_item = cast("dict[str, Any]", item)
        try:
            name = _expect_string(
                skill_item["name"], "name", index, path, document_name
            )
            raw_source_path = skill_item.get("source_path")
            source_path = (
                f"skills/{name}"
                if raw_source_path is None
                else _expect_string(
                    raw_source_path, "source_path", index, path, document_name
                )
            )
            target_kind = _expect_string(
                skill_item.get("target_kind", "project-agent"),
                "target_kind",
                index,
                path,
                document_name,
            )
            default_target_agent = None if target_kind == "skill-vault" else "pi"
            default_target_path = (
                f"skills/{name}"
                if target_kind == "skill-vault"
                else f".pi/skills/{name}"
            )
            entry = ManifestEntry(
                name=name,
                repo_id=_expect_string(
                    *_required_alias(
                        skill_item,
                        ("source_repo_id", "repo_id"),
                        "source_repo_id",
                        index,
                        path,
                        document_name,
                    ),
                    index,
                    path,
                    document_name,
                ),
                repo_url=_expect_string(
                    *_required_alias(
                        skill_item,
                        ("source_repo_url", "repo_url"),
                        "source_repo_url",
                        index,
                        path,
                        document_name,
                    ),
                    index,
                    path,
                    document_name,
                ),
                source_path=source_path,
                description=_expect_string(
                    skill_item.get("description", ""),
                    "description",
                    index,
                    path,
                    document_name,
                ),
                target_kind=target_kind,
                target_agent=_optional_string(
                    skill_item.get("target_agent", default_target_agent),
                    "target_agent",
                    index,
                    path,
                    document_name,
                ),
                target_path=_optional_string(
                    skill_item.get("target_path", default_target_path),
                    "target_path",
                    index,
                    path,
                    document_name,
                ),
                source_backend=_optional_string(
                    skill_item.get("source_backend"),
                    "source_backend",
                    index,
                    path,
                    document_name,
                ),
                source_commit=_optional_string(
                    skill_item.get("source_commit"),
                    "source_commit",
                    index,
                    path,
                    document_name,
                ),
                source_tree=_optional_string(
                    skill_item.get("source_tree"),
                    "source_tree",
                    index,
                    path,
                    document_name,
                ),
                source_content_hash=_optional_string(
                    skill_item.get("source_content_hash"),
                    "source_content_hash",
                    index,
                    path,
                    document_name,
                ),
                source_skill_file_hash=_optional_string(
                    skill_item.get("source_skill_file_hash"),
                    "source_skill_file_hash",
                    index,
                    path,
                    document_name,
                ),
                installed_content_hash=_optional_string(
                    skill_item.get("installed_content_hash"),
                    "installed_content_hash",
                    index,
                    path,
                    document_name,
                ),
                local_content_hash=_optional_string(
                    skill_item.get("local_content_hash"),
                    "local_content_hash",
                    index,
                    path,
                    document_name,
                ),
                orphan=_optional_bool(
                    skill_item.get("orphan", False),
                    "orphan",
                    index,
                    path,
                    document_name,
                ),
                modified=_optional_bool(
                    skill_item.get("modified", False),
                    "modified",
                    index,
                    path,
                    document_name,
                ),
                update_available=_optional_bool(
                    skill_item.get("update_available", False),
                    "update_available",
                    index,
                    path,
                    document_name,
                ),
            )
        except KeyError as exc:
            raise SvError(
                f"Invalid {document_name} at {path}: skill entry {index} is missing {exc.args[0]!r}."
            ) from exc
        key = _manifest_entry_key(entry, entries)
        entries[key] = entry
    return entries


def _manifest_entry_key(
    entry: ManifestEntry, existing_entries: dict[str, ManifestEntry]
) -> str:
    if entry.name not in existing_entries:
        return entry.name
    base_key = ":".join(
        (
            entry.target_kind,
            entry.target_agent or "",
            entry.target_path or "",
            entry.name,
        )
    )
    key = base_key
    suffix = 2
    while key in existing_entries:
        key = f"{base_key}:{suffix}"
        suffix += 1
    return key


def _required_alias(
    item: dict[str, Any],
    field_names: tuple[str, ...],
    display_field: str,
    index: int,
    path: Path,
    document_name: str,
) -> tuple[Any, str]:
    for field_name in field_names:
        if field_name in item:
            return item[field_name], field_name
    raise SvError(
        f"Invalid {document_name} at {path}: skill entry {index} is missing {display_field!r}."
    )


def _expect_string(
    value: Any, field: str, index: int, path: Path, document_name: str
) -> str:
    if not isinstance(value, str):
        raise SvError(
            f"Invalid {document_name} at {path}: skill entry {index} field {field!r} must be a string."
        )
    return value


def _optional_string(
    value: Any, field: str, index: int, path: Path, document_name: str
) -> str | None:
    if value is None:
        return None
    return _expect_string(value, field, index, path, document_name)


def _optional_bool(
    value: Any, field: str, index: int, path: Path, document_name: str
) -> bool:
    if not isinstance(value, bool):
        raise SvError(
            f"Invalid {document_name} at {path}: skill entry {index} field {field!r} must be a boolean."
        )
    return value


def save_manifest(project_skills_dir: Path, entries: dict[str, ManifestEntry]) -> None:
    path = manifest_path(project_skills_dir)
    try:
        _reject_symlinked_manifest_dir(path.parent)
        lines: list[str] = [f"schema_version = {MANIFEST_SCHEMA_VERSION}"]
        for entry in (entries[name] for name in sorted(entries)):
            lines.append("")
            lines.append("[[skills]]")
            _append_entry(lines, entry)
        atomic_write_text(
            path,
            "\n".join(lines) + "\n",
            document_name="sv manifest",
            temp_name=f"{path.name}.tmp",
            temp_path_description="manifest",
        )
    except SvError:
        raise
    except OSError as exc:
        raise SvError(f"Failed to write sv manifest at {path}: {exc}") from exc


def _append_entry(lines: list[str], entry: ManifestEntry) -> None:
    lines.append(f'name = "{toml_escape(entry.name)}"')
    lines.append(f'target_kind = "{toml_escape(entry.target_kind)}"')
    _append_optional_string(lines, "target_agent", entry.target_agent)
    _append_optional_string(lines, "target_path", entry.target_path)
    lines.append(f'source_repo_id = "{toml_escape(entry.repo_id)}"')
    lines.append(f'source_repo_url = "{toml_escape(entry.repo_url)}"')
    lines.append(f'source_path = "{toml_escape(entry.source_path)}"')
    lines.append(f'description = "{toml_escape(entry.description)}"')
    _append_optional_string(lines, "source_backend", entry.source_backend)
    _append_optional_string(lines, "source_commit", entry.source_commit)
    _append_optional_string(lines, "source_tree", entry.source_tree)
    _append_optional_string(lines, "source_content_hash", entry.source_content_hash)
    _append_optional_string(
        lines, "source_skill_file_hash", entry.source_skill_file_hash
    )
    _append_optional_string(
        lines, "installed_content_hash", entry.installed_content_hash
    )
    _append_optional_string(lines, "local_content_hash", entry.local_content_hash)
    if entry.orphan:
        lines.append("orphan = true")
    if entry.modified:
        lines.append("modified = true")
    if entry.update_available:
        lines.append("update_available = true")


def _append_optional_string(lines: list[str], field: str, value: str | None) -> None:
    if value is not None:
        lines.append(f'{field} = "{toml_escape(value)}"')


def _reject_symlinked_manifest_dir(path: Path) -> None:
    try:
        if path.is_symlink():
            raise SvError(f"Refusing to use symlinked sv manifest directory at {path}.")
    except OSError as exc:
        raise SvError(f"Failed to inspect sv manifest directory {path}: {exc}") from exc


def upsert_manifest_entry(project_skills_dir: Path, entry: ManifestEntry) -> None:
    entries = load_manifest(project_skills_dir)
    entries[entry.name] = entry
    save_manifest(project_skills_dir, entries)


def remove_manifest_entry(project_skills_dir: Path, skill_name: str) -> None:
    legacy_exists = (
        _should_check_legacy_manifest(project_skills_dir)
        and legacy_manifest_path(project_skills_dir).is_file()
    )
    if not manifest_path(project_skills_dir).is_file() and not legacy_exists:
        return

    entries = load_manifest(project_skills_dir)
    if skill_name not in entries:
        return

    del entries[skill_name]
    save_manifest(project_skills_dir, entries)


def _project_root_for_skills_dir(project_skills_dir: Path) -> Path:
    if project_skills_dir.name == "skills":
        if project_skills_dir.parent.name == ".pi":
            return project_skills_dir.parent.parent
        return project_skills_dir.parent
    return project_skills_dir


def _is_pi_project_skills_dir(project_skills_dir: Path) -> bool:
    return project_skills_dir.name == "skills" and project_skills_dir.parent.name == ".pi"


def _should_check_legacy_manifest(project_skills_dir: Path) -> bool:
    if _is_pi_project_skills_dir(project_skills_dir):
        return True
    if project_skills_dir.name == "skills":
        return False
    return (project_skills_dir / ".pi" / "skills" / ".sv-manifest.toml").is_file()

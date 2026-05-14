from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
import tomllib

from sv.errors import SvError


@dataclass(frozen=True)
class ManifestEntry:
    name: str
    repo_id: str
    repo_url: str
    source_path: str
    description: str


def manifest_path(project_skills_dir: Path) -> Path:
    return project_skills_dir / ".sv-manifest.toml"


def load_manifest(project_skills_dir: Path) -> dict[str, ManifestEntry]:
    path = manifest_path(project_skills_dir)
    if not path.is_file():
        return {}

    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except tomllib.TOMLDecodeError as exc:
        raise SvError(f"Failed to read sv manifest at {path}: {exc}") from exc
    except OSError as exc:
        raise SvError(f"Failed to read sv manifest at {path}: {exc}") from exc

    return _parse_manifest_entries(data.get("skills", []), path)


def _parse_manifest_entries(raw_skills: Any, path: Path) -> dict[str, ManifestEntry]:
    if not isinstance(raw_skills, list):
        raise SvError(f"Invalid sv manifest at {path}: skills must be a list.")

    entries: dict[str, ManifestEntry] = {}
    for index, item in enumerate(raw_skills, start=1):
        if not isinstance(item, dict):
            raise SvError(
                f"Invalid sv manifest at {path}: skills[{index}] must be a table."
            )
        skill_item = cast("dict[str, Any]", item)
        try:
            entry = ManifestEntry(
                name=_expect_string(skill_item["name"], "name", index, path),
                repo_id=_expect_string(skill_item["repo_id"], "repo_id", index, path),
                repo_url=_expect_string(
                    skill_item["repo_url"], "repo_url", index, path
                ),
                source_path=_expect_string(
                    skill_item["source_path"], "source_path", index, path
                ),
                description=_expect_string(
                    skill_item.get("description", ""), "description", index, path
                ),
            )
        except KeyError as exc:
            raise SvError(
                f"Invalid sv manifest at {path}: skill entry {index} is missing {exc.args[0]!r}."
            ) from exc
        entries[entry.name] = entry
    return entries


def _expect_string(value: Any, field: str, index: int, path: Path) -> str:
    if not isinstance(value, str):
        raise SvError(
            f"Invalid sv manifest at {path}: skill entry {index} field {field!r} must be a string."
        )
    return value


def save_manifest(project_skills_dir: Path, entries: dict[str, ManifestEntry]) -> None:
    path = manifest_path(project_skills_dir)
    temp_path = path.with_name(f"{path.name}.tmp")
    try:
        project_skills_dir.mkdir(parents=True, exist_ok=True)
        if not entries:
            path.unlink(missing_ok=True)
            return

        lines: list[str] = []
        for index, entry in enumerate(entries[name] for name in sorted(entries)):
            if index:
                lines.append("")
            lines.append("[[skills]]")
            lines.append(f'name = "{_toml_escape(entry.name)}"')
            lines.append(f'repo_id = "{_toml_escape(entry.repo_id)}"')
            lines.append(f'repo_url = "{_toml_escape(entry.repo_url)}"')
            lines.append(f'source_path = "{_toml_escape(entry.source_path)}"')
            lines.append(f'description = "{_toml_escape(entry.description)}"')
        temp_path.write_text("\n".join(lines) + "\n")
        temp_path.replace(path)
    except OSError as exc:
        with suppress(OSError):
            temp_path.unlink(missing_ok=True)
        raise SvError(f"Failed to write sv manifest at {path}: {exc}") from exc


def upsert_manifest_entry(project_skills_dir: Path, entry: ManifestEntry) -> None:
    entries = load_manifest(project_skills_dir)
    entries[entry.name] = entry
    save_manifest(project_skills_dir, entries)


def remove_manifest_entry(project_skills_dir: Path, skill_name: str) -> None:
    if not manifest_path(project_skills_dir).is_file():
        return

    entries = load_manifest(project_skills_dir)
    if skill_name not in entries:
        return

    del entries[skill_name]
    save_manifest(project_skills_dir, entries)


def _toml_escape(value: str) -> str:
    escaped: list[str] = []
    replacements = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
    for char in value:
        replacement = replacements.get(char)
        if replacement is not None:
            escaped.append(replacement)
        elif ord(char) < 0x20:
            escaped.append(f"\\u{ord(char):04x}")
        else:
            escaped.append(char)
    return "".join(escaped)

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


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

    with path.open("rb") as file:
        data = tomllib.load(file)

    entries: dict[str, ManifestEntry] = {}
    for item in data.get("skills", []):
        entry = ManifestEntry(
            name=str(item["name"]),
            repo_id=str(item["repo_id"]),
            repo_url=str(item["repo_url"]),
            source_path=str(item["source_path"]),
            description=str(item.get("description", "")),
        )
        entries[entry.name] = entry
    return entries


def save_manifest(project_skills_dir: Path, entries: dict[str, ManifestEntry]) -> None:
    project_skills_dir.mkdir(parents=True, exist_ok=True)
    path = manifest_path(project_skills_dir)
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
    path.write_text("\n".join(lines) + "\n")


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
    return value.replace("\\", "\\\\").replace('"', '\\"')

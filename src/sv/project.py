from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Protocol

from sv.errors import SvError
from sv.manifest import ManifestEntry, load_manifest, upsert_manifest_entry


class ProjectSourceSkill(Protocol):
    name: str
    description: str
    repo_id: str
    repo_url: str
    source_path: Path

    @property
    def source_relative_path(self) -> str: ...


@dataclass(frozen=True)
class AddSkillResult:
    """Outcome of adding a source skill to a Pi project."""

    skill: str
    target: Path
    status: str
    repo_id: str | None = None


@dataclass(frozen=True)
class AddAllSkillsResult:
    """Summary of adding every source skill to a Pi project."""

    results: list[AddSkillResult]


@dataclass(frozen=True)
class RemoveSkillResult:
    """Outcome of removing a Pi skill from a project."""

    skill: str
    target: Path


@dataclass(frozen=True)
class SyncSkip:
    skill: str
    reason: str
    repo_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SyncResult:
    """Summary of local project skills considered during sync."""

    updated: list[str]
    skipped: list[SyncSkip]
    backfilled: list[str]
    no_skills_dir: bool = False


def normalize_skill_name(skill: str) -> str:
    """Return a safe single-folder skill name for source and project paths."""
    name = skill.strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise SvError(
            f"Invalid skill name {skill!r}. Use a single source skill folder name."
        )
    return name


def add_project_skill(
    entry: ProjectSourceSkill, project_skills_dir: Path
) -> AddSkillResult:
    skill_name = normalize_skill_name(entry.name)
    if not entry.source_path.is_dir():
        raise SvError(f"Skill '{skill_name}' was not found in source skills directory.")

    project_skills_dir.mkdir(parents=True, exist_ok=True)
    target = project_skills_dir / skill_name
    if target.exists():
        return AddSkillResult(
            skill=skill_name,
            target=target,
            status="exists",
            repo_id=entry.repo_id,
        )

    shutil.copytree(entry.source_path, target)
    upsert_manifest_entry(project_skills_dir, _manifest_entry_for(entry))
    return AddSkillResult(
        skill=skill_name,
        target=target,
        status="added",
        repo_id=entry.repo_id,
    )


def add_all_project_skills(
    catalog: Sequence[ProjectSourceSkill], project_skills_dir: Path
) -> AddAllSkillsResult:
    return AddAllSkillsResult(
        results=[add_project_skill(entry, project_skills_dir) for entry in catalog]
    )


def list_project_skills(project_skills_dir: Path) -> list[str]:
    """Return project-local Pi skill directory names in display order."""
    if not project_skills_dir.is_dir():
        return []
    return sorted(path.name for path in project_skills_dir.iterdir() if path.is_dir())


def remove_project_skill(skill: str, project_skills_dir: Path) -> RemoveSkillResult:
    skill_name = normalize_skill_name(skill)
    target = project_skills_dir / skill_name
    if not target.is_dir():
        raise SvError(f"Pi skill '{skill_name}' was not found in this project.")

    try:
        shutil.rmtree(target)
    except OSError as exc:
        raise SvError(f"Failed to remove Pi skill '{skill_name}': {exc}") from exc

    return RemoveSkillResult(skill=skill_name, target=target)


def sync_project_skills(
    catalog: Sequence[ProjectSourceSkill], project_skills_dir: Path
) -> SyncResult:
    if not project_skills_dir.is_dir():
        return SyncResult(updated=[], skipped=[], backfilled=[], no_skills_dir=True)

    by_key = {(entry.name, entry.repo_id): entry for entry in catalog}
    by_name: dict[str, list[ProjectSourceSkill]] = {}
    for entry in catalog:
        by_name.setdefault(entry.name, []).append(entry)

    manifest = load_manifest(project_skills_dir)
    updated: list[str] = []
    backfilled: list[str] = []
    skipped: list[SyncSkip] = []

    for local_skill in sorted(project_skills_dir.iterdir(), key=lambda path: path.name):
        if not local_skill.is_dir() or local_skill.name.startswith("."):
            continue

        manifest_entry = manifest.get(local_skill.name)
        if manifest_entry is not None:
            entry = by_key.get((manifest_entry.name, manifest_entry.repo_id))
            if entry is None:
                skipped.append(
                    SyncSkip(
                        skill=local_skill.name,
                        reason="source-missing",
                        repo_ids=(manifest_entry.repo_id,),
                    )
                )
                continue
            _replace_tree(entry.source_path, local_skill)
            upsert_manifest_entry(project_skills_dir, _manifest_entry_for(entry))
            updated.append(local_skill.name)
            continue

        matches = by_name.get(local_skill.name, [])
        if len(matches) == 1:
            entry = matches[0]
            _replace_tree(entry.source_path, local_skill)
            upsert_manifest_entry(project_skills_dir, _manifest_entry_for(entry))
            updated.append(local_skill.name)
            backfilled.append(local_skill.name)
        elif len(matches) > 1:
            skipped.append(
                SyncSkip(
                    skill=local_skill.name,
                    reason="ambiguous",
                    repo_ids=tuple(entry.repo_id for entry in matches),
                )
            )
        else:
            skipped.append(SyncSkip(skill=local_skill.name, reason="local-only"))

    return SyncResult(
        updated=updated,
        skipped=skipped,
        backfilled=backfilled,
        no_skills_dir=False,
    )


def _replace_tree(source: Path, target: Path) -> None:
    """Replace target with source while preserving target if the copy fails."""
    temp_target = target.with_name(f".{target.name}.sv-sync-tmp")
    if temp_target.exists():
        shutil.rmtree(temp_target)

    try:
        # Copy into a sibling first so a failed copy does not delete the local skill.
        shutil.copytree(source, temp_target)
        shutil.rmtree(target)
        temp_target.rename(target)
    except OSError as exc:
        if temp_target.exists():
            shutil.rmtree(temp_target, ignore_errors=True)
        raise SvError(f"Failed to sync skill '{target.name}': {exc}") from exc


def _manifest_entry_for(entry: ProjectSourceSkill) -> ManifestEntry:
    return ManifestEntry(
        name=entry.name,
        repo_id=entry.repo_id,
        repo_url=entry.repo_url,
        source_path=entry.source_relative_path,
        description=entry.description,
    )

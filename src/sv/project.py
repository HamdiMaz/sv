from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Protocol

from sv.config import repo_source_key
from sv.errors import SvError
from sv.manifest import (
    ManifestEntry,
    load_manifest,
    remove_manifest_entry,
    save_manifest,
    upsert_manifest_entry,
)


class ProjectSourceSkill(Protocol):
    name: str
    description: str
    repo_id: str
    repo_url: str
    source_path: Path
    repo_aliases: tuple[str, ...]

    @property
    def source_relative_path(self) -> str: ...


@dataclass(frozen=True)
class AddSkillResult:
    """Outcome of adding a source skill to a Pi project."""

    skill: str
    target: Path
    status: str
    repo_id: str | None = None
    existing_repo_id: str | None = None


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
    if (
        not name
        or name.startswith(".")
        or any(char in name for char in ("/", "\\", ":"))
        or _contains_control_character(name)
    ):
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
    _ensure_safe_source_skill_tree(entry.source_path, skill_name)
    _ensure_safe_project_skills_dir(project_skills_dir)

    try:
        project_skills_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SvError(
            f"Failed to prepare Pi skills directory {project_skills_dir}: {exc}"
        ) from exc

    manifest = load_manifest(project_skills_dir)
    target = project_skills_dir / skill_name
    if target.exists() or target.is_symlink():
        _reject_symlinked_project_skill(target)
        if not target.is_dir():
            raise SvError(
                f"Cannot add Pi skill '{skill_name}': non-directory path already exists at {target}."
            )
        existing_entry = manifest.get(skill_name)
        return AddSkillResult(
            skill=skill_name,
            target=target,
            status="exists",
            repo_id=entry.repo_id,
            existing_repo_id=(
                existing_entry.repo_id if existing_entry is not None else None
            ),
        )

    _copy_tree_for_add(entry.source_path, target)
    try:
        upsert_manifest_entry(project_skills_dir, _manifest_entry_for(entry))
    except SvError as exc:
        try:
            if target.exists():
                shutil.rmtree(target)
        except OSError as rollback_exc:
            raise SvError(
                f"Failed to add Pi skill '{skill_name}': manifest update failed "
                f"({exc}) and rollback failed: {rollback_exc}"
            ) from rollback_exc
        raise
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
    _ensure_safe_project_skills_dir(project_skills_dir)
    if not project_skills_dir.is_dir():
        return []
    try:
        skill_names: list[str] = []
        for path in project_skills_dir.iterdir():
            if path.name.startswith("."):
                continue
            _reject_symlinked_project_skill(path)
            _validate_project_skill_dir_name(path)
            if path.is_dir():
                skill_names.append(path.name)
        return sorted(skill_names)
    except OSError as exc:
        raise SvError(
            f"Failed to list Pi skills in {project_skills_dir}: {exc}"
        ) from exc


def remove_project_skill(skill: str, project_skills_dir: Path) -> RemoveSkillResult:
    skill_name = normalize_skill_name(skill)
    _ensure_safe_project_skills_dir(project_skills_dir)
    target = project_skills_dir / skill_name
    _reject_symlinked_project_skill(target)
    if not target.is_dir():
        raise SvError(f"Pi skill '{skill_name}' was not found in this project.")

    original_manifest = load_manifest(project_skills_dir)
    backup_target = target.with_name(f".{target.name}.sv-remove-backup")
    if backup_target.exists():
        try:
            shutil.rmtree(backup_target)
        except OSError as exc:
            raise SvError(
                f"Failed to remove stale sv backup for Pi skill '{skill_name}': {exc}"
            ) from exc

    try:
        target.rename(backup_target)
    except OSError as exc:
        raise SvError(f"Failed to remove Pi skill '{skill_name}': {exc}") from exc

    try:
        remove_manifest_entry(project_skills_dir, skill_name)
    except SvError as exc:
        try:
            backup_target.rename(target)
        except OSError as rollback_exc:
            raise SvError(
                f"Failed to remove Pi skill '{skill_name}': manifest update failed "
                f"({exc}) and rollback failed: {rollback_exc}"
            ) from rollback_exc
        raise

    try:
        shutil.rmtree(backup_target)
    except OSError as exc:
        try:
            if not target.exists():
                backup_target.rename(target)
            save_manifest(project_skills_dir, original_manifest)
        except (OSError, SvError) as rollback_exc:
            raise SvError(
                f"Failed to remove Pi skill '{skill_name}': cleanup failed "
                f"({exc}) and rollback failed: {rollback_exc}"
            ) from rollback_exc
        raise SvError(f"Failed to remove Pi skill '{skill_name}': {exc}") from exc

    return RemoveSkillResult(skill=skill_name, target=target)


def sync_project_skills(
    catalog: Sequence[ProjectSourceSkill], project_skills_dir: Path
) -> SyncResult:
    _ensure_safe_project_skills_dir(project_skills_dir)
    if not project_skills_dir.is_dir():
        return SyncResult(updated=[], skipped=[], backfilled=[], no_skills_dir=True)

    by_key: dict[tuple[str, str], ProjectSourceSkill] = {}
    by_source_key: dict[tuple[str, str], ProjectSourceSkill] = {}
    by_name: dict[str, list[ProjectSourceSkill]] = {}
    for entry in catalog:
        for repo_id in (entry.repo_id, *entry.repo_aliases):
            by_key[(entry.name, repo_id)] = entry
        by_source_key[(entry.name, repo_source_key(entry.repo_url))] = entry
        by_name.setdefault(entry.name, []).append(entry)

    manifest = load_manifest(project_skills_dir)
    updated: list[str] = []
    backfilled: list[str] = []
    skipped: list[SyncSkip] = []

    try:
        local_skills = sorted(project_skills_dir.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise SvError(
            f"Failed to list Pi skills in {project_skills_dir}: {exc}"
        ) from exc

    for local_skill in local_skills:
        if local_skill.name.startswith("."):
            continue
        _reject_symlinked_project_skill(local_skill)
        _validate_project_skill_dir_name(local_skill)
        if not local_skill.is_dir():
            continue

        manifest_entry = manifest.get(local_skill.name)
        if manifest_entry is not None:
            manifest_source_key = _recorded_source_key(manifest_entry)
            entry = by_key.get((manifest_entry.name, manifest_entry.repo_id))
            if entry is not None and not _source_matches_recorded_key(
                entry, manifest_source_key
            ):
                entry = None
            if entry is None and manifest_source_key is not None:
                entry = by_source_key.get((manifest_entry.name, manifest_source_key))
            if entry is None:
                skipped.append(
                    SyncSkip(
                        skill=local_skill.name,
                        reason="source-missing",
                        repo_ids=(manifest_entry.repo_id,),
                    )
                )
                continue
            _replace_tree_and_update_manifest(entry, local_skill, project_skills_dir)
            updated.append(local_skill.name)
            continue

        matches = by_name.get(local_skill.name, [])
        if len(matches) == 1:
            entry = matches[0]
            _replace_tree_and_update_manifest(entry, local_skill, project_skills_dir)
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


def _recorded_source_key(manifest_entry: ManifestEntry) -> str | None:
    try:
        return repo_source_key(manifest_entry.repo_url)
    except ValueError:
        return None


def _source_matches_recorded_key(
    entry: ProjectSourceSkill, recorded_source_key: str | None
) -> bool:
    if recorded_source_key is None:
        return False
    return repo_source_key(entry.repo_url) == recorded_source_key


def _contains_control_character(value: str) -> bool:
    return any(ord(char) < 0x20 or 0x7F <= ord(char) < 0xA0 for char in value)


def _ensure_safe_project_skills_dir(project_skills_dir: Path) -> None:
    pi_dir = project_skills_dir.parent
    for path in (pi_dir, project_skills_dir):
        try:
            if path.is_symlink():
                raise SvError(f"Refusing to use symlinked Pi skills path at {path}.")
        except OSError as exc:
            raise SvError(f"Failed to inspect Pi skills path {path}: {exc}") from exc


def _reject_symlinked_project_skill(path: Path) -> None:
    try:
        is_symlink = path.is_symlink()
    except OSError as exc:
        raise SvError(f"Failed to inspect Pi skill path {path}: {exc}") from exc
    if is_symlink:
        raise SvError(f"Refusing to manage symlinked Pi skill '{path.name}' at {path}.")


def _validate_project_skill_dir_name(path: Path) -> None:
    try:
        normalize_skill_name(path.name)
    except SvError as exc:
        raise SvError(f"Invalid Pi skill directory at {path}: {exc}") from exc


def _ensure_safe_source_skill_tree(source: Path, skill_name: str) -> None:
    if source.is_symlink():
        raise SvError(f"Source skill '{skill_name}' contains a symlink at {source}.")
    try:
        for path in source.rglob("*"):
            if path.is_symlink():
                raise SvError(
                    f"Source skill '{skill_name}' contains a symlink at {path}."
                )
    except OSError as exc:
        raise SvError(f"Failed to inspect source skill '{skill_name}': {exc}") from exc


def _copy_tree_for_add(source: Path, target: Path) -> None:
    """Copy source into target without leaving partial target directories behind."""
    temp_target = target.with_name(f".{target.name}.sv-add-tmp")
    if temp_target.exists():
        shutil.rmtree(temp_target, ignore_errors=True)

    try:
        shutil.copytree(source, temp_target)
        temp_target.rename(target)
    except OSError as exc:
        if temp_target.exists():
            shutil.rmtree(temp_target, ignore_errors=True)
        raise SvError(f"Failed to add Pi skill '{target.name}': {exc}") from exc


def _replace_tree_and_update_manifest(
    entry: ProjectSourceSkill, target: Path, project_skills_dir: Path
) -> None:
    _ensure_safe_source_skill_tree(entry.source_path, entry.name)
    manifest_entry = _manifest_entry_for(entry)

    def update_manifest() -> None:
        upsert_manifest_entry(project_skills_dir, manifest_entry)

    _replace_tree(entry.source_path, target, after_replace=update_manifest)


def _replace_tree(
    source: Path, target: Path, after_replace: Callable[[], None] | None = None
) -> None:
    """Replace target with source while preserving target if the copy fails."""
    temp_target = target.with_name(f".{target.name}.sv-sync-tmp")
    backup_target = target.with_name(f".{target.name}.sv-sync-backup")
    if temp_target.exists():
        shutil.rmtree(temp_target, ignore_errors=True)
    if backup_target.exists():
        shutil.rmtree(backup_target, ignore_errors=True)

    try:
        # Copy into a sibling first so a failed copy does not delete the local skill.
        shutil.copytree(source, temp_target)
        target.rename(backup_target)
        try:
            temp_target.rename(target)
        except OSError:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            if backup_target.exists():
                backup_target.rename(target)
            raise
        try:
            if after_replace is not None:
                after_replace()
        except SvError as exc:
            try:
                _restore_backup(target, backup_target)
            except OSError as rollback_exc:
                raise SvError(
                    f"Failed to sync skill '{target.name}': manifest update failed "
                    f"({exc}) and rollback failed: {rollback_exc}"
                ) from rollback_exc
            raise
        shutil.rmtree(backup_target, ignore_errors=True)
    except OSError as exc:
        if temp_target.exists():
            shutil.rmtree(temp_target, ignore_errors=True)
        if backup_target.exists() and not target.exists():
            try:
                backup_target.rename(target)
            except OSError:
                pass
        raise SvError(f"Failed to sync skill '{target.name}': {exc}") from exc


def _restore_backup(target: Path, backup_target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    backup_target.rename(target)


def _manifest_entry_for(entry: ProjectSourceSkill) -> ManifestEntry:
    return ManifestEntry(
        name=entry.name,
        repo_id=entry.repo_id,
        repo_url=entry.repo_url,
        source_path=entry.source_relative_path,
        description=entry.description,
    )

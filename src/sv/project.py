from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from sv.errors import SvError


@dataclass(frozen=True)
class AddSkillResult:
    """Outcome of adding a source skill to a Pi project."""

    skill: str
    target: Path
    status: str


@dataclass(frozen=True)
class AddAllSkillsResult:
    """Summary of adding every source skill to a Pi project."""

    results: list[AddSkillResult]


@dataclass(frozen=True)
class SyncResult:
    """Summary of local project skills considered during sync."""

    updated: list[str]
    skipped: list[str]
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
    skill: str, source_repo: Path, project_skills_dir: Path
) -> AddSkillResult:
    skill_name = normalize_skill_name(skill)
    source_skill = source_repo / "skills" / skill_name
    if not source_skill.is_dir():
        raise SvError(f"Skill '{skill_name}' was not found in source skills directory.")

    project_skills_dir.mkdir(parents=True, exist_ok=True)
    target = project_skills_dir / skill_name
    if target.exists():
        return AddSkillResult(skill=skill_name, target=target, status="exists")

    shutil.copytree(source_skill, target)
    return AddSkillResult(skill=skill_name, target=target, status="added")


def add_all_project_skills(
    source_repo: Path, project_skills_dir: Path
) -> AddAllSkillsResult:
    source_root = source_repo / "skills"
    results = [
        add_project_skill(skill_name, source_repo, project_skills_dir)
        for skill_name in sorted(_source_skill_names(source_root))
    ]
    return AddAllSkillsResult(results=results)


def sync_project_skills(source_repo: Path, project_skills_dir: Path) -> SyncResult:
    if not project_skills_dir.is_dir():
        return SyncResult(updated=[], skipped=[], no_skills_dir=True)

    source_root = source_repo / "skills"
    source_names = _source_skill_names(source_root)
    updated: list[str] = []
    skipped: list[str] = []

    for local_skill in sorted(project_skills_dir.iterdir(), key=lambda path: path.name):
        if not local_skill.is_dir():
            continue

        if local_skill.name not in source_names:
            skipped.append(local_skill.name)
            continue

        _replace_tree(source_root / local_skill.name, local_skill)
        updated.append(local_skill.name)

    return SyncResult(updated=updated, skipped=skipped, no_skills_dir=False)


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


def _source_skill_names(source_root: Path) -> set[str]:
    if not source_root.is_dir():
        return set()
    return {path.name for path in source_root.iterdir() if path.is_dir()}

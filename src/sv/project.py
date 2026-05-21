from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
import shutil
import unicodedata
from typing import Protocol

from sv.agents import project_agent_for
from sv.config import repo_source_key
from sv.errors import SvError
from sv.manifest import ManifestEntry
from sv.materialization import DEFAULT_MATERIALIZATION_ADAPTER
from sv.parallel import map_ordered
from sv.stores import ProjectManifestStore


class ProjectSourceSkill(Protocol):
    name: str
    description: str
    repo_id: str
    repo_url: str
    source_path: Path
    source_relative_path: str
    repo_aliases: tuple[str, ...]
    source_backend: str

    def materialize_to(self, destination: Path) -> None:
        """Materialize this selected source skill into destination."""
        ...


@dataclass(frozen=True)
class _TargetStyle:
    target_kind: str
    target_agent: str | None
    target_path_prefix: str
    skill_label: str
    skills_dir_label: str
    path_label: str


_PI_TARGET = _TargetStyle(
    target_kind="project-agent",
    target_agent="pi",
    target_path_prefix=".pi/skills",
    skill_label="Pi skill",
    skills_dir_label="Pi skills directory",
    path_label="Pi skills path",
)
_VAULT_TARGET = _TargetStyle(
    target_kind="skill-vault",
    target_agent=None,
    target_path_prefix="skills",
    skill_label="vault skill",
    skills_dir_label="vault skills directory",
    path_label="vault skills path",
)


def _project_agent_target(agent_name: str) -> _TargetStyle:
    agent = project_agent_for(agent_name)
    return _TargetStyle(
        target_kind="project-agent",
        target_agent=agent.name,
        target_path_prefix=agent.target_path_prefix,
        skill_label=agent.skill_label,
        skills_dir_label=agent.skills_dir_label,
        path_label=agent.path_label,
    )


_MATERIALIZATION = DEFAULT_MATERIALIZATION_ADAPTER


@dataclass(frozen=True)
class AddSkillResult:
    """Outcome of adding a source skill to a Pi project."""

    skill: str
    target: Path
    status: str
    repo_id: str | None = None
    existing_repo_id: str | None = None
    source_reference: str | None = None
    existing_source_reference: str | None = None
    target_kind: str = _PI_TARGET.target_kind
    target_agent: str | None = _PI_TARGET.target_agent


@dataclass(frozen=True)
class AddAllSkillsResult:
    """Summary of adding every source skill to a Pi project."""

    results: list[AddSkillResult]
    target_kind: str = _PI_TARGET.target_kind
    target_agent: str | None = _PI_TARGET.target_agent


@dataclass(frozen=True)
class RemoveSkillResult:
    """Outcome of removing a local skill from a project or skill-vault."""

    skill: str
    target: Path
    target_kind: str = _PI_TARGET.target_kind
    target_agent: str | None = _PI_TARGET.target_agent


@dataclass(frozen=True)
class SyncSkip:
    skill: str
    reason: str
    repo_ids: tuple[str, ...] = ()
    source_references: tuple[str, ...] = ()


@dataclass(frozen=True)
class SyncResult:
    """Summary of local skills considered during sync."""

    updated: list[str]
    skipped: list[SyncSkip]
    backfilled: list[str]
    no_skills_dir: bool = False
    target_kind: str = _PI_TARGET.target_kind
    target_agent: str | None = _PI_TARGET.target_agent


@dataclass(frozen=True)
class _MaterializedSkillMetadata:
    content_hash: str
    skill_file_hash: str


@dataclass(frozen=True)
class _AddPlan:
    index: int
    entry: ProjectSourceSkill
    skill_name: str
    target: Path
    replace_existing_target: bool = False
    result: AddSkillResult | None = None


@dataclass(frozen=True)
class _PreparedAdd:
    plan: _AddPlan
    metadata: _MaterializedSkillMetadata


@dataclass(frozen=True)
class _ReplacementPlan:
    entry: ProjectSourceSkill
    target: Path
    project_skills_dir: Path
    target_style: _TargetStyle


@dataclass(frozen=True)
class _PreparedReplacement:
    plan: _ReplacementPlan
    metadata: _MaterializedSkillMetadata


@dataclass(frozen=True)
class _ReplacementWork:
    plan: _ReplacementPlan
    prepared: _PreparedReplacement | None = None


@dataclass(frozen=True)
class _ManifestUpdatePlan:
    project_skills_dir: Path
    entry: ManifestEntry
    target_style: _TargetStyle


@dataclass(frozen=True)
class _LocalStateRefresh:
    key: str
    entry: ManifestEntry
    refreshed: ManifestEntry


def normalize_skill_name(skill: str) -> str:
    """Return a safe single-folder skill name for source and project paths."""
    name = skill.strip()
    if (
        not name
        or name.startswith(".")
        or name.startswith("-")
        or any(char in name for char in ("/", "\\", ":"))
        or _contains_control_character(name)
        or _contains_unicode_format_character(name)
    ):
        raise SvError(
            f"Invalid skill name {skill!r}. Use a single source skill folder name."
        )
    return name


def add_project_agent_skill(
    entry: ProjectSourceSkill, project_skills_dir: Path, agent: str
) -> AddSkillResult:
    return _add_skill(entry, project_skills_dir, _project_agent_target(agent))


def add_project_skill(
    entry: ProjectSourceSkill, project_skills_dir: Path
) -> AddSkillResult:
    return add_project_agent_skill(entry, project_skills_dir, "pi")


def add_vault_skill(
    entry: ProjectSourceSkill,
    vault_skills_dir: Path,
    *,
    replace_existing: bool = False,
) -> AddSkillResult:
    return _add_skill(
        entry, vault_skills_dir, _VAULT_TARGET, replace_existing=replace_existing
    )


def _add_skill(
    entry: ProjectSourceSkill,
    project_skills_dir: Path,
    target_style: _TargetStyle,
    *,
    replace_existing: bool = False,
) -> AddSkillResult:
    skill_name = normalize_skill_name(entry.name)
    if entry.source_backend == "local-cache" and not entry.source_path.is_dir():
        raise SvError(f"Skill '{skill_name}' was not found in source skills directory.")
    _ensure_safe_project_skills_dir(project_skills_dir, target_style)

    try:
        project_skills_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SvError(
            f"Failed to prepare {target_style.skills_dir_label} {project_skills_dir}: {exc}"
        ) from exc

    manifest = _load_manifest_for_target(project_skills_dir, target_style)
    target = project_skills_dir / skill_name
    if target.exists() or target.is_symlink():
        _reject_symlinked_project_skill(target, target_style)
        if not target.is_dir():
            raise SvError(
                f"Cannot add {target_style.skill_label} '{skill_name}': non-directory path already exists at {target}."
            )
        existing_entry = manifest.get(skill_name)
        if not replace_existing:
            return AddSkillResult(
                skill=skill_name,
                target=target,
                status="exists",
                repo_id=entry.repo_id,
                existing_repo_id=(
                    existing_entry.repo_id if existing_entry is not None else None
                ),
                source_reference=_source_reference_for(entry),
                existing_source_reference=(
                    _source_reference_for_manifest(existing_entry)
                    if existing_entry is not None
                    else None
                ),
                target_kind=target_style.target_kind,
                target_agent=target_style.target_agent,
            )

        metadata = _materialize_entry_for_replace(entry, target, target_style)
        manifest_replacement = _manifest_entry_for(
            entry, target_style, metadata=metadata
        )

        def update_manifest() -> None:
            _upsert_manifest_entry_for_target(
                project_skills_dir, manifest_replacement, target_style
            )

        _replace_with_materialized_entry(
            target, after_replace=update_manifest, target_style=target_style
        )
        return AddSkillResult(
            skill=skill_name,
            target=target,
            status="replaced",
            repo_id=entry.repo_id,
            source_reference=_source_reference_for(entry),
            target_kind=target_style.target_kind,
            target_agent=target_style.target_agent,
        )

    metadata = _materialize_entry_for_add(entry, target, target_style)

    def update_manifest() -> None:
        _upsert_manifest_entry_for_target(
            project_skills_dir,
            _manifest_entry_for(entry, target_style, metadata=metadata),
            target_style,
        )

    _MATERIALIZATION.install_materialized_skill_folder(
        _add_temp_target(target),
        target,
        error_message=f"Failed to add {target_style.skill_label} '{target.name}'",
        after_install=update_manifest,
    )
    return AddSkillResult(
        skill=skill_name,
        target=target,
        status="added",
        repo_id=entry.repo_id,
        source_reference=_source_reference_for(entry),
        target_kind=target_style.target_kind,
        target_agent=target_style.target_agent,
    )


def add_all_project_agent_skills(
    catalog: Sequence[ProjectSourceSkill], project_skills_dir: Path, agent: str
) -> AddAllSkillsResult:
    return _add_all_skills_parallel(
        catalog,
        project_skills_dir,
        _project_agent_target(agent),
        replace_existing=False,
    )


def add_all_project_skills(
    catalog: Sequence[ProjectSourceSkill], project_skills_dir: Path
) -> AddAllSkillsResult:
    return add_all_project_agent_skills(catalog, project_skills_dir, "pi")


def add_all_vault_skills(
    catalog: Sequence[ProjectSourceSkill],
    vault_skills_dir: Path,
    *,
    replace_existing: bool = False,
    replace_existing_indexes: frozenset[int] = frozenset(),
) -> AddAllSkillsResult:
    return _add_all_skills_parallel(
        catalog,
        vault_skills_dir,
        _VAULT_TARGET,
        replace_existing=replace_existing,
        replace_existing_indexes=replace_existing_indexes,
    )


def _plan_add_all_skills(
    catalog: Sequence[ProjectSourceSkill],
    project_skills_dir: Path,
    target_style: _TargetStyle,
    *,
    replace_existing: bool,
    replace_existing_indexes: frozenset[int] = frozenset(),
) -> tuple[dict[int, AddSkillResult], list[_AddPlan]]:
    manifest = _load_manifest_for_target(project_skills_dir, target_style)
    existing_or_skipped: dict[int, AddSkillResult] = {}
    to_prepare: list[_AddPlan] = []
    planned_targets: set[Path] = set()
    planned_manifest_entries: dict[str, ManifestEntry] = {}

    for index, entry in enumerate(catalog):
        skill_name = normalize_skill_name(entry.name)
        if entry.source_backend == "local-cache" and not entry.source_path.is_dir():
            raise SvError(
                f"Skill '{skill_name}' was not found in source skills directory."
            )
        target = project_skills_dir / skill_name
        existing_entry = planned_manifest_entries.get(skill_name) or manifest.get(
            skill_name
        )
        target_is_planned = target in planned_targets
        if target_is_planned:
            existing_or_skipped[index] = AddSkillResult(
                skill=skill_name,
                target=target,
                status="exists",
                repo_id=entry.repo_id,
                existing_repo_id=(
                    existing_entry.repo_id if existing_entry is not None else None
                ),
                source_reference=_source_reference_for(entry),
                existing_source_reference=(
                    _source_reference_for_manifest(existing_entry)
                    if existing_entry is not None
                    else None
                ),
                target_kind=target_style.target_kind,
                target_agent=target_style.target_agent,
            )
            continue
        target_exists = target.exists() or target.is_symlink()
        if target_exists:
            _reject_symlinked_project_skill(target, target_style)
            if not target.is_dir():
                raise SvError(
                    f"Cannot add {target_style.skill_label} '{skill_name}': "
                    f"non-directory path already exists at {target}."
                )
        should_replace = replace_existing or index in replace_existing_indexes
        if target_exists and should_replace:
            to_prepare.append(
                _AddPlan(
                    index=index,
                    entry=entry,
                    skill_name=skill_name,
                    target=target,
                    replace_existing_target=True,
                )
            )
            planned_targets.add(target)
            planned_manifest_entries[skill_name] = _manifest_entry_for(
                entry, target_style
            )
            continue
        if target_exists:
            existing_or_skipped[index] = AddSkillResult(
                skill=skill_name,
                target=target,
                status="exists",
                repo_id=entry.repo_id,
                existing_repo_id=(
                    existing_entry.repo_id if existing_entry is not None else None
                ),
                source_reference=_source_reference_for(entry),
                existing_source_reference=(
                    _source_reference_for_manifest(existing_entry)
                    if existing_entry is not None
                    else None
                ),
                target_kind=target_style.target_kind,
                target_agent=target_style.target_agent,
            )
            continue
        to_prepare.append(
            _AddPlan(index=index, entry=entry, skill_name=skill_name, target=target)
        )
        planned_targets.add(target)
        planned_manifest_entries[skill_name] = _manifest_entry_for(entry, target_style)

    return existing_or_skipped, to_prepare


def _add_plan_temp_target(plan: _AddPlan) -> Path:
    if plan.replace_existing_target:
        return _sync_temp_target(plan.target)
    return _add_temp_target(plan.target)


def _prepare_add_plan(plan: _AddPlan, target_style: _TargetStyle) -> _PreparedAdd:
    if plan.replace_existing_target:
        metadata = _materialize_entry_for_replace(plan.entry, plan.target, target_style)
    else:
        metadata = _materialize_entry_for_add(plan.entry, plan.target, target_style)
    return _PreparedAdd(plan=plan, metadata=metadata)


def _cleanup_prepared_adds(prepared: Sequence[_PreparedAdd]) -> None:
    for item in prepared:
        _MATERIALIZATION.remove_materialization_path(
            _add_plan_temp_target(item.plan), ignore_errors=True
        )


def _add_all_skills_parallel(
    catalog: Sequence[ProjectSourceSkill],
    project_skills_dir: Path,
    target_style: _TargetStyle,
    *,
    replace_existing: bool,
    replace_existing_indexes: frozenset[int] = frozenset(),
) -> AddAllSkillsResult:
    _ensure_safe_project_skills_dir(project_skills_dir, target_style)
    try:
        project_skills_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SvError(
            f"Failed to prepare {target_style.skills_dir_label} "
            f"{project_skills_dir}: {exc}"
        ) from exc

    existing_or_skipped, to_prepare = _plan_add_all_skills(
        catalog,
        project_skills_dir,
        target_style,
        replace_existing=replace_existing,
        replace_existing_indexes=replace_existing_indexes,
    )

    prepared: list[_PreparedAdd] = []
    try:
        prepared = map_ordered(
            to_prepare, lambda plan: _prepare_add_plan(plan, target_style)
        )
    except Exception:
        _cleanup_prepared_adds(prepared)
        for plan in to_prepare:
            _MATERIALIZATION.remove_materialization_path(
                _add_plan_temp_target(plan), ignore_errors=True
            )
        raise

    ordered_results: list[AddSkillResult | None] = [None] * len(catalog)
    for index, result in existing_or_skipped.items():
        ordered_results[index] = result

    try:
        for item in prepared:
            plan = item.plan
            manifest_entry = _manifest_entry_for(
                plan.entry, target_style, metadata=item.metadata
            )

            def update_manifest(entry: ManifestEntry = manifest_entry) -> None:
                _upsert_manifest_entry_for_target(
                    project_skills_dir, entry, target_style
                )

            if plan.replace_existing_target:
                _replace_with_materialized_entry(
                    plan.target,
                    after_replace=update_manifest,
                    target_style=target_style,
                )
                status = "replaced"
            else:
                _MATERIALIZATION.install_materialized_skill_folder(
                    _add_temp_target(plan.target),
                    plan.target,
                    error_message=(
                        f"Failed to add {target_style.skill_label} '{plan.target.name}'"
                    ),
                    after_install=update_manifest,
                )
                status = "added"
            ordered_results[plan.index] = AddSkillResult(
                skill=plan.skill_name,
                target=plan.target,
                status=status,
                repo_id=plan.entry.repo_id,
                source_reference=_source_reference_for(plan.entry),
                target_kind=target_style.target_kind,
                target_agent=target_style.target_agent,
            )
    except Exception:
        _cleanup_prepared_adds(prepared)
        raise

    return AddAllSkillsResult(
        results=[result for result in ordered_results if result is not None],
        target_kind=target_style.target_kind,
        target_agent=target_style.target_agent,
    )


def list_project_skills(project_skills_dir: Path) -> list[str]:
    """Return project-local Pi skill directory names in display order."""
    return _list_skills(project_skills_dir, _PI_TARGET)


def validate_project_skills_for_run(project_skills_dir: Path) -> list[str]:
    """Validate project skill trees before exposing them to Pi."""
    skill_names = list_project_skills(project_skills_dir)
    for skill_name in skill_names:
        target = project_skills_dir / skill_name
        try:
            _MATERIALIZATION.validate_materialization_source_tree(target)
        except SvError as exc:
            raise SvError(
                f"Refusing to run Pi with unsafe project skill '{skill_name}': {exc}"
            ) from exc
    return skill_names


def list_vault_skills(vault_skills_dir: Path) -> list[str]:
    """Return root vault skill directory names in display order."""
    return _list_skills(vault_skills_dir, _VAULT_TARGET)


def _list_skills(project_skills_dir: Path, target_style: _TargetStyle) -> list[str]:
    _ensure_safe_project_skills_dir(project_skills_dir, target_style)
    if not project_skills_dir.is_dir():
        return []
    try:
        skill_names: list[str] = []
        for path in project_skills_dir.iterdir():
            if path.name.startswith("."):
                continue
            _reject_symlinked_project_skill(path, target_style)
            _validate_project_skill_dir_name(path, target_style)
            if path.is_dir():
                skill_names.append(path.name)
        return sorted(skill_names)
    except OSError as exc:
        raise SvError(
            f"Failed to list {target_style.skills_dir_label} in {project_skills_dir}: {exc}"
        ) from exc


def remove_project_agent_skill(
    skill: str, project_skills_dir: Path, agent: str
) -> RemoveSkillResult:
    return _remove_skill(skill, project_skills_dir, _project_agent_target(agent))


def remove_project_skill(skill: str, project_skills_dir: Path) -> RemoveSkillResult:
    return remove_project_agent_skill(skill, project_skills_dir, "pi")


def remove_vault_skill(skill: str, vault_skills_dir: Path) -> RemoveSkillResult:
    return _remove_skill(skill, vault_skills_dir, _VAULT_TARGET)


def _remove_skill(
    skill: str, project_skills_dir: Path, target_style: _TargetStyle
) -> RemoveSkillResult:
    skill_name = normalize_skill_name(skill)
    _ensure_safe_project_skills_dir(project_skills_dir, target_style)
    target = project_skills_dir / skill_name
    _reject_symlinked_project_skill(target, target_style)
    if not target.is_dir():
        if target_style.target_kind == _PI_TARGET.target_kind:
            raise SvError(
                f"{target_style.skill_label} '{skill_name}' was not found in this project."
            )
        raise SvError(f"Vault skill '{skill_name}' was not found in this skill-vault.")

    original_manifest = ProjectManifestStore(project_skills_dir).load()
    backup_target = target.with_name(f".{target.name}.sv-remove-backup")
    if backup_target.exists():
        try:
            shutil.rmtree(backup_target)
        except OSError as exc:
            raise SvError(
                f"Failed to remove stale sv backup for {target_style.skill_label} '{skill_name}': {exc}"
            ) from exc

    try:
        target.rename(backup_target)
    except OSError as exc:
        raise SvError(
            f"Failed to remove {target_style.skill_label} '{skill_name}': {exc}"
        ) from exc

    try:
        _remove_manifest_entry_for_target(project_skills_dir, skill_name, target_style)
    except SvError as exc:
        try:
            backup_target.rename(target)
        except OSError as rollback_exc:
            raise SvError(
                f"Failed to remove {target_style.skill_label} '{skill_name}': manifest update failed "
                f"({exc}) and rollback failed: {rollback_exc}"
            ) from rollback_exc
        raise

    try:
        shutil.rmtree(backup_target)
    except OSError as exc:
        try:
            if not target.exists():
                backup_target.rename(target)
            ProjectManifestStore(project_skills_dir).save(original_manifest)
        except (OSError, SvError) as rollback_exc:
            raise SvError(
                f"Failed to remove {target_style.skill_label} '{skill_name}': cleanup failed "
                f"({exc}) and rollback failed: {rollback_exc}"
            ) from rollback_exc
        raise SvError(
            f"Failed to remove {target_style.skill_label} '{skill_name}': {exc}"
        ) from exc

    return RemoveSkillResult(
        skill=skill_name,
        target=target,
        target_kind=target_style.target_kind,
        target_agent=target_style.target_agent,
    )


def sync_project_agent_skills(
    catalog: Sequence[ProjectSourceSkill], project_skills_dir: Path, agent: str
) -> SyncResult:
    return _sync_skills(catalog, project_skills_dir, _project_agent_target(agent))


def sync_project_skills(
    catalog: Sequence[ProjectSourceSkill], project_skills_dir: Path
) -> SyncResult:
    return sync_project_agent_skills(catalog, project_skills_dir, "pi")


def sync_vault_skills(
    catalog: Sequence[ProjectSourceSkill], vault_skills_dir: Path
) -> SyncResult:
    return _sync_skills(catalog, vault_skills_dir, _VAULT_TARGET)


def update_project_agent_skills(
    catalog: Sequence[ProjectSourceSkill], project_skills_dir: Path, agent: str
) -> SyncResult:
    return _update_skills(catalog, project_skills_dir, _project_agent_target(agent))


def update_project_skills(
    catalog: Sequence[ProjectSourceSkill], project_skills_dir: Path
) -> SyncResult:
    return update_project_agent_skills(catalog, project_skills_dir, "pi")


def update_vault_skills(
    catalog: Sequence[ProjectSourceSkill], vault_skills_dir: Path
) -> SyncResult:
    return _update_skills(catalog, vault_skills_dir, _VAULT_TARGET)


def refresh_project_agent_skill_states(
    catalog: Sequence[ProjectSourceSkill], project_skills_dir: Path, agent: str
) -> None:
    """Refresh canonical manifest hash/state fields for managed project-agent skills.

    This records the latest checked local content hash and deterministic state flags
    without mutating skill folders. Installed hashes are preserved unless a later
    successful install/replacement writes a new manifest entry.
    """
    _refresh_skill_states(catalog, project_skills_dir, _project_agent_target(agent))


def refresh_project_skill_states(
    catalog: Sequence[ProjectSourceSkill], project_skills_dir: Path
) -> None:
    refresh_project_agent_skill_states(catalog, project_skills_dir, "pi")


def refresh_project_agent_skill_local_states(
    project_skills_dir: Path, agent: str
) -> None:
    """Refresh local hash/modified fields for managed project-agent skills only."""
    _refresh_local_skill_states(project_skills_dir, _project_agent_target(agent))


def refresh_project_skill_local_states(project_skills_dir: Path) -> None:
    refresh_project_agent_skill_local_states(project_skills_dir, "pi")


def refresh_vault_skill_states(
    catalog: Sequence[ProjectSourceSkill], vault_skills_dir: Path
) -> None:
    """Refresh canonical manifest hash/state fields for managed vault skills."""
    _refresh_skill_states(catalog, vault_skills_dir, _VAULT_TARGET)


def refresh_vault_skill_local_states(vault_skills_dir: Path) -> None:
    """Refresh local hash/modified fields for managed vault skills only."""
    _refresh_local_skill_states(vault_skills_dir, _VAULT_TARGET)


def _sync_skills(
    catalog: Sequence[ProjectSourceSkill],
    project_skills_dir: Path,
    target_style: _TargetStyle,
) -> SyncResult:
    _ensure_safe_project_skills_dir(project_skills_dir, target_style)
    if not project_skills_dir.is_dir():
        return SyncResult(
            updated=[],
            skipped=[],
            backfilled=[],
            no_skills_dir=True,
            target_kind=target_style.target_kind,
            target_agent=target_style.target_agent,
        )

    by_repo_name_path: dict[tuple[str, str, str], ProjectSourceSkill] = {}
    by_source_key_path: dict[tuple[str, str, str], ProjectSourceSkill] = {}
    by_name: dict[str, list[ProjectSourceSkill]] = {}
    for entry in catalog:
        for repo_id in (entry.repo_id, *entry.repo_aliases):
            by_repo_name_path[(entry.name, repo_id, entry.source_relative_path)] = entry
        by_source_key_path[
            (entry.name, repo_source_key(entry.repo_url), entry.source_relative_path)
        ] = entry
        by_name.setdefault(entry.name, []).append(entry)

    manifest = _load_manifest_for_target(project_skills_dir, target_style)
    updated: list[str] = []
    backfilled: list[str] = []
    skipped: list[SyncSkip] = []
    replacement_work: list[_ReplacementWork] = []
    manifest_updates: list[_ManifestUpdatePlan] = []

    try:
        local_skills = sorted(project_skills_dir.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise SvError(
            f"Failed to list {target_style.skills_dir_label} in {project_skills_dir}: {exc}"
        ) from exc

    for local_skill in local_skills:
        if local_skill.name.startswith("."):
            continue
        _reject_symlinked_project_skill(local_skill, target_style)
        _validate_project_skill_dir_name(local_skill, target_style)
        if not local_skill.is_dir():
            continue

        manifest_entry = manifest.get(local_skill.name)
        if manifest_entry is not None:
            manifest_source_key = _recorded_source_key(manifest_entry)
            entry = by_repo_name_path.get(
                (
                    manifest_entry.name,
                    manifest_entry.repo_id,
                    manifest_entry.source_path,
                )
            )
            if entry is not None and not _source_matches_recorded_key(
                entry, manifest_source_key
            ):
                entry = None
            if entry is None and manifest_source_key is not None:
                entry = by_source_key_path.get(
                    (
                        manifest_entry.name,
                        manifest_source_key,
                        manifest_entry.source_path,
                    )
                )
            if entry is None:
                refreshed = _refreshed_manifest_entry_state(
                    manifest_entry,
                    local_skill,
                    None,
                )
                manifest_updates.append(
                    _ManifestUpdatePlan(project_skills_dir, refreshed, target_style)
                )
                skipped.append(
                    SyncSkip(
                        skill=local_skill.name,
                        reason="source-missing",
                        repo_ids=(manifest_entry.repo_id,),
                    )
                )
                continue
            replacement_work.append(
                _ReplacementWork(
                    _ReplacementPlan(
                        entry, local_skill, project_skills_dir, target_style
                    )
                )
            )
            updated.append(local_skill.name)
            continue

        matches = by_name.get(local_skill.name, [])
        if len(matches) == 1:
            skipped.append(SyncSkip(skill=local_skill.name, reason="local-only"))
        elif len(matches) > 1:
            source_references: tuple[str, ...] = ()
            if _needs_path_aware_source_references(matches):
                source_references = tuple(
                    _source_reference_for(entry) for entry in matches
                )
            skipped.append(
                SyncSkip(
                    skill=local_skill.name,
                    reason="ambiguous",
                    repo_ids=tuple(entry.repo_id for entry in matches),
                    source_references=source_references,
                )
            )
        else:
            skipped.append(SyncSkip(skill=local_skill.name, reason="local-only"))

    prepared_replacements = _prepare_replacement_work_ordered(replacement_work)
    _commit_manifest_updates_and_replacements(manifest_updates, prepared_replacements)

    return SyncResult(
        updated=updated,
        skipped=skipped,
        backfilled=backfilled,
        no_skills_dir=False,
        target_kind=target_style.target_kind,
        target_agent=target_style.target_agent,
    )


def _update_skills(
    catalog: Sequence[ProjectSourceSkill],
    project_skills_dir: Path,
    target_style: _TargetStyle,
) -> SyncResult:
    _ensure_safe_project_skills_dir(project_skills_dir, target_style)
    if not project_skills_dir.is_dir():
        return SyncResult(
            updated=[],
            skipped=[],
            backfilled=[],
            no_skills_dir=True,
            target_kind=target_style.target_kind,
            target_agent=target_style.target_agent,
        )

    manifest = _load_manifest_for_target(project_skills_dir, target_style)
    updated: list[str] = []
    skipped: list[SyncSkip] = []
    replacement_work: list[_ReplacementWork] = []
    manifest_updates: list[_ManifestUpdatePlan] = []

    try:
        for skill_name, manifest_entry in sorted(manifest.items()):
            target = project_skills_dir / skill_name
            _reject_symlinked_project_skill(target, target_style)
            _validate_project_skill_dir_name(target, target_style)
            if not target.is_dir():
                continue

            source_entry = _catalog_entry_for_manifest(catalog, manifest_entry)
            refreshed = _refreshed_manifest_entry_state(
                manifest_entry,
                target,
                source_entry,
            )
            if source_entry is None:
                manifest_updates.append(
                    _ManifestUpdatePlan(project_skills_dir, refreshed, target_style)
                )
                skipped.append(
                    SyncSkip(
                        skill=skill_name,
                        reason="source-missing",
                        repo_ids=(manifest_entry.repo_id,),
                    )
                )
                continue

            installed_hash = manifest_entry.installed_content_hash
            local_hash = refreshed.local_content_hash
            source_hash = _available_source_content_hash(source_entry)
            source_skill_file_hash = refreshed.source_skill_file_hash
            baseline_hash = installed_hash
            if (
                baseline_hash is None
                and manifest_entry.source_content_hash is not None
                and local_hash == manifest_entry.source_content_hash
            ):
                baseline_hash = manifest_entry.source_content_hash

            if baseline_hash is None:
                if source_hash is None:
                    metadata = _materialize_entry_for_replace(
                        source_entry, target, target_style
                    )
                    _MATERIALIZATION.remove_materialization_path(
                        _sync_temp_target(target), ignore_errors=True
                    )
                    source_hash = metadata.content_hash
                    source_skill_file_hash = metadata.skill_file_hash
                if local_hash == source_hash:
                    refreshed = replace(
                        refreshed,
                        source_content_hash=source_hash,
                        source_skill_file_hash=source_skill_file_hash,
                        installed_content_hash=source_hash,
                        local_content_hash=local_hash,
                        orphan=False,
                        modified=False,
                        update_available=False,
                    )
                    manifest_updates.append(
                        _ManifestUpdatePlan(project_skills_dir, refreshed, target_style)
                    )
                    skipped.append(SyncSkip(skill=skill_name, reason="unchanged"))
                    continue
                refreshed = replace(
                    refreshed,
                    source_content_hash=source_hash,
                    source_skill_file_hash=source_skill_file_hash,
                    modified=True,
                    update_available=source_hash != local_hash,
                )
                manifest_updates.append(
                    _ManifestUpdatePlan(project_skills_dir, refreshed, target_style)
                )
                skipped.append(SyncSkip(skill=skill_name, reason="modified"))
                continue

            if local_hash != baseline_hash:
                if source_hash is None:
                    metadata = _materialize_entry_for_replace(
                        source_entry, target, target_style
                    )
                    _MATERIALIZATION.remove_materialization_path(
                        _sync_temp_target(target), ignore_errors=True
                    )
                    source_hash = metadata.content_hash
                    source_skill_file_hash = metadata.skill_file_hash
                refreshed = replace(
                    refreshed,
                    source_content_hash=source_hash,
                    source_skill_file_hash=source_skill_file_hash,
                    update_available=source_hash != baseline_hash,
                )
                manifest_updates.append(
                    _ManifestUpdatePlan(project_skills_dir, refreshed, target_style)
                )
                skipped.append(SyncSkip(skill=skill_name, reason="modified"))
                continue
            if source_hash is not None and source_hash == baseline_hash:
                refreshed = replace(
                    refreshed,
                    installed_content_hash=baseline_hash,
                    modified=False,
                    update_available=False,
                )
                manifest_updates.append(
                    _ManifestUpdatePlan(project_skills_dir, refreshed, target_style)
                )
                skipped.append(SyncSkip(skill=skill_name, reason="unchanged"))
                continue

            replacement_plan = _ReplacementPlan(
                source_entry, target, project_skills_dir, target_style
            )
            if source_hash is not None:
                replacement_work.append(_ReplacementWork(replacement_plan))
                updated.append(skill_name)
                continue

            metadata = _materialize_entry_for_replace(
                source_entry, target, target_style
            )
            if metadata.content_hash == baseline_hash:
                _MATERIALIZATION.remove_materialization_path(
                    _sync_temp_target(target), ignore_errors=True
                )
                refreshed = replace(
                    refreshed,
                    source_content_hash=metadata.content_hash,
                    source_skill_file_hash=metadata.skill_file_hash,
                    installed_content_hash=baseline_hash,
                    local_content_hash=local_hash,
                    orphan=False,
                    modified=False,
                    update_available=False,
                )
                manifest_updates.append(
                    _ManifestUpdatePlan(project_skills_dir, refreshed, target_style)
                )
                skipped.append(SyncSkip(skill=skill_name, reason="unchanged"))
                continue

            prepared = _PreparedReplacement(replacement_plan, metadata)
            replacement_work.append(_ReplacementWork(replacement_plan, prepared))
            updated.append(skill_name)
    except Exception:
        _cleanup_replacement_work(replacement_work)
        raise

    prepared_replacements = _prepare_replacement_work_ordered(replacement_work)
    _commit_manifest_updates_and_replacements(manifest_updates, prepared_replacements)

    return SyncResult(
        updated=updated,
        skipped=skipped,
        backfilled=[],
        no_skills_dir=False,
        target_kind=target_style.target_kind,
        target_agent=target_style.target_agent,
    )


def _refresh_skill_states(
    catalog: Sequence[ProjectSourceSkill],
    project_skills_dir: Path,
    target_style: _TargetStyle,
) -> None:
    _ensure_safe_project_skills_dir(project_skills_dir, target_style)
    entries = ProjectManifestStore(project_skills_dir).load()
    if not entries:
        return

    refresh_items: list[tuple[str, ManifestEntry, ProjectSourceSkill | None]] = []
    for key, manifest_entry in entries.items():
        if not _manifest_entry_matches_target(manifest_entry, target_style):
            continue
        _validate_manifest_entry_skill_name(manifest_entry, target_style)
        target = project_skills_dir / manifest_entry.name
        _reject_symlinked_project_skill(target, target_style)
        _validate_project_skill_dir_name(target, target_style)
        if target.is_dir():
            refresh_items.append(
                (
                    key,
                    manifest_entry,
                    _catalog_entry_for_manifest(catalog, manifest_entry),
                )
            )

    def worker(
        item: tuple[str, ManifestEntry, ProjectSourceSkill | None],
    ) -> _LocalStateRefresh:
        key, manifest_entry, source_entry = item
        target = project_skills_dir / manifest_entry.name
        return _LocalStateRefresh(
            key=key,
            entry=manifest_entry,
            refreshed=_refreshed_manifest_entry_state(
                manifest_entry,
                target,
                source_entry,
            ),
        )

    updated_entries = dict(entries)
    changed = False
    for result in map_ordered(refresh_items, worker):
        if result.refreshed != result.entry:
            updated_entries[result.key] = result.refreshed
            changed = True

    if changed:
        ProjectManifestStore(project_skills_dir).save(updated_entries)


def _refresh_local_skill_states(
    project_skills_dir: Path,
    target_style: _TargetStyle,
) -> None:
    from sv.hashing import sha256_skill_directory

    _ensure_safe_project_skills_dir(project_skills_dir, target_style)
    entries = ProjectManifestStore(project_skills_dir).load()
    if not entries:
        return

    refresh_items: list[tuple[str, ManifestEntry]] = []
    for key, manifest_entry in entries.items():
        if not _manifest_entry_matches_target(manifest_entry, target_style):
            continue
        _validate_manifest_entry_skill_name(manifest_entry, target_style)
        target = project_skills_dir / manifest_entry.name
        _reject_symlinked_project_skill(target, target_style)
        _validate_project_skill_dir_name(target, target_style)
        if target.is_dir():
            refresh_items.append((key, manifest_entry))

    def worker(item: tuple[str, ManifestEntry]) -> _LocalStateRefresh:
        key, manifest_entry = item
        target = project_skills_dir / manifest_entry.name
        local_content_hash = sha256_skill_directory(
            target, expected_name=manifest_entry.name
        )
        modified = (
            manifest_entry.installed_content_hash is not None
            and local_content_hash != manifest_entry.installed_content_hash
        )
        return _LocalStateRefresh(
            key=key,
            entry=manifest_entry,
            refreshed=replace(
                manifest_entry,
                local_content_hash=local_content_hash,
                modified=modified,
            ),
        )

    updated_entries = dict(entries)
    changed = False
    for result in map_ordered(refresh_items, worker):
        if result.refreshed != result.entry:
            updated_entries[result.key] = result.refreshed
            changed = True

    if changed:
        ProjectManifestStore(project_skills_dir).save(updated_entries)


def _refreshed_manifest_entry_state(
    manifest_entry: ManifestEntry,
    target: Path,
    source_entry: ProjectSourceSkill | None,
) -> ManifestEntry:
    from sv.hashing import sha256_file, sha256_skill_directory

    local_content_hash = sha256_skill_directory(
        target, expected_name=manifest_entry.name
    )
    source_content_hash = _known_source_content_hash(source_entry)
    source_skill_file_hash = _known_source_skill_file_hash(source_entry)
    if source_entry is not None and source_content_hash is None:
        try:
            if source_entry.source_path.is_dir():
                source_content_hash = sha256_skill_directory(
                    source_entry.source_path, expected_name=source_entry.name
                )
        except SvError:
            source_content_hash = None
    if source_entry is not None and source_skill_file_hash is None:
        try:
            skill_file = source_entry.source_path / "SKILL.md"
            if skill_file.is_file():
                source_skill_file_hash = sha256_file(skill_file)
        except SvError:
            source_skill_file_hash = None

    installed_content_hash = manifest_entry.installed_content_hash
    modified = (
        installed_content_hash is not None
        and local_content_hash != installed_content_hash
    )
    orphan = source_entry is None
    update_available = (
        not orphan
        and source_content_hash is not None
        and installed_content_hash is not None
        and source_content_hash != installed_content_hash
    )
    return replace(
        manifest_entry,
        source_content_hash=source_content_hash or manifest_entry.source_content_hash,
        source_skill_file_hash=(
            source_skill_file_hash or manifest_entry.source_skill_file_hash
        ),
        local_content_hash=local_content_hash,
        orphan=orphan,
        modified=modified,
        update_available=update_available,
    )


def _catalog_entry_for_manifest(
    catalog: Sequence[ProjectSourceSkill], manifest_entry: ManifestEntry
) -> ProjectSourceSkill | None:
    recorded_source_key = _recorded_source_key(manifest_entry)
    for entry in catalog:
        if entry.name != manifest_entry.name:
            continue
        if entry.source_relative_path != manifest_entry.source_path:
            continue
        if entry.repo_id == manifest_entry.repo_id and _source_matches_recorded_key(
            entry, recorded_source_key
        ):
            return entry
        if recorded_source_key is not None and _source_matches_recorded_key(
            entry, recorded_source_key
        ):
            return entry
    return None


def _known_source_content_hash(entry: ProjectSourceSkill | None) -> str | None:
    if entry is None:
        return None
    value = getattr(entry, "source_content_hash", None)
    return value if isinstance(value, str) else None


def _available_source_content_hash(entry: ProjectSourceSkill) -> str | None:
    known_hash = _known_source_content_hash(entry)
    if known_hash is not None:
        return known_hash
    from sv.hashing import sha256_skill_directory

    try:
        if entry.source_path.is_dir():
            return sha256_skill_directory(entry.source_path, expected_name=entry.name)
    except SvError:
        return None
    return None


def _known_source_skill_file_hash(entry: ProjectSourceSkill | None) -> str | None:
    if entry is None:
        return None
    value = getattr(entry, "source_skill_file_hash", None)
    return value if isinstance(value, str) else None


def _needs_path_aware_source_references(
    entries: Sequence[ProjectSourceSkill],
) -> bool:
    repo_ids = {entry.repo_id for entry in entries}
    return len(repo_ids) < len(entries) or any(
        entry.source_relative_path != f"skills/{entry.name}" for entry in entries
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


def _contains_unicode_format_character(value: str) -> bool:
    return any(unicodedata.category(char) == "Cf" for char in value)


def _ensure_safe_project_skills_dir(
    project_skills_dir: Path, target_style: _TargetStyle = _PI_TARGET
) -> None:
    for path in (*reversed(project_skills_dir.parents), project_skills_dir):
        try:
            if path.is_symlink():
                raise SvError(
                    f"Refusing to use symlinked {target_style.path_label} at {path}."
                )
        except OSError as exc:
            raise SvError(
                f"Failed to inspect {target_style.path_label} {path}: {exc}"
            ) from exc


def _reject_symlinked_project_skill(
    path: Path, target_style: _TargetStyle = _PI_TARGET
) -> None:
    try:
        is_symlink = path.is_symlink()
    except OSError as exc:
        raise SvError(
            f"Failed to inspect {target_style.skill_label} path {path}: {exc}"
        ) from exc
    if is_symlink:
        raise SvError(
            f"Refusing to manage symlinked {target_style.skill_label} '{path.name}' at {path}."
        )


def _validate_project_skill_dir_name(
    path: Path, target_style: _TargetStyle = _PI_TARGET
) -> None:
    try:
        normalize_skill_name(path.name)
    except SvError as exc:
        raise SvError(
            f"Invalid {target_style.skill_label} directory at {path}: {exc}"
        ) from exc


def _validate_manifest_entry_skill_name(
    entry: ManifestEntry, target_style: _TargetStyle = _PI_TARGET
) -> None:
    try:
        normalize_skill_name(entry.name)
    except SvError as exc:
        raise SvError(
            f"Invalid {target_style.skill_label} directory in sv manifest: {exc}"
        ) from exc


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


def _add_temp_target(target: Path) -> Path:
    return target.with_name(f".{target.name}.sv-add-tmp")


def _sync_temp_target(target: Path) -> Path:
    return target.with_name(f".{target.name}.sv-sync-tmp")


def _materialize_entry_for_add(
    entry: ProjectSourceSkill,
    target: Path,
    target_style: _TargetStyle = _PI_TARGET,
) -> _MaterializedSkillMetadata:
    error_message = f"Failed to add {target_style.skill_label} '{target.name}'"
    return _materialize_entry_to_temp(
        entry,
        _add_temp_target(target),
        skill_name=target.name,
        error_message=error_message,
    )


def _materialize_entry_to_temp(
    entry: ProjectSourceSkill,
    temp_target: Path,
    *,
    skill_name: str,
    error_message: str,
) -> _MaterializedSkillMetadata:
    """Materialize and validate one selected source skill before local mutation."""
    _MATERIALIZATION.remove_materialization_path(temp_target, ignore_errors=True)
    try:
        entry.materialize_to(temp_target)
        return _validate_materialized_skill_folder(temp_target, skill_name)
    except Exception as exc:
        _MATERIALIZATION.remove_materialization_path(temp_target, ignore_errors=True)
        if isinstance(exc, SvError) and str(exc).startswith(error_message):
            raise
        raise SvError(f"{error_message}: {exc}") from exc


def _validate_materialized_skill_folder(
    materialized_target: Path, skill_name: str
) -> _MaterializedSkillMetadata:
    from sv.hashing import sha256_file, sha256_skill_directory
    from sv.skills import parse_skill_file

    try:
        _MATERIALIZATION.validate_materialization_source_tree(materialized_target)
    except SvError as exc:
        if "symlink" in str(exc) or "Failed to inspect" in str(exc):
            _ensure_safe_source_skill_tree(materialized_target, skill_name)
        raise
    parse_skill_file(materialized_target / "SKILL.md", expected_folder=skill_name)
    return _MaterializedSkillMetadata(
        content_hash=sha256_skill_directory(
            materialized_target, expected_name=skill_name
        ),
        skill_file_hash=sha256_file(materialized_target / "SKILL.md"),
    )


def _replace_tree_and_update_manifest(
    entry: ProjectSourceSkill,
    target: Path,
    project_skills_dir: Path,
    target_style: _TargetStyle = _PI_TARGET,
) -> None:
    metadata = _materialize_entry_for_replace(entry, target, target_style)
    manifest_entry = _manifest_entry_for(entry, target_style, metadata=metadata)

    def update_manifest() -> None:
        _upsert_manifest_entry_for_target(
            project_skills_dir, manifest_entry, target_style
        )

    _replace_with_materialized_entry(
        target, after_replace=update_manifest, target_style=target_style
    )


def _prepare_replacement_plan(plan: _ReplacementPlan) -> _PreparedReplacement:
    metadata = _materialize_entry_for_replace(
        plan.entry, plan.target, plan.target_style
    )
    return _PreparedReplacement(plan=plan, metadata=metadata)


def _cleanup_replacement_work(work: Sequence[_ReplacementWork]) -> None:
    for item in work:
        _MATERIALIZATION.remove_materialization_path(
            _sync_temp_target(item.plan.target), ignore_errors=True
        )


def _prepare_replacement_work_ordered(
    work: Sequence[_ReplacementWork],
) -> list[_PreparedReplacement]:
    plans_to_prepare = [item.plan for item in work if item.prepared is None]
    try:
        prepared_iter = iter(map_ordered(plans_to_prepare, _prepare_replacement_plan))
        prepared_replacements: list[_PreparedReplacement] = []
        for item in work:
            if item.prepared is not None:
                prepared_replacements.append(item.prepared)
            else:
                prepared_replacements.append(next(prepared_iter))
        return prepared_replacements
    except Exception:
        _cleanup_replacement_work(work)
        raise


def _commit_prepared_replacement(prepared: _PreparedReplacement) -> None:
    plan = prepared.plan
    manifest_entry = _manifest_entry_for(
        plan.entry, plan.target_style, metadata=prepared.metadata
    )

    def update_manifest() -> None:
        _upsert_manifest_entry_for_target(
            plan.project_skills_dir, manifest_entry, plan.target_style
        )

    _replace_with_materialized_entry(
        plan.target, after_replace=update_manifest, target_style=plan.target_style
    )


def _cleanup_prepared_replacements(
    prepared_replacements: Sequence[_PreparedReplacement],
) -> None:
    for prepared in prepared_replacements:
        _MATERIALIZATION.remove_materialization_path(
            _sync_temp_target(prepared.plan.target), ignore_errors=True
        )


def _commit_prepared_replacements(
    prepared_replacements: Sequence[_PreparedReplacement],
) -> None:
    try:
        for prepared in prepared_replacements:
            _commit_prepared_replacement(prepared)
    except Exception:
        _cleanup_prepared_replacements(prepared_replacements)
        raise


def _commit_manifest_updates(updates: Sequence[_ManifestUpdatePlan]) -> None:
    for update in updates:
        _upsert_manifest_entry_for_target(
            update.project_skills_dir, update.entry, update.target_style
        )


def _commit_manifest_updates_and_replacements(
    updates: Sequence[_ManifestUpdatePlan],
    prepared_replacements: Sequence[_PreparedReplacement],
) -> None:
    try:
        _commit_manifest_updates(updates)
        _commit_prepared_replacements(prepared_replacements)
    except Exception:
        _cleanup_prepared_replacements(prepared_replacements)
        raise


def _materialize_entry_for_replace(
    entry: ProjectSourceSkill,
    target: Path,
    target_style: _TargetStyle = _PI_TARGET,
) -> _MaterializedSkillMetadata:
    if target_style.target_kind == _PI_TARGET.target_kind:
        error_message = f"Failed to sync skill '{target.name}'"
    else:
        error_message = f"Failed to sync {target_style.skill_label} '{target.name}'"
    return _materialize_entry_to_temp(
        entry,
        _sync_temp_target(target),
        skill_name=target.name,
        error_message=error_message,
    )


def _replace_with_materialized_entry(
    target: Path,
    after_replace: Callable[[], None],
    target_style: _TargetStyle = _PI_TARGET,
) -> None:
    """Replace target with materialized source while preserving target on failure."""
    temp_target = _sync_temp_target(target)
    backup_target = target.with_name(f".{target.name}.sv-sync-backup")
    if target_style.target_kind == _PI_TARGET.target_kind:
        error_message = f"Failed to sync skill '{target.name}'"
    else:
        error_message = f"Failed to sync {target_style.skill_label} '{target.name}'"
    _MATERIALIZATION.remove_materialization_path(backup_target, ignore_errors=True)
    _MATERIALIZATION.replace_with_materialized_skill_folder(
        temp_target,
        target,
        backup_target,
        error_message=error_message,
        after_replace=after_replace,
    )


def _source_reference_for(entry: ProjectSourceSkill) -> str:
    if entry.source_relative_path == f"skills/{entry.name}":
        return entry.repo_id
    return f"{entry.repo_id}:{entry.source_relative_path}"


def _source_reference_for_manifest(entry: ManifestEntry) -> str:
    if entry.source_path == f"skills/{entry.name}":
        return entry.repo_id
    return f"{entry.repo_id}:{entry.source_path}"


def _upsert_manifest_entry_for_target(
    project_skills_dir: Path, entry: ManifestEntry, target_style: _TargetStyle
) -> None:
    store = ProjectManifestStore(project_skills_dir)
    entries = store.load()
    for key, existing_entry in entries.items():
        if existing_entry.name == entry.name and _manifest_entry_matches_target(
            existing_entry, target_style
        ):
            entries[key] = entry
            store.save(entries)
            return
    key = entry.name
    if key in entries:
        key = f"{entry.target_kind}:{entry.target_agent or ''}:{entry.target_path}:{entry.name}"
    entries[key] = entry
    store.save(entries)


def _load_manifest_for_target(
    project_skills_dir: Path, target_style: _TargetStyle
) -> dict[str, ManifestEntry]:
    return {
        entry.name: entry
        for entry in ProjectManifestStore(project_skills_dir).load().values()
        if _manifest_entry_matches_target(entry, target_style)
    }


def _remove_manifest_entry_for_target(
    project_skills_dir: Path, skill_name: str, target_style: _TargetStyle
) -> None:
    store = ProjectManifestStore(project_skills_dir)
    entries = store.load()
    for key, existing_entry in list(entries.items()):
        if existing_entry.name == skill_name and _manifest_entry_matches_target(
            existing_entry, target_style
        ):
            del entries[key]
            store.save(entries)
            return


def _manifest_entry_matches_target(
    entry: ManifestEntry, target_style: _TargetStyle
) -> bool:
    return (
        entry.target_kind == target_style.target_kind
        and entry.target_agent == target_style.target_agent
        and entry.target_path == f"{target_style.target_path_prefix}/{entry.name}"
    )


def _manifest_entry_for(
    entry: ProjectSourceSkill,
    target_style: _TargetStyle = _PI_TARGET,
    *,
    metadata: _MaterializedSkillMetadata | None = None,
) -> ManifestEntry:
    content_hash = metadata.content_hash if metadata is not None else None
    skill_file_hash = metadata.skill_file_hash if metadata is not None else None
    return ManifestEntry(
        name=entry.name,
        repo_id=entry.repo_id,
        repo_url=entry.repo_url,
        source_path=entry.source_relative_path,
        description=entry.description,
        target_kind=target_style.target_kind,
        target_agent=target_style.target_agent,
        target_path=f"{target_style.target_path_prefix}/{entry.name}",
        source_backend=entry.source_backend,
        source_content_hash=content_hash,
        source_skill_file_hash=skill_file_hash,
        installed_content_hash=content_hash,
        local_content_hash=content_hash,
    )

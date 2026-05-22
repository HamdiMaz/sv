from dataclasses import replace
from pathlib import Path
import os
import shutil
import threading
from typing import cast

import pytest

from tests.helpers import assert_no_partial_sv_dirs
from sv import project as project_module
from sv.agents import PiAdapter
import sv.hashing as hashing_module
from sv.catalog import SourceSkill
from sv.errors import SvError
from sv.hashing import sha256_skill_directory
from sv.manifest import ManifestEntry, load_manifest, save_manifest
from sv.materialization import apply_skill_file_modes
from sv.project import (
    add_all_project_agent_skills,
    add_all_project_skills,
    add_all_vault_skills,
    add_project_agent_skill,
    add_project_skill,
    add_vault_skill,
    list_project_skills,
    normalize_skill_name,
    remove_project_agent_skill,
    remove_project_skill,
    sync_project_agent_skills,
    sync_project_skills,
    refresh_project_agent_skill_local_states,
    refresh_project_agent_skill_states,
    refresh_project_skill_local_states,
    update_project_agent_skills,
    update_project_skills,
)


def make_source_skill(
    source_root: Path, name: str, repo_id: str = "Org/Skills"
) -> SourceSkill:
    skill_dir = source_root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name.title()} skill.\n---\n"
    )
    (skill_dir / "notes.md").write_text(f"{name} remote\n")
    return SourceSkill(
        name=name,
        description=f"{name.title()} skill.",
        repo_id=repo_id,
        repo_url=f"https://github.com/{repo_id}.git",
        repo_path=source_root,
        source_path=skill_dir,
    )


def entry_manifest(entry: SourceSkill) -> ManifestEntry:
    return ManifestEntry(
        name=entry.name,
        repo_id=entry.repo_id,
        repo_url=entry.repo_url,
        source_path=entry.source_relative_path,
        description=entry.description,
    )


def test_pi_adapter_uses_project_pi_skills_dir(tmp_path: Path):
    adapter = PiAdapter()

    assert adapter.name == "pi"
    assert adapter.project_skill_dir(tmp_path) == tmp_path / ".pi" / "skills"


def test_pi_adapter_builds_isolated_run_command():
    adapter = PiAdapter()

    assert adapter.run_command(["--model", "fast"]) == [
        "pi",
        "--no-skills",
        "--skill",
        ".pi/skills",
        "--model",
        "fast",
    ]


def test_add_project_skill_from_catalog_writes_manifest(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"

    result = add_project_skill(entry, project_skills)

    assert result.status == "added"
    assert result.skill == "alpha"
    assert result.repo_id == "Org/Skills"
    assert (project_skills / "alpha" / "notes.md").read_text() == "alpha remote\n"
    manifest = load_manifest(project_skills)
    assert manifest["alpha"].repo_id == "Org/Skills"
    assert manifest["alpha"].source_path == "skills/alpha"


def test_add_project_agent_skill_writes_claude_target_metadata(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project = tmp_path / "project"
    project_skills = project / ".claude" / "skills"

    result = add_project_agent_skill(entry, project_skills, "claude")

    assert result.status == "added"
    assert result.target == project_skills / "alpha"
    assert result.target_kind == "project-agent"
    assert result.target_agent == "claude"
    manifest = load_manifest(project_skills)
    assert manifest["alpha"].target_kind == "project-agent"
    assert manifest["alpha"].target_agent == "claude"
    assert manifest["alpha"].target_path == ".claude/skills/alpha"


def test_add_applies_indexed_executable_paths_before_hashing(tmp_path: Path) -> None:
    source = tmp_path / "source" / "skills" / "alpha"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill.\n---\n", encoding="utf-8"
    )
    script = source / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env python3\nprint('alpha')\n", encoding="utf-8")
    os.chmod(script, 0o755)
    expected_hash = sha256_skill_directory(source, expected_name="alpha")
    os.chmod(script, 0o644)

    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=tmp_path / "source",
        source_path=source,
        source_relative_path="skills/alpha",
        source_content_hash=expected_hash,
        source_executable_paths=("scripts/run.py",),
    )
    target_dir = tmp_path / "project" / ".pi" / "skills"

    result = add_project_agent_skill(entry, target_dir, "pi")

    installed_script = target_dir / "alpha" / "scripts" / "run.py"
    assert result.status == "added"
    assert os.access(installed_script, os.X_OK)
    assert sha256_skill_directory(target_dir / "alpha", expected_name="alpha") == expected_hash


def test_add_repairs_unknown_executable_modes_after_hash_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "source" / "skills" / "alpha"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill.\n---\n", encoding="utf-8"
    )
    script = source / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env python3\nprint('alpha')\n", encoding="utf-8")
    os.chmod(script, 0o755)
    expected_hash = sha256_skill_directory(source, expected_name="alpha")
    os.chmod(script, 0o644)
    repair_calls: list[Path] = []

    def repair(destination: Path) -> None:
        repair_calls.append(destination)
        apply_skill_file_modes(destination, ("scripts/run.py",))

    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=tmp_path / "source",
        source_path=source,
        source_relative_path="skills/alpha",
        source_content_hash=expected_hash,
        source_executable_paths=None,
        _mode_repairer=repair,
    )
    target_dir = tmp_path / "project" / ".pi" / "skills"

    result = add_project_agent_skill(entry, target_dir, "pi")

    installed = target_dir / "alpha"
    assert result.status == "added"
    assert repair_calls == [target_dir / ".alpha.sv-add-tmp"]
    assert os.access(installed / "scripts" / "run.py", os.X_OK)
    assert sha256_skill_directory(installed, expected_name="alpha") == expected_hash


def test_add_all_project_agent_skills_returns_claude_target_metadata_for_empty_result(
    tmp_path: Path,
):
    project_skills = tmp_path / "project" / ".claude" / "skills"

    result = add_all_project_agent_skills([], project_skills, "claude")

    assert result.results == []
    assert result.target_kind == "project-agent"
    assert result.target_agent == "claude"


def test_add_all_project_agent_skills_returns_claude_target_metadata_for_results(
    tmp_path: Path,
):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".claude" / "skills"

    result = add_all_project_agent_skills([entry], project_skills, "claude")

    assert result.target_kind == "project-agent"
    assert result.target_agent == "claude"
    assert [(item.target_kind, item.target_agent) for item in result.results] == [
        ("project-agent", "claude")
    ]


def test_project_manifest_keeps_same_skill_for_multiple_agents(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project = tmp_path / "project"
    pi_skills = project / ".pi" / "skills"
    claude_skills = project / ".claude" / "skills"

    add_project_agent_skill(entry, pi_skills, "pi")
    add_project_agent_skill(entry, claude_skills, "claude")

    manifest = load_manifest(pi_skills)
    assert len(manifest) == 2
    assert sorted(
        (item.name, item.target_agent, item.target_path)
        for item in manifest.values()
    ) == [
        ("alpha", "claude", ".claude/skills/alpha"),
        ("alpha", "pi", ".pi/skills/alpha"),
    ]


def test_remove_project_agent_skill_removes_only_selected_agent(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project = tmp_path / "project"
    pi_skills = project / ".pi" / "skills"
    claude_skills = project / ".claude" / "skills"
    add_project_agent_skill(entry, pi_skills, "pi")
    add_project_agent_skill(entry, claude_skills, "claude")

    result = remove_project_agent_skill("alpha", claude_skills, "claude")

    assert result.target_agent == "claude"
    assert not (claude_skills / "alpha").exists()
    assert (pi_skills / "alpha").is_dir()
    remaining = load_manifest(pi_skills)
    assert [(item.name, item.target_agent) for item in remaining.values()] == [
        ("alpha", "pi")
    ]


def test_remove_project_agent_skill_missing_claude_uses_claude_label(tmp_path: Path):
    project_skills = tmp_path / "project" / ".claude" / "skills"

    with pytest.raises(
        SvError, match="Claude skill 'alpha' was not found in this project"
    ):
        remove_project_agent_skill("alpha", project_skills, "claude")


def test_sync_project_agent_skills_syncs_only_selected_agent(tmp_path: Path):
    source_alpha = make_source_skill(tmp_path / "source", "alpha")
    project = tmp_path / "project"
    pi_skills = project / ".pi" / "skills"
    claude_skills = project / ".claude" / "skills"
    add_project_agent_skill(source_alpha, pi_skills, "pi")
    add_project_agent_skill(source_alpha, claude_skills, "claude")
    (source_alpha.source_path / "notes.md").write_text("alpha synced\n")

    result = sync_project_agent_skills([source_alpha], claude_skills, "claude")

    assert result.target_agent == "claude"
    assert result.updated == ["alpha"]
    assert (claude_skills / "alpha" / "notes.md").read_text() == "alpha synced\n"
    assert (pi_skills / "alpha" / "notes.md").read_text() == "alpha remote\n"


def test_update_project_agent_skills_updates_only_selected_agent(tmp_path: Path):
    source_alpha = make_source_skill(tmp_path / "source", "alpha")
    project = tmp_path / "project"
    agents_skills = project / ".agents" / "skills"
    pi_skills = project / ".pi" / "skills"
    add_project_agent_skill(source_alpha, agents_skills, "agents")
    add_project_agent_skill(source_alpha, pi_skills, "pi")
    (source_alpha.source_path / "notes.md").write_text("alpha updated\n")

    result = update_project_agent_skills([source_alpha], agents_skills, "agents")

    assert result.target_agent == "agents"
    assert result.updated == ["alpha"]
    assert (agents_skills / "alpha" / "notes.md").read_text() == "alpha updated\n"
    assert (pi_skills / "alpha" / "notes.md").read_text() == "alpha remote\n"


def test_add_project_skill_validates_materialized_folder_through_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    delegate = project_module.DEFAULT_MATERIALIZATION_ADAPTER

    class SpyMaterializationAdapter:
        def __init__(self) -> None:
            self.validated: list[Path] = []

        def validate_materialization_source_tree(self, source: Path) -> None:
            self.validated.append(source)
            delegate.validate_materialization_source_tree(source)

        def remove_materialization_path(
            self, path: Path, *, ignore_errors: bool = False
        ) -> None:
            delegate.remove_materialization_path(path, ignore_errors=ignore_errors)

        def apply_skill_file_modes(self, *args, **kwargs) -> None:
            delegate.apply_skill_file_modes(*args, **kwargs)

        def install_materialized_skill_folder(self, *args, **kwargs) -> None:
            delegate.install_materialized_skill_folder(*args, **kwargs)

    spy = SpyMaterializationAdapter()
    monkeypatch.setattr(project_module, "_MATERIALIZATION", spy)

    result = add_project_skill(entry, project_skills)

    assert result.status == "added"
    assert spy.validated == [project_skills / ".alpha.sv-add-tmp"]


def test_add_project_skill_existing_skill_does_not_write_manifest(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    existing = project_skills / "alpha"
    existing.mkdir(parents=True)
    (existing / "notes.md").write_text("local\n")

    result = add_project_skill(entry, project_skills)

    assert result.status == "exists"
    assert load_manifest(project_skills) == {}
    assert (existing / "notes.md").read_text() == "local\n"


def test_refresh_project_skill_states_detects_local_edit_by_hash(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    add_project_skill(entry, project_skills)
    original_manifest_entry = load_manifest(project_skills)["alpha"]

    local_skill = project_skills / "alpha"
    (local_skill / "notes.md").write_text("alpha local edit\n")

    project_module.refresh_project_skill_states([entry], project_skills)

    refreshed = load_manifest(project_skills)["alpha"]
    assert refreshed.installed_content_hash == original_manifest_entry.installed_content_hash
    assert refreshed.local_content_hash == sha256_skill_directory(
        local_skill, expected_name="alpha"
    )
    assert refreshed.modified is True
    assert refreshed.update_available is False


def test_refresh_project_skill_states_marks_update_available_by_source_hash(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    add_project_skill(entry, project_skills)
    original_manifest_entry = load_manifest(project_skills)["alpha"]

    (entry.source_path / "notes.md").write_text("alpha remote v2\n")

    project_module.refresh_project_skill_states([entry], project_skills)

    refreshed = load_manifest(project_skills)["alpha"]
    assert refreshed.installed_content_hash == original_manifest_entry.installed_content_hash
    assert refreshed.local_content_hash == original_manifest_entry.installed_content_hash
    assert refreshed.source_content_hash == sha256_skill_directory(
        entry.source_path, expected_name="alpha"
    )
    assert refreshed.modified is False
    assert refreshed.update_available is True


def test_refresh_project_skill_states_marks_and_clears_orphan_candidate(
    tmp_path: Path,
):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    add_project_skill(entry, project_skills)

    project_module.refresh_project_skill_states([], project_skills)

    orphaned = load_manifest(project_skills)["alpha"]
    assert orphaned.orphan is True
    assert orphaned.update_available is False

    project_module.refresh_project_skill_states([entry], project_skills)

    assert load_manifest(project_skills)["alpha"].orphan is False


def test_refresh_project_agent_skill_states_refreshes_only_selected_agent(
    tmp_path: Path,
):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project = tmp_path / "project"
    pi_skills = project / ".pi" / "skills"
    claude_skills = project / ".claude" / "skills"
    add_project_agent_skill(entry, pi_skills, "pi")
    add_project_agent_skill(entry, claude_skills, "claude")
    original_pi = next(
        item for item in load_manifest(pi_skills).values() if item.target_agent == "pi"
    )

    (pi_skills / "alpha" / "notes.md").write_text("alpha pi local edit\n")
    (claude_skills / "alpha" / "notes.md").write_text("alpha claude local edit\n")

    refresh_project_agent_skill_states([entry], claude_skills, "claude")

    manifest = load_manifest(pi_skills)
    pi_entry = next(item for item in manifest.values() if item.target_agent == "pi")
    claude_entry = next(
        item for item in manifest.values() if item.target_agent == "claude"
    )
    assert pi_entry.local_content_hash == original_pi.local_content_hash
    assert pi_entry.modified is False
    assert claude_entry.local_content_hash == sha256_skill_directory(
        claude_skills / "alpha", expected_name="alpha"
    )
    assert claude_entry.modified is True


def test_refresh_project_agent_skill_local_states_refreshes_only_selected_agent(
    tmp_path: Path,
):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project = tmp_path / "project"
    pi_skills = project / ".pi" / "skills"
    agents_skills = project / ".agents" / "skills"
    add_project_agent_skill(entry, pi_skills, "pi")
    add_project_agent_skill(entry, agents_skills, "agents")
    original_pi = next(
        item for item in load_manifest(pi_skills).values() if item.target_agent == "pi"
    )

    (pi_skills / "alpha" / "notes.md").write_text("alpha pi local edit\n")
    (agents_skills / "alpha" / "notes.md").write_text("alpha agents local edit\n")

    refresh_project_agent_skill_local_states(agents_skills, "agents")

    manifest = load_manifest(pi_skills)
    pi_entry = next(item for item in manifest.values() if item.target_agent == "pi")
    agents_entry = next(
        item for item in manifest.values() if item.target_agent == "agents"
    )
    assert pi_entry.local_content_hash == original_pi.local_content_hash
    assert pi_entry.modified is False
    assert agents_entry.local_content_hash == sha256_skill_directory(
        agents_skills / "alpha", expected_name="alpha"
    )
    assert agents_entry.modified is True


def test_refresh_vault_skill_states_detects_local_edit_by_hash(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    vault_skills = tmp_path / "vault" / "skills"
    add_vault_skill(entry, vault_skills)
    original_manifest_entry = load_manifest(vault_skills)["alpha"]

    local_skill = vault_skills / "alpha"
    (local_skill / "notes.md").write_text("alpha vault edit\n")

    project_module.refresh_vault_skill_states([entry], vault_skills)

    refreshed = load_manifest(vault_skills)["alpha"]
    assert refreshed.target_kind == "skill-vault"
    assert refreshed.target_agent is None
    assert refreshed.target_path == "skills/alpha"
    assert refreshed.installed_content_hash == original_manifest_entry.installed_content_hash
    assert refreshed.local_content_hash == sha256_skill_directory(
        local_skill, expected_name="alpha"
    )
    assert refreshed.modified is True


def test_add_project_skill_existing_file_raises_error(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    blocker = project_skills / "alpha"
    blocker.parent.mkdir(parents=True)
    blocker.write_text("not a directory\n")

    with pytest.raises(SvError, match="non-directory path already exists"):
        add_project_skill(entry, project_skills)

    assert blocker.read_text() == "not a directory\n"
    assert load_manifest(project_skills) == {}


def test_add_project_skill_reports_project_directory_creation_failures(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    project_skills.parent.parent.mkdir(parents=True)
    project_skills.parent.write_text("not a directory\n")

    with pytest.raises(SvError, match="Failed to prepare Pi skills directory"):
        add_project_skill(entry, project_skills)


def test_add_project_skill_rejects_symlinked_project_ancestor(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    real_project = tmp_path / "real-project"
    real_project.mkdir()
    project_link = tmp_path / "project-link"
    project_link.symlink_to(real_project, target_is_directory=True)
    project_skills = project_link / ".pi" / "skills"

    with pytest.raises(SvError, match="symlinked Pi skills path"):
        add_project_skill(entry, project_skills)

    assert not (real_project / ".pi").exists()


def test_add_project_skill_wraps_copy_failures_and_cleans_temp(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    temp_dir = project_skills / ".alpha.sv-add-tmp"

    def fail_copytree(source, target):
        target.mkdir(parents=True)
        (target / "partial.txt").write_text("partial\n")
        raise OSError("copy failed")

    monkeypatch.setattr(shutil, "copytree", fail_copytree)

    with pytest.raises(SvError, match="Failed to add Pi skill 'alpha'"):
        add_project_skill(entry, project_skills)

    assert not (project_skills / "alpha").exists()
    assert not temp_dir.exists()
    assert_no_partial_sv_dirs(project_skills)
    assert load_manifest(project_skills) == {}


def test_add_project_skill_cleans_stale_add_temp_directory_before_copy(
    tmp_path: Path,
):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    temp_dir = project_skills / ".alpha.sv-add-tmp"
    temp_dir.mkdir(parents=True)
    (temp_dir / "stale.txt").write_text("stale\n")

    result = add_project_skill(entry, project_skills)

    assert result.status == "added"
    assert result.skill == "alpha"
    assert not temp_dir.exists()
    assert (project_skills / "alpha" / "notes.md").read_text() == "alpha remote\n"
    manifest = load_manifest(project_skills)
    assert manifest["alpha"].repo_id == "Org/Skills"
    assert_no_partial_sv_dirs(project_skills)


def test_add_project_skill_missing_source_skill_raises_error(tmp_path: Path):
    entry = SourceSkill(
        name="missing",
        description="Missing skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=tmp_path / "source",
        source_path=tmp_path / "source" / "skills" / "missing",
    )

    with pytest.raises(SvError, match="Skill 'missing' was not found"):
        add_project_skill(entry, tmp_path / "project" / ".pi" / "skills")


def test_add_project_skill_rejects_symlinks_inside_source_skill(tmp_path: Path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support is required")
    entry = make_source_skill(tmp_path / "source", "alpha")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret\n")
    os.symlink(secret, entry.source_path / "secret-link.txt")
    project_skills = tmp_path / "project" / ".pi" / "skills"

    with pytest.raises(SvError, match="contains a symlink"):
        add_project_skill(entry, project_skills)

    assert not (project_skills / "alpha").exists()


def test_add_project_skill_rejects_symlinked_project_skills_dir(tmp_path: Path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support is required")
    entry = make_source_skill(tmp_path / "source", "alpha")
    project = tmp_path / "project"
    outside = tmp_path / "outside-skills"
    outside.mkdir()
    (project / ".pi").mkdir(parents=True)
    os.symlink(outside, project / ".pi" / "skills")

    with pytest.raises(SvError, match="Refusing to use symlinked Pi skills path"):
        add_project_skill(entry, project / ".pi" / "skills")

    assert not (outside / "alpha").exists()


def test_add_project_skill_malformed_manifest_prevents_copy(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    (project_skills / ".sv-manifest.toml").write_text("skills = [\n")

    with pytest.raises(SvError, match="Failed to read sv manifest"):
        add_project_skill(entry, project_skills)

    assert not (project_skills / "alpha").exists()


def test_add_project_skill_rolls_back_copy_when_manifest_update_fails(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    temp_dir = project_skills / ".alpha.sv-add-tmp"

    def fail_manifest_save(self, entries):
        raise SvError("manifest write failed")

    monkeypatch.setattr("sv.project.ProjectManifestStore.save", fail_manifest_save)

    with pytest.raises(SvError, match="manifest write failed"):
        add_project_skill(entry, project_skills)

    assert not (project_skills / "alpha").exists()
    assert not temp_dir.exists()
    assert_no_partial_sv_dirs(project_skills)


def test_add_project_skill_reports_manifest_failure_rollback_failure(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    temp_dir = project_skills / ".alpha.sv-add-tmp"
    original_rmtree = shutil.rmtree

    def fail_manifest_save(self, entries):
        raise SvError("manifest write failed")

    def fail_rmtree(path, *args, **kwargs):
        if path.name == "alpha":
            raise OSError("rollback failed")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("sv.project.ProjectManifestStore.save", fail_manifest_save)
    monkeypatch.setattr(shutil, "rmtree", fail_rmtree)

    with pytest.raises(SvError, match="rollback failed"):
        add_project_skill(entry, project_skills)

    assert (project_skills / "alpha").exists()
    assert not temp_dir.exists()
    assert_no_partial_sv_dirs(project_skills)


def test_add_all_project_skills_copies_all_source_skills(tmp_path: Path):
    alpha = make_source_skill(tmp_path / "source", "alpha")
    beta = make_source_skill(tmp_path / "source", "beta")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    existing = project_skills / "beta"
    existing.mkdir(parents=True)
    (existing / "notes.md").write_text("local beta\n")

    result = add_all_project_skills([alpha, beta], project_skills)

    assert [(item.skill, item.status) for item in result.results] == [
        ("alpha", "added"),
        ("beta", "exists"),
    ]
    assert (project_skills / "alpha" / "notes.md").read_text() == "alpha remote\n"
    assert (project_skills / "beta" / "notes.md").read_text() == "local beta\n"


class BlockingProjectSourceSkill:
    description = "Blocking skill."
    repo_id = "Org/Skills"
    repo_url = "https://github.com/Org/Skills.git"
    repo_aliases: tuple[str, ...] = ()
    source_backend = "blocking"

    def __init__(
        self,
        name: str,
        source_root: Path,
        started: threading.Event,
        peer_started: threading.Event,
    ):
        self.name = name
        self.source_path = source_root / "skills" / name
        self.source_relative_path = f"skills/{name}"
        self.started = started
        self.peer_started = peer_started
        self.source_path.mkdir(parents=True)
        (self.source_path / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            "description: Blocking skill.\n"
            "---\n"
        )

    def materialize_to(self, destination: Path) -> None:
        self.started.set()
        assert self.peer_started.wait(2), "add-all materialization did not overlap"
        shutil.copytree(self.source_path, destination)


def test_add_all_project_skills_prepares_new_skills_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SV_JOBS", "2")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    source_root = tmp_path / "source"
    alpha_started = threading.Event()
    beta_started = threading.Event()
    catalog = [
        BlockingProjectSourceSkill("alpha", source_root, alpha_started, beta_started),
        BlockingProjectSourceSkill("beta", source_root, beta_started, alpha_started),
    ]
    result = add_all_project_skills(catalog, project_skills)
    assert [(item.skill, item.status) for item in result.results] == [
        ("alpha", "added"),
        ("beta", "added"),
    ]
    assert sorted(path.name for path in project_skills.iterdir() if not path.name.startswith(".")) == ["alpha", "beta"]
    assert sorted(load_manifest(project_skills)) == ["alpha", "beta"]


def test_refresh_project_skill_local_states_hashes_skills_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SV_JOBS", "2")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    source_root = tmp_path / "source"
    project_skills.mkdir(parents=True)
    alpha_materialize_started = threading.Event()
    beta_materialize_started = threading.Event()
    alpha = BlockingProjectSourceSkill(
        "alpha", source_root, alpha_materialize_started, beta_materialize_started
    )
    beta = BlockingProjectSourceSkill(
        "beta", source_root, beta_materialize_started, alpha_materialize_started
    )
    add_all_project_skills([alpha, beta], project_skills)

    alpha_started = threading.Event()
    beta_started = threading.Event()
    original_hash = hashing_module.sha256_skill_directory

    def blocking_hash(path: Path, *, expected_name=None):
        if path.name == "alpha":
            alpha_started.set()
            assert beta_started.wait(2), "local state hashing did not overlap"
        if path.name == "beta":
            beta_started.set()
            assert alpha_started.wait(2), "local state hashing did not overlap"
        return original_hash(path, expected_name=expected_name)

    monkeypatch.setattr(hashing_module, "sha256_skill_directory", blocking_hash)

    refresh_project_skill_local_states(project_skills)

    manifest = load_manifest(project_skills)
    assert manifest["alpha"].local_content_hash is not None
    assert manifest["beta"].local_content_hash is not None


def test_sync_project_skills_prepares_replacements_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SV_JOBS", "2")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    source_root = tmp_path / "source"
    project_skills.mkdir(parents=True)
    alpha_started = threading.Event()
    beta_started = threading.Event()
    alpha = BlockingProjectSourceSkill("alpha", source_root, alpha_started, beta_started)
    beta = BlockingProjectSourceSkill("beta", source_root, beta_started, alpha_started)
    add_all_project_skills([alpha, beta], project_skills)
    alpha.started = threading.Event()
    beta.started = threading.Event()
    alpha.peer_started = beta.started
    beta.peer_started = alpha.started
    (alpha.source_path / "notes.md").write_text("alpha v2\n")
    (beta.source_path / "notes.md").write_text("beta v2\n")
    result = sync_project_skills([alpha, beta], project_skills)
    assert result.updated == ["alpha", "beta"]
    assert result.skipped == []
    assert (project_skills / "alpha" / "notes.md").read_text() == "alpha v2\n"
    assert (project_skills / "beta" / "notes.md").read_text() == "beta v2\n"
    manifest = load_manifest(project_skills)
    alpha_hash = sha256_skill_directory(alpha.source_path, expected_name="alpha")
    beta_hash = sha256_skill_directory(beta.source_path, expected_name="beta")
    assert manifest["alpha"].source_content_hash == alpha_hash
    assert manifest["alpha"].installed_content_hash == alpha_hash
    assert manifest["alpha"].local_content_hash == alpha_hash
    assert manifest["alpha"].modified is False
    assert manifest["alpha"].update_available is False
    assert manifest["beta"].source_content_hash == beta_hash
    assert manifest["beta"].installed_content_hash == beta_hash
    assert manifest["beta"].local_content_hash == beta_hash
    assert manifest["beta"].modified is False
    assert manifest["beta"].update_available is False


def test_update_project_skills_prepares_changed_replacements_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SV_JOBS", "2")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    source_root = tmp_path / "source"
    project_skills.mkdir(parents=True)
    alpha_started = threading.Event()
    beta_started = threading.Event()
    alpha = BlockingProjectSourceSkill("alpha", source_root, alpha_started, beta_started)
    beta = BlockingProjectSourceSkill("beta", source_root, beta_started, alpha_started)
    add_all_project_skills([alpha, beta], project_skills)
    alpha.started = threading.Event()
    beta.started = threading.Event()
    alpha.peer_started = beta.started
    beta.peer_started = alpha.started
    (alpha.source_path / "notes.md").write_text("alpha update\n")
    (beta.source_path / "notes.md").write_text("beta update\n")
    result = update_project_skills([alpha, beta], project_skills)
    assert result.updated == ["alpha", "beta"]
    assert [skip.reason for skip in result.skipped] == []
    assert (project_skills / "alpha" / "notes.md").read_text() == "alpha update\n"
    assert (project_skills / "beta" / "notes.md").read_text() == "beta update\n"
    manifest = load_manifest(project_skills)
    alpha_hash = sha256_skill_directory(alpha.source_path, expected_name="alpha")
    beta_hash = sha256_skill_directory(beta.source_path, expected_name="beta")
    assert manifest["alpha"].source_content_hash == alpha_hash
    assert manifest["alpha"].installed_content_hash == alpha_hash
    assert manifest["alpha"].local_content_hash == alpha_hash
    assert manifest["alpha"].modified is False
    assert manifest["alpha"].update_available is False
    assert manifest["beta"].source_content_hash == beta_hash
    assert manifest["beta"].installed_content_hash == beta_hash
    assert manifest["beta"].local_content_hash == beta_hash
    assert manifest["beta"].modified is False
    assert manifest["beta"].update_available is False


class FailingPrepareProjectSourceSkill(BlockingProjectSourceSkill):
    def materialize_to(self, destination: Path) -> None:
        self.started.set()
        assert self.peer_started.wait(2), "failing add-all materialization did not overlap"
        raise SvError("simulated materialization failure")


def test_sync_project_skills_cleans_prepared_temps_when_one_prepare_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SV_JOBS", "2")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    source_root = tmp_path / "source"
    alpha_started = threading.Event()
    beta_started = threading.Event()
    alpha = BlockingProjectSourceSkill("alpha", source_root, alpha_started, beta_started)
    beta = BlockingProjectSourceSkill("beta", source_root, beta_started, alpha_started)
    add_all_project_skills([alpha, beta], project_skills)
    alpha.started = threading.Event()
    beta_failing = FailingPrepareProjectSourceSkill.__new__(FailingPrepareProjectSourceSkill)
    beta_failing.__dict__.update(beta.__dict__)
    beta_failing.started = threading.Event()
    beta_failing.peer_started = alpha.started
    beta = beta_failing
    alpha.peer_started = beta.started
    (alpha.source_path / "notes.md").write_text("alpha v2\n")
    (beta.source_path / "notes.md").write_text("beta v2\n")

    with pytest.raises(SvError, match="Failed to sync skill 'beta'"):
        sync_project_skills([alpha, beta], project_skills)

    assert not (project_skills / "alpha" / "notes.md").exists()
    assert not (project_skills / "beta" / "notes.md").exists()
    assert_no_partial_sv_dirs(project_skills)


def test_sync_project_agent_skills_uses_agent_label_when_prepare_fails(
    tmp_path: Path,
) -> None:
    project_skills = tmp_path / "project" / ".claude" / "skills"
    source_skill = make_source_skill(tmp_path / "source", "beta")
    add_project_agent_skill(source_skill, project_skills, "claude")
    failing_source_skill = replace(
        source_skill,
        _materializer=lambda destination: (_ for _ in ()).throw(
            SvError("simulated materialization failure")
        ),
    )

    with pytest.raises(SvError, match="Failed to sync Claude skill 'beta'"):
        sync_project_agent_skills([failing_source_skill], project_skills, "claude")

    assert_no_partial_sv_dirs(project_skills)


def test_update_project_skills_cleans_prepared_temps_when_one_prepare_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SV_JOBS", "2")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    source_root = tmp_path / "source"
    alpha_started = threading.Event()
    beta_started = threading.Event()
    alpha = BlockingProjectSourceSkill("alpha", source_root, alpha_started, beta_started)
    beta = BlockingProjectSourceSkill("beta", source_root, beta_started, alpha_started)
    add_all_project_skills([alpha, beta], project_skills)
    alpha.started = threading.Event()
    beta_failing = FailingPrepareProjectSourceSkill.__new__(FailingPrepareProjectSourceSkill)
    beta_failing.__dict__.update(beta.__dict__)
    beta_failing.started = threading.Event()
    beta_failing.peer_started = alpha.started
    beta = beta_failing
    alpha.peer_started = beta.started
    (alpha.source_path / "notes.md").write_text("alpha update\n")
    (beta.source_path / "notes.md").write_text("beta update\n")

    with pytest.raises(SvError, match="Failed to sync skill 'beta'"):
        update_project_skills([alpha, beta], project_skills)

    assert not (project_skills / "alpha" / "notes.md").exists()
    assert not (project_skills / "beta" / "notes.md").exists()
    assert_no_partial_sv_dirs(project_skills)


def test_sync_project_skills_cleans_prepared_temps_when_serial_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_skills = tmp_path / "project" / ".pi" / "skills"
    alpha = make_source_skill(tmp_path / "source", "alpha")
    beta = make_source_skill(tmp_path / "source", "beta")
    add_all_project_skills([alpha, beta], project_skills)
    (alpha.source_path / "notes.md").write_text("alpha v2\n")
    (beta.source_path / "notes.md").write_text("beta v2\n")

    def fail_replace(target, after_replace, target_style=project_module._PI_TARGET):
        assert (project_skills / ".alpha.sv-sync-tmp").is_dir()
        assert (project_skills / ".beta.sv-sync-tmp").is_dir()
        raise SvError("replace commit failed")

    monkeypatch.setattr(project_module, "_replace_with_materialized_entry", fail_replace)

    with pytest.raises(SvError, match="replace commit failed"):
        sync_project_skills([alpha, beta], project_skills)

    assert (project_skills / "alpha" / "notes.md").read_text() == "alpha remote\n"
    assert (project_skills / "beta" / "notes.md").read_text() == "beta remote\n"
    assert_no_partial_sv_dirs(project_skills)


def test_add_all_project_skills_cleans_prepared_temps_when_one_prepare_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SV_JOBS", "2")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    source_root = tmp_path / "source"
    alpha_started = threading.Event()
    beta_started = threading.Event()
    alpha = BlockingProjectSourceSkill("alpha", source_root, alpha_started, beta_started)
    beta = FailingPrepareProjectSourceSkill("beta", source_root, beta_started, alpha_started)
    with pytest.raises(SvError, match="Failed to add Pi skill 'beta'"):
        add_all_project_skills([alpha, beta], project_skills)
    assert not (project_skills / "alpha").exists()
    assert not (project_skills / "beta").exists()
    assert not list(project_skills.glob(".*.sv-add-tmp"))


def test_add_all_project_skills_cleans_prepared_temps_when_serial_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alpha = make_source_skill(tmp_path / "source", "alpha")
    beta = make_source_skill(tmp_path / "source", "beta")
    project_skills = tmp_path / "project" / ".pi" / "skills"

    def fail_manifest_update(project_skills_dir, entry, target_style):
        raise SvError("manifest write failed")

    monkeypatch.setattr(
        project_module, "_upsert_manifest_entry_for_target", fail_manifest_update
    )

    with pytest.raises(SvError, match="manifest write failed"):
        add_all_project_skills([alpha, beta], project_skills)

    assert not (project_skills / ".alpha.sv-add-tmp").exists()
    assert not (project_skills / ".beta.sv-add-tmp").exists()
    assert_no_partial_sv_dirs(project_skills)


def test_add_all_vault_skills_replaces_existing_targets_with_replace_temp(
    tmp_path: Path,
) -> None:
    alpha = make_source_skill(tmp_path / "source", "alpha")
    vault_skills = tmp_path / "vault" / "skills"
    add_vault_skill(alpha, vault_skills)
    (alpha.source_path / "notes.md").write_text("alpha replacement\n")
    result = add_all_vault_skills([alpha], vault_skills, replace_existing=True)
    assert [(item.skill, item.status) for item in result.results] == [("alpha", "replaced")]
    assert (vault_skills / "alpha" / "notes.md").read_text() == "alpha replacement\n"
    assert_no_partial_sv_dirs(vault_skills)


def test_add_all_vault_skills_replace_existing_keeps_one_plan_for_duplicates(
    tmp_path: Path,
) -> None:
    installed = make_source_skill(
        tmp_path / "installed-source", "alpha", repo_id="Org/Installed"
    )
    first = make_source_skill(tmp_path / "source-a", "alpha", repo_id="Org/A")
    second = make_source_skill(tmp_path / "source-b", "alpha", repo_id="Org/B")
    vault_skills = tmp_path / "vault" / "skills"
    add_vault_skill(installed, vault_skills)
    (first.source_path / "notes.md").write_text("first replacement\n")
    (second.source_path / "notes.md").write_text("second replacement\n")

    result = add_all_vault_skills(
        [first, second], vault_skills, replace_existing=True
    )

    assert [
        (item.skill, item.status, item.repo_id, item.existing_repo_id)
        for item in result.results
    ] == [
        ("alpha", "replaced", "Org/A", None),
        ("alpha", "exists", "Org/B", "Org/A"),
    ]
    assert (vault_skills / "alpha" / "notes.md").read_text() == "first replacement\n"
    assert load_manifest(vault_skills)["alpha"].repo_id == "Org/A"
    assert_no_partial_sv_dirs(vault_skills)


def test_add_all_project_skills_preserves_duplicate_name_result_rows(
    tmp_path: Path,
) -> None:
    first = make_source_skill(tmp_path / "source-a", "alpha", repo_id="Org/A")
    second = make_source_skill(tmp_path / "source-b", "alpha", repo_id="Org/B")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    result = add_all_project_skills([first, second], project_skills)
    assert [(item.skill, item.status, item.repo_id, item.existing_repo_id) for item in result.results] == [
        ("alpha", "added", "Org/A", None),
        ("alpha", "exists", "Org/B", "Org/A"),
    ]


def test_add_all_vault_skills_honors_duplicate_name_replace_indexes(
    tmp_path: Path,
) -> None:
    installed = make_source_skill(tmp_path / "installed-source", "alpha", repo_id="Org/Installed")
    first = make_source_skill(tmp_path / "source-a", "alpha", repo_id="Org/A")
    second = make_source_skill(tmp_path / "source-b", "alpha", repo_id="Org/B")
    vault_skills = tmp_path / "vault" / "skills"
    add_vault_skill(installed, vault_skills)
    (second.source_path / "notes.md").write_text("second replacement\n")
    result = add_all_vault_skills(
        [first, second], vault_skills, replace_existing_indexes=frozenset({1})
    )
    assert [(item.skill, item.status, item.repo_id, item.existing_repo_id) for item in result.results] == [
        ("alpha", "exists", "Org/A", "Org/Installed"),
        ("alpha", "replaced", "Org/B", None),
    ]
    assert (vault_skills / "alpha" / "notes.md").read_text() == "second replacement\n"
    assert_no_partial_sv_dirs(vault_skills)


@pytest.mark.parametrize(
    "skill",
    [
        "",
        "   ",
        ".",
        "..",
        ".alpha",
        "../alpha",
        "alpha/beta",
        r"alpha\\beta",
        "repo:skill",
        "bad\x1bname",
        "-option-like",
        "zero\u200bwidth",
        "rtl\u202eoverride",
    ],
)
def test_normalize_skill_name_rejects_path_like_and_deceptive_skill_names(skill: str):
    with pytest.raises(SvError, match="Invalid skill name"):
        normalize_skill_name(skill)


def test_project_private_source_hash_helpers_prefer_known_values_and_handle_missing(
    tmp_path: Path,
):
    entry = make_source_skill(tmp_path / "source", "alpha")
    known = SourceSkill(
        name=entry.name,
        description=entry.description,
        repo_id=entry.repo_id,
        repo_url=entry.repo_url,
        repo_path=entry.repo_path,
        source_path=entry.source_path,
        source_relative_path=entry.source_relative_path,
        source_content_hash="sha256:c651ccb96b0c0e490de4cc12b9b46d643e6dba87840fab27e2c8d4d5cc2037fa",
        source_skill_file_hash="sha256:1b19abd1bfc5c54a3807a697a0f3b4b26d4c670926b8077aef876e87bab02bdb",
    )
    missing = SourceSkill(
        name="missing",
        description="Missing skill.",
        repo_id=entry.repo_id,
        repo_url=entry.repo_url,
        repo_path=entry.repo_path,
        source_path=tmp_path / "source" / "skills" / "missing",
        source_relative_path="skills/missing",
    )

    assert project_module._known_source_content_hash(None) is None
    assert project_module._known_source_content_hash(known) == "sha256:c651ccb96b0c0e490de4cc12b9b46d643e6dba87840fab27e2c8d4d5cc2037fa"
    assert project_module._known_source_skill_file_hash(None) is None
    assert project_module._known_source_skill_file_hash(known) == "sha256:1b19abd1bfc5c54a3807a697a0f3b4b26d4c670926b8077aef876e87bab02bdb"
    assert project_module._available_source_content_hash(known) == "sha256:c651ccb96b0c0e490de4cc12b9b46d643e6dba87840fab27e2c8d4d5cc2037fa"
    assert project_module._available_source_content_hash(missing) is None


def test_project_mode_repair_helper_handles_optional_repairer(tmp_path: Path):
    destination = tmp_path / "destination"
    repair_calls: list[Path] = []
    entry = make_source_skill(tmp_path / "source", "alpha")
    repairing_entry = replace(
        entry,
        _mode_repairer=lambda path: repair_calls.append(path),
    )

    assert (
        project_module._repair_materialized_modes(
            cast(project_module.ProjectSourceSkill, object()), destination
        )
        is False
    )
    assert project_module._repair_materialized_modes(entry, destination) is False
    assert project_module._repair_materialized_modes(repairing_entry, destination) is True
    assert repair_calls == [destination]


def test_available_source_content_hash_ignores_github_api_source_path_without_known_hash(
    tmp_path: Path,
):
    entry = make_source_skill(tmp_path / "source", "alpha")
    remote_entry = SourceSkill(
        name=entry.name,
        description=entry.description,
        repo_id=entry.repo_id,
        repo_url=entry.repo_url,
        repo_path=entry.repo_path,
        source_path=entry.source_path,
        source_relative_path=entry.source_relative_path,
        source_backend="github-gh-api",
    )

    assert project_module._available_source_content_hash(remote_entry) is None


def test_refreshed_manifest_entry_state_ignores_github_api_source_path_without_known_hash(
    tmp_path: Path,
):
    installed_entry = make_source_skill(tmp_path / "installed-source", "alpha")
    installed_hash = sha256_skill_directory(installed_entry.source_path, expected_name="alpha")
    stale_entry = make_source_skill(tmp_path / "stale-source", "alpha")
    (stale_entry.source_path / "notes.md").write_text("stale remote cache\n")
    stale_hash = sha256_skill_directory(stale_entry.source_path, expected_name="alpha")
    assert stale_hash != installed_hash
    remote_entry = SourceSkill(
        name=stale_entry.name,
        description=stale_entry.description,
        repo_id=stale_entry.repo_id,
        repo_url=stale_entry.repo_url,
        repo_path=stale_entry.repo_path,
        source_path=stale_entry.source_path,
        source_relative_path=stale_entry.source_relative_path,
        source_backend="github-gh-api",
    )
    manifest_entry = ManifestEntry(
        name="alpha",
        repo_id=installed_entry.repo_id,
        repo_url=installed_entry.repo_url,
        source_path=installed_entry.source_relative_path,
        description=installed_entry.description,
        installed_content_hash=installed_hash,
    )

    refreshed = project_module._refreshed_manifest_entry_state(
        manifest_entry, installed_entry.source_path, remote_entry
    )

    assert refreshed.source_content_hash != stale_hash
    assert refreshed.update_available is False


def test_refreshed_manifest_entry_state_ignores_cache_reconstructed_source_path_without_known_hash(
    tmp_path: Path,
):
    installed_entry = make_source_skill(tmp_path / "installed-source", "alpha")
    installed_hash = sha256_skill_directory(installed_entry.source_path, expected_name="alpha")
    stale_entry = make_source_skill(tmp_path / "stale-source", "alpha")
    (stale_entry.source_path / "notes.md").write_text("stale reconstructed cache\n")
    stale_hash = sha256_skill_directory(stale_entry.source_path, expected_name="alpha")
    assert stale_hash != installed_hash
    cached_remote_entry = SourceSkill(
        name=stale_entry.name,
        description=stale_entry.description,
        repo_id=stale_entry.repo_id,
        repo_url=stale_entry.repo_url,
        repo_path=stale_entry.repo_path,
        source_path=stale_entry.source_path,
        source_relative_path=stale_entry.source_relative_path,
        source_backend="cache:github-gh-api",
    )
    manifest_entry = ManifestEntry(
        name="alpha",
        repo_id=installed_entry.repo_id,
        repo_url=installed_entry.repo_url,
        source_path=installed_entry.source_relative_path,
        description=installed_entry.description,
        installed_content_hash=installed_hash,
    )

    refreshed = project_module._refreshed_manifest_entry_state(
        manifest_entry, installed_entry.source_path, cached_remote_entry
    )

    assert refreshed.source_content_hash != stale_hash
    assert refreshed.update_available is False


def test_list_project_skills_returns_sorted_skill_directories(tmp_path: Path):
    project_skills = tmp_path / "project" / ".pi" / "skills"
    (project_skills / "beta").mkdir(parents=True)
    (project_skills / "alpha").mkdir()
    (project_skills / ".sv-sync-tmp").mkdir()
    (project_skills / "README.md").write_text("not a skill directory\n")

    assert list_project_skills(project_skills) == ["alpha", "beta"]


def test_list_project_skills_rejects_control_character_skill_names(tmp_path: Path):
    project_skills = tmp_path / "project" / ".pi" / "skills"
    (project_skills / "bad\x1bname").mkdir(parents=True)

    with pytest.raises(SvError, match="Invalid Pi skill directory"):
        list_project_skills(project_skills)


def test_remove_project_skill_deletes_local_skill_directory(tmp_path: Path):
    project_skills = tmp_path / "project" / ".pi" / "skills"
    skill = project_skills / "alpha"
    skill.mkdir(parents=True)
    (skill / "notes.md").write_text("alpha skill\n")

    result = remove_project_skill("alpha", project_skills)

    assert result.skill == "alpha"
    assert result.target == skill
    assert not skill.exists()


def test_remove_project_skill_deletes_manifest_entry(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    assert add_project_skill(entry, project_skills).status == "added"

    result = remove_project_skill("alpha", project_skills)

    assert result.skill == "alpha"
    assert not (project_skills / "alpha").exists()
    assert load_manifest(project_skills) == {}


def test_remove_project_skill_preserves_other_manifest_entries(tmp_path: Path):
    alpha = make_source_skill(tmp_path / "source", "alpha")
    beta = make_source_skill(tmp_path / "source", "beta")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    add_project_skill(alpha, project_skills)
    add_project_skill(beta, project_skills)

    remove_project_skill("alpha", project_skills)

    manifest = load_manifest(project_skills)
    assert sorted(manifest) == ["beta"]
    assert manifest["beta"].repo_id == "Org/Skills"


def test_remove_project_skill_missing_skill_keeps_manifest(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    save_manifest(project_skills, {"alpha": entry_manifest(entry)})

    with pytest.raises(SvError, match="Pi skill 'alpha' was not found"):
        remove_project_skill("alpha", project_skills)

    assert sorted(load_manifest(project_skills)) == ["alpha"]


def test_remove_project_skill_malformed_manifest_keeps_skill_directory(tmp_path: Path):
    project_skills = tmp_path / "project" / ".pi" / "skills"
    skill = project_skills / "alpha"
    skill.mkdir(parents=True)
    (skill / "notes.md").write_text("alpha\n")
    (project_skills / ".sv-manifest.toml").write_text("skills = [\n")

    with pytest.raises(SvError, match="Failed to read sv manifest"):
        remove_project_skill("alpha", project_skills)

    assert (skill / "notes.md").read_text() == "alpha\n"


def test_remove_project_skill_restores_skill_when_manifest_update_fails(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    assert add_project_skill(entry, project_skills).status == "added"
    skill = project_skills / "alpha"
    original_notes = skill / "notes.md"
    assert original_notes.read_text() == "alpha remote\n"

    def fail_manifest_save(self, entries):
        raise SvError("manifest write failed")

    monkeypatch.setattr("sv.project.ProjectManifestStore.save", fail_manifest_save)

    with pytest.raises(SvError, match="manifest write failed"):
        remove_project_skill("alpha", project_skills)

    assert (skill / "notes.md").read_text() == "alpha remote\n"
    assert load_manifest(project_skills)["alpha"].repo_id == "Org/Skills"


def test_remove_project_skill_cleans_stale_remove_backup_on_success(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    assert add_project_skill(entry, project_skills).status == "added"
    skill = project_skills / "alpha"
    stale_backup = project_skills / ".alpha.sv-remove-backup"
    stale_backup.mkdir(parents=True)
    (stale_backup / "leftover.txt").write_text("leftover\n")

    result = remove_project_skill("alpha", project_skills)

    assert result.skill == "alpha"
    assert result.target == skill
    assert not skill.exists()
    assert not stale_backup.exists()
    assert load_manifest(project_skills) == {}
    assert_no_partial_sv_dirs(project_skills)


def test_remove_project_skill_restores_skill_when_backup_cleanup_fails(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    assert add_project_skill(entry, project_skills).status == "added"
    skill = project_skills / "alpha"
    backup_target = project_skills / ".alpha.sv-remove-backup"
    original_manifest = load_manifest(project_skills)
    original_rmtree = shutil.rmtree

    def fail_remove_backup(path, *args, **kwargs):
        if path == backup_target:
            raise OSError("backup cleanup failed")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", fail_remove_backup)

    with pytest.raises(SvError, match="Failed to remove Pi skill 'alpha'"):
        remove_project_skill("alpha", project_skills)

    assert load_manifest(project_skills) == original_manifest
    assert skill.exists()
    assert (skill / "notes.md").read_text() == "alpha remote\n"
    assert not backup_target.exists()
    assert_no_partial_sv_dirs(project_skills)


def test_remove_project_skill_reports_stale_backup_cleanup_failure(
    tmp_path: Path, monkeypatch
):
    project_skills = tmp_path / "project" / ".pi" / "skills"
    skill = project_skills / "alpha"
    skill.mkdir(parents=True)
    stale_backup = project_skills / ".alpha.sv-remove-backup"
    stale_backup.mkdir()
    original_rmtree = shutil.rmtree

    def fail_stale_backup_cleanup(path, *args, **kwargs):
        if path == stale_backup:
            raise OSError("stale cleanup failed")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", fail_stale_backup_cleanup)

    with pytest.raises(SvError, match="Failed to remove stale sv backup"):
        remove_project_skill("alpha", project_skills)

    assert skill.exists()
    assert stale_backup.exists()


def test_remove_project_skill_reports_rename_failure(tmp_path: Path, monkeypatch):
    project_skills = tmp_path / "project" / ".pi" / "skills"
    skill = project_skills / "alpha"
    skill.mkdir(parents=True)
    original_rename = Path.rename

    def fail_remove_rename(path, target):
        if path == skill:
            raise OSError("rename failed")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_remove_rename)

    with pytest.raises(SvError, match="Failed to remove Pi skill 'alpha'"):
        remove_project_skill("alpha", project_skills)

    assert skill.exists()


def test_remove_project_skill_reports_manifest_failure_when_restore_fails(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    assert add_project_skill(entry, project_skills).status == "added"
    skill = project_skills / "alpha"
    backup_target = project_skills / ".alpha.sv-remove-backup"
    original_rename = Path.rename

    def fail_manifest_save(self, entries):
        raise SvError("manifest write failed")

    def fail_restore_rename(path, target):
        if path == backup_target and target == skill:
            raise OSError("restore failed")
        return original_rename(path, target)

    monkeypatch.setattr("sv.project.ProjectManifestStore.save", fail_manifest_save)
    monkeypatch.setattr(Path, "rename", fail_restore_rename)

    with pytest.raises(SvError, match="rollback failed"):
        remove_project_skill("alpha", project_skills)

    assert backup_target.exists()
    assert not skill.exists()


def test_remove_project_skill_reports_cleanup_failure_when_rollback_fails(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    assert add_project_skill(entry, project_skills).status == "added"
    skill = project_skills / "alpha"
    backup_target = project_skills / ".alpha.sv-remove-backup"
    original_rmtree = shutil.rmtree
    original_rename = Path.rename

    def fail_remove_backup(path, *args, **kwargs):
        if path == backup_target:
            raise OSError("backup cleanup failed")
        return original_rmtree(path, *args, **kwargs)

    def fail_restore_rename(path, target):
        if path == backup_target and target == skill:
            raise OSError("restore failed")
        return original_rename(path, target)

    monkeypatch.setattr(shutil, "rmtree", fail_remove_backup)
    monkeypatch.setattr(Path, "rename", fail_restore_rename)

    with pytest.raises(SvError, match="cleanup failed .* rollback failed"):
        remove_project_skill("alpha", project_skills)

    assert backup_target.exists()
    assert not skill.exists()


def test_remove_project_skill_missing_skill_raises_error(tmp_path: Path):
    with pytest.raises(SvError, match="Pi skill 'missing' was not found"):
        remove_project_skill("missing", tmp_path / "project" / ".pi" / "skills")


def test_remove_project_skill_rejects_symlinked_local_skill(tmp_path: Path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support is required")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    outside = tmp_path / "outside-alpha"
    outside.mkdir(parents=True)
    (outside / "notes.md").write_text("outside\n")
    project_skills.mkdir(parents=True)
    os.symlink(outside, project_skills / "alpha")

    with pytest.raises(SvError, match="Refusing to manage symlinked Pi skill"):
        remove_project_skill("alpha", project_skills)

    assert (outside / "notes.md").read_text() == "outside\n"


def test_remove_project_skill_rejects_symlinked_pi_dir(tmp_path: Path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support is required")
    project = tmp_path / "project"
    project.mkdir()
    outside_pi = tmp_path / "outside-pi"
    outside_skill = outside_pi / "skills" / "alpha"
    outside_skill.mkdir(parents=True)
    (outside_skill / "notes.md").write_text("outside\n")
    os.symlink(outside_pi, project / ".pi")

    with pytest.raises(SvError, match="Refusing to use symlinked Pi skills path"):
        remove_project_skill("alpha", project / ".pi" / "skills")

    assert (outside_skill / "notes.md").read_text() == "outside\n"


def test_update_project_skills_preserves_local_edits_and_marks_state(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    add_project_skill(entry, project_skills)
    local_skill = project_skills / "managed"
    (local_skill / "notes.md").write_text("managed local edit\n")
    (entry.source_path / "notes.md").write_text("managed remote v2\n")

    result = update_project_skills([entry], project_skills)

    assert result.updated == []
    assert [(skip.skill, skip.reason) for skip in result.skipped] == [
        ("managed", "modified")
    ]
    assert (local_skill / "notes.md").read_text() == "managed local edit\n"
    manifest_entry = load_manifest(project_skills)["managed"]
    assert manifest_entry.modified is True
    assert manifest_entry.update_available is True


def test_update_project_skills_replaces_unmodified_skill_when_source_changed(
    tmp_path: Path,
):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    add_project_skill(entry, project_skills)
    local_skill = project_skills / "managed"
    (entry.source_path / "notes.md").write_text("managed remote v2\n")

    result = update_project_skills([entry], project_skills)

    assert result.updated == ["managed"]
    assert result.skipped == []
    assert (local_skill / "notes.md").read_text() == "managed remote v2\n"
    manifest_entry = load_manifest(project_skills)["managed"]
    assert manifest_entry.modified is False
    assert manifest_entry.update_available is False


def test_update_project_skills_updates_legacy_entry_with_recorded_source_hash(
    tmp_path: Path,
):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    add_project_skill(entry, project_skills)
    local_skill = project_skills / "managed"
    original_hash = load_manifest(project_skills)["managed"].installed_content_hash
    save_manifest(
        project_skills,
        {
            "managed": ManifestEntry(
                name="managed",
                repo_id=entry.repo_id,
                repo_url=entry.repo_url,
                source_path=entry.source_relative_path,
                description=entry.description,
                source_content_hash=original_hash,
            )
        },
    )
    (entry.source_path / "notes.md").write_text("managed remote v2\n")

    result = update_project_skills([entry], project_skills)

    assert result.updated == ["managed"]
    assert result.skipped == []
    assert (local_skill / "notes.md").read_text() == "managed remote v2\n"
    manifest_entry = load_manifest(project_skills)["managed"]
    assert manifest_entry.installed_content_hash == sha256_skill_directory(
        local_skill, expected_name="managed"
    )
    assert manifest_entry.modified is False


def test_update_project_skills_backfills_hashless_entry_matching_current_source(
    tmp_path: Path,
):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    add_project_skill(entry, project_skills)
    local_skill = project_skills / "managed"
    save_manifest(project_skills, {"managed": entry_manifest(entry)})

    result = update_project_skills([entry], project_skills)

    assert result.updated == []
    assert [(skip.skill, skip.reason) for skip in result.skipped] == [
        ("managed", "unchanged")
    ]
    local_hash = sha256_skill_directory(local_skill, expected_name="managed")
    manifest_entry = load_manifest(project_skills)["managed"]
    assert manifest_entry.source_content_hash == local_hash
    assert manifest_entry.installed_content_hash == local_hash
    assert manifest_entry.local_content_hash == local_hash
    assert manifest_entry.modified is False


def test_update_project_skills_skips_hash_unchanged_source_without_materializing(
    tmp_path: Path,
):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    add_project_skill(entry, project_skills)
    installed_hash = load_manifest(project_skills)["managed"].installed_content_hash

    def fail_materialize(destination: Path) -> None:
        raise AssertionError("unchanged source should not be materialized")

    indexed_entry = SourceSkill(
        name="managed",
        description="Managed skill.",
        repo_id=entry.repo_id,
        repo_url=entry.repo_url,
        repo_path=tmp_path / "missing-source-cache",
        source_path=tmp_path / "missing-source-cache" / "skills" / "managed",
        source_relative_path="skills/managed",
        source_backend="fake-index",
        source_content_hash=installed_hash,
        _materializer=fail_materialize,
    )

    result = update_project_skills([indexed_entry], project_skills)

    assert result.updated == []
    assert [(skip.skill, skip.reason) for skip in result.skipped] == [
        ("managed", "unchanged")
    ]
    assert (project_skills / "managed" / "notes.md").read_text() == "managed remote\n"


def test_update_project_skills_keeps_unhashed_materialized_source_unchanged(
    tmp_path: Path,
) -> None:
    source_entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    add_project_skill(source_entry, project_skills)

    def materialize(destination: Path) -> None:
        shutil.copytree(source_entry.source_path, destination)

    remote_entry = SourceSkill(
        name="managed",
        description="Managed skill.",
        repo_id=source_entry.repo_id,
        repo_url=source_entry.repo_url,
        repo_path=tmp_path / "missing-source-cache",
        source_path=tmp_path / "missing-source-cache" / "skills" / "managed",
        source_relative_path="skills/managed",
        source_backend="fake-remote",
        _materializer=materialize,
    )

    result = update_project_skills([remote_entry], project_skills)
    assert result.updated == []
    assert [(skip.skill, skip.reason) for skip in result.skipped] == [
        ("managed", "unchanged")
    ]
    assert_no_partial_sv_dirs(project_skills)


def test_sync_project_skills_updates_manifest_tracked_origin(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    assert add_project_skill(entry, project_skills).status == "added"
    (entry.source_path / "notes.md").write_text("managed remote v2\n")

    result = sync_project_skills([entry], project_skills)

    assert result.no_skills_dir is False
    assert result.updated == ["managed"]
    assert result.backfilled == []
    assert result.skipped == []
    assert (project_skills / "managed" / "notes.md").read_text() == (
        "managed remote v2\n"
    )
    assert load_manifest(project_skills)["managed"].repo_id == "Org/Skills"


def test_sync_project_skills_updates_hashes_after_successful_replacement(
    tmp_path: Path,
):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    add_project_skill(entry, project_skills)
    local_skill = project_skills / "managed"
    (local_skill / "notes.md").write_text("managed local edit\n")
    project_module.refresh_project_skill_states([entry], project_skills)
    assert load_manifest(project_skills)["managed"].modified is True
    (entry.source_path / "notes.md").write_text("managed remote v2\n")

    sync_project_skills([entry], project_skills)

    replaced_hash = sha256_skill_directory(local_skill, expected_name="managed")
    manifest_entry = load_manifest(project_skills)["managed"]
    assert (local_skill / "notes.md").read_text() == "managed remote v2\n"
    assert manifest_entry.source_content_hash == replaced_hash
    assert manifest_entry.installed_content_hash == replaced_hash
    assert manifest_entry.local_content_hash == replaced_hash
    assert manifest_entry.modified is False
    assert manifest_entry.update_available is False


def test_sync_project_skills_updates_manifest_tracked_origin_from_repo_alias(
    tmp_path: Path,
):
    entry = SourceSkill(
        name="managed",
        description="Managed skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=tmp_path / "source",
        source_path=tmp_path / "source" / "skills" / "managed",
        repo_aliases=("Mirror/Skills",),
    )
    entry.source_path.mkdir(parents=True)
    (entry.source_path / "SKILL.md").write_text(
        "---\nname: managed\ndescription: Managed skill.\n---\n"
    )
    (entry.source_path / "notes.md").write_text("managed remote\n")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    local = project_skills / "managed"
    local.mkdir(parents=True)
    (local / "notes.md").write_text("managed local\n")
    save_manifest(
        project_skills,
        {
            "managed": ManifestEntry(
                name="managed",
                repo_id="Mirror/Skills",
                repo_url="https://github.com/Org/Skills.git",
                source_path="skills/managed",
                description="Managed skill.",
            )
        },
    )

    result = sync_project_skills([entry], project_skills)

    assert result.updated == ["managed"]
    assert result.skipped == []
    assert (local / "notes.md").read_text() == "managed remote\n"
    assert load_manifest(project_skills)["managed"].repo_id == "Org/Skills"


def test_sync_project_skills_treats_missing_recorded_source_path_as_missing(
    tmp_path: Path,
):
    source_root = tmp_path / "source"
    source_path = source_root / "team" / "skills" / "managed"
    source_path.mkdir(parents=True)
    (source_path / "notes.md").write_text("managed remote from team\n")
    entry = SourceSkill(
        name="managed",
        description="Managed skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=source_root,
        source_path=source_path,
        source_relative_path="team/skills/managed",
    )
    project_skills = tmp_path / "project" / ".pi" / "skills"
    local = project_skills / "managed"
    local.mkdir(parents=True)
    (local / "notes.md").write_text("managed local\n")
    save_manifest(
        project_skills,
        {
            "managed": ManifestEntry(
                name="managed",
                repo_id="Org/Skills",
                repo_url="https://github.com/Org/Skills.git",
                source_path="skills/managed",
                description="Managed skill.",
            )
        },
    )

    result = sync_project_skills([entry], project_skills)

    assert result.updated == []
    assert [(skip.skill, skip.reason, skip.repo_ids) for skip in result.skipped] == [
        ("managed", "source-missing", ("Org/Skills",))
    ]
    assert (local / "notes.md").read_text() == "managed local\n"


def test_sync_project_skills_skips_recorded_origin_when_repo_id_url_changes(
    tmp_path: Path,
):
    entry = SourceSkill(
        name="managed",
        description="Managed skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/NewSkills.git",
        repo_path=tmp_path / "source",
        source_path=tmp_path / "source" / "skills" / "managed",
    )
    entry.source_path.mkdir(parents=True)
    (entry.source_path / "notes.md").write_text("managed remote\n")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    local = project_skills / "managed"
    local.mkdir(parents=True)
    (local / "notes.md").write_text("managed local\n")
    save_manifest(
        project_skills,
        {
            "managed": ManifestEntry(
                name="managed",
                repo_id="Org/Skills",
                repo_url="https://github.com/Org/OldSkills.git",
                source_path="skills/managed",
                description="Managed skill.",
            )
        },
    )

    result = sync_project_skills([entry], project_skills)

    assert result.updated == []
    assert [(skip.skill, skip.reason, skip.repo_ids) for skip in result.skipped] == [
        ("managed", "source-missing", ("Org/Skills",))
    ]
    assert (local / "notes.md").read_text() == "managed local\n"


def test_sync_project_skills_skips_invalid_recorded_repo_url(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    local = project_skills / "managed"
    local.mkdir(parents=True)
    (local / "notes.md").write_text("managed local\n")
    save_manifest(
        project_skills,
        {
            "managed": ManifestEntry(
                name="managed",
                repo_id="Org/Skills",
                repo_url="https://github.com/Org/Skills.git\x1b[2J",
                source_path="skills/managed",
                description="Managed skill.",
            )
        },
    )

    result = sync_project_skills([entry], project_skills)

    assert result.updated == []
    assert [(skip.skill, skip.reason, skip.repo_ids) for skip in result.skipped] == [
        ("managed", "source-missing", ("Org/Skills",))
    ]
    assert (local / "notes.md").read_text() == "managed local\n"


def test_sync_project_skills_updates_manifest_tracked_origin_from_repo_url_fallback(
    tmp_path: Path,
):
    entry = SourceSkill(
        name="managed",
        description="Managed skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=tmp_path / "source",
        source_path=tmp_path / "source" / "skills" / "managed",
    )
    entry.source_path.mkdir(parents=True)
    (entry.source_path / "SKILL.md").write_text(
        "---\nname: managed\ndescription: Managed skill.\n---\n"
    )
    (entry.source_path / "notes.md").write_text("managed remote\n")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    local = project_skills / "managed"
    local.mkdir(parents=True)
    (local / "notes.md").write_text("managed local\n")
    save_manifest(
        project_skills,
        {
            "managed": ManifestEntry(
                name="managed",
                repo_id="Mirror/Skills",
                repo_url="git@github.com:Org/Skills.git",
                source_path="skills/managed",
                description="Managed skill.",
            )
        },
    )

    result = sync_project_skills([entry], project_skills)

    assert result.updated == ["managed"]
    assert result.skipped == []
    assert (local / "notes.md").read_text() == "managed remote\n"
    assert load_manifest(project_skills)["managed"].repo_id == "Org/Skills"


def test_sync_project_skills_keeps_unique_untracked_skill_unmanaged(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "manual")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    manual = project_skills / "manual"
    manual.mkdir(parents=True)
    (manual / "notes.md").write_text("manual local\n")

    result = sync_project_skills([entry], project_skills)

    assert result.updated == []
    assert result.backfilled == []
    assert [(skip.skill, skip.reason, skip.repo_ids) for skip in result.skipped] == [
        ("manual", "local-only", ())
    ]
    assert (manual / "notes.md").read_text() == "manual local\n"
    assert load_manifest(project_skills) == {}


def test_sync_project_skills_skips_ambiguous_untracked_skill(tmp_path: Path):
    first = make_source_skill(tmp_path / "source-a", "shared", repo_id="Org/A")
    second = make_source_skill(tmp_path / "source-b", "shared", repo_id="Org/B")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    local = project_skills / "shared"
    local.mkdir(parents=True)
    (local / "notes.md").write_text("keep local\n")

    result = sync_project_skills([first, second], project_skills)

    assert result.updated == []
    assert result.backfilled == []
    assert [(skip.skill, skip.reason, skip.repo_ids) for skip in result.skipped] == [
        ("shared", "ambiguous", ("Org/A", "Org/B"))
    ]
    assert (local / "notes.md").read_text() == "keep local\n"
    assert load_manifest(project_skills) == {}


def test_sync_project_skills_reports_ambiguous_same_repo_paths(tmp_path: Path):
    source_root = tmp_path / "source"
    first_path = source_root / "team-a" / "skills" / "shared"
    second_path = source_root / "team-b" / "skills" / "shared"
    first_path.mkdir(parents=True)
    second_path.mkdir(parents=True)
    first = SourceSkill(
        name="shared",
        description="Shared skill A.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=source_root,
        source_path=first_path,
        source_relative_path="team-a/skills/shared",
    )
    second = SourceSkill(
        name="shared",
        description="Shared skill B.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=source_root,
        source_path=second_path,
        source_relative_path="team-b/skills/shared",
    )
    project_skills = tmp_path / "project" / ".pi" / "skills"
    local = project_skills / "shared"
    local.mkdir(parents=True)
    (local / "notes.md").write_text("local shared\n")

    result = sync_project_skills([first, second], project_skills)

    assert result.updated == []
    assert [
        (skip.skill, skip.reason, skip.source_references) for skip in result.skipped
    ] == [
        (
            "shared",
            "ambiguous",
            (
                "Org/Skills:team-a/skills/shared",
                "Org/Skills:team-b/skills/shared",
            ),
        )
    ]


def test_sync_project_skills_cleans_temp_and_backup_dirs_after_success(
    tmp_path: Path,
):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    managed_local = project_skills / "managed"
    managed_local.mkdir(parents=True)
    (managed_local / "notes.md").write_text("managed local v1\n")
    (entry.source_path / "notes.md").write_text("managed remote v2\n")
    save_manifest(
        project_skills,
        {
            "managed": ManifestEntry(
                name="managed",
                repo_id="Org/Skills",
                repo_url="https://github.com/Org/Skills.git",
                source_path="skills/managed",
                description="Managed skill.",
            )
        },
    )
    stale_temp = project_skills / ".managed.sv-sync-tmp"
    stale_backup = project_skills / ".managed.sv-sync-backup"
    stale_temp.mkdir(parents=True)
    stale_backup.mkdir(parents=True)
    (stale_temp / "leftover.txt").write_text("temp\n")
    (stale_backup / "leftover.txt").write_text("backup\n")

    result = sync_project_skills([entry], project_skills)

    assert result.updated == ["managed"]
    assert result.backfilled == []
    assert result.skipped == []
    assert (managed_local / "notes.md").read_text() == "managed remote v2\n"
    assert load_manifest(project_skills)["managed"].repo_id == "Org/Skills"
    assert not stale_temp.exists()
    assert not stale_backup.exists()
    assert_no_partial_sv_dirs(project_skills)


def test_sync_project_skills_updates_managed_skill_before_skipping_local_only_skill(
    tmp_path: Path,
):
    source_entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    managed_local = project_skills / "managed"
    managed_local.mkdir(parents=True)
    (managed_local / "notes.md").write_text("managed local v1\n")
    local_only = project_skills / "local_only"
    local_only.mkdir(parents=True)
    (local_only / "notes.md").write_text("kept local\n")
    save_manifest(
        project_skills,
        {
            "managed": ManifestEntry(
                name="managed",
                repo_id="Org/Skills",
                repo_url="https://github.com/Org/Skills.git",
                source_path="skills/managed",
                description="Managed skill.",
            )
        },
    )
    (source_entry.source_path / "notes.md").write_text("managed remote v2\n")

    result = sync_project_skills([source_entry], project_skills)

    assert result.updated == ["managed"]
    assert result.backfilled == []
    assert [(skip.skill, skip.reason, skip.repo_ids) for skip in result.skipped] == [
        ("local_only", "local-only", ())
    ]
    assert (managed_local / "notes.md").read_text() == "managed remote v2\n"
    assert (local_only / "notes.md").read_text() == "kept local\n"
    assert load_manifest(project_skills)["managed"].repo_id == "Org/Skills"
    assert_no_partial_sv_dirs(project_skills)


def test_sync_project_skills_preserves_local_skill_when_copy_fails(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    managed_local = project_skills / "managed"
    managed_local.mkdir(parents=True)
    (managed_local / "notes.md").write_text("local v1\n")
    save_manifest(project_skills, {"managed": entry_manifest(entry)})

    def fail_copytree(source, target):
        raise OSError("copy failed")

    monkeypatch.setattr(shutil, "copytree", fail_copytree)

    with pytest.raises(SvError, match="Failed to sync skill 'managed'"):
        sync_project_skills([entry], project_skills)

    assert (managed_local / "notes.md").read_text() == "local v1\n"
    assert_no_partial_sv_dirs(project_skills)


def test_sync_project_skills_restores_local_skill_when_replace_fails(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    managed_local = project_skills / "managed"
    managed_local.mkdir(parents=True)
    (managed_local / "notes.md").write_text("local v1\n")
    save_manifest(project_skills, {"managed": entry_manifest(entry)})
    original_rename = Path.rename

    def fail_temp_rename(path, target):
        if path.name == ".managed.sv-sync-tmp":
            raise OSError("rename failed")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_temp_rename)

    with pytest.raises(SvError, match="Failed to sync skill 'managed'"):
        sync_project_skills([entry], project_skills)

    assert (managed_local / "notes.md").read_text() == "local v1\n"
    assert_no_partial_sv_dirs(project_skills)


def test_sync_project_skills_restores_local_skill_when_manifest_update_fails(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    managed_local = project_skills / "managed"
    managed_local.mkdir(parents=True)
    (managed_local / "notes.md").write_text("local v1\n")
    save_manifest(project_skills, {"managed": entry_manifest(entry)})

    def fail_manifest_save(self, entries):
        raise SvError("manifest write failed")

    monkeypatch.setattr("sv.project.ProjectManifestStore.save", fail_manifest_save)

    with pytest.raises(SvError, match="manifest write failed"):
        sync_project_skills([entry], project_skills)

    assert (managed_local / "notes.md").read_text() == "local v1\n"
    assert load_manifest(project_skills) == {"managed": entry_manifest(entry)}
    assert_no_partial_sv_dirs(project_skills)


def test_sync_project_skills_rejects_symlinked_project_skills_dir(tmp_path: Path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support is required")
    entry = make_source_skill(tmp_path / "source", "alpha")
    project = tmp_path / "project"
    outside = tmp_path / "outside-skills"
    outside_alpha = outside / "alpha"
    outside_alpha.mkdir(parents=True)
    (outside_alpha / "notes.md").write_text("outside\n")
    (project / ".pi").mkdir(parents=True)
    os.symlink(outside, project / ".pi" / "skills")

    with pytest.raises(SvError, match="Refusing to use symlinked Pi skills path"):
        sync_project_skills([entry], project / ".pi" / "skills")

    assert (outside_alpha / "notes.md").read_text() == "outside\n"


def test_sync_project_skills_rejects_control_character_skill_names(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    (project_skills / "bad\x1bname").mkdir(parents=True)

    with pytest.raises(SvError, match="Invalid Pi skill directory"):
        sync_project_skills([entry], project_skills)


def test_sync_project_skills_reports_no_skills_dir(tmp_path: Path):
    result = sync_project_skills([], tmp_path / "project" / ".pi" / "skills")

    assert result.no_skills_dir is True
    assert result.updated == []
    assert result.skipped == []
    assert result.backfilled == []


def test_sync_project_skills_ignores_non_directory_project_entries(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    (project_skills / "README.md").write_text("not a skill\n")

    result = sync_project_skills([entry], project_skills)

    assert result.updated == []
    assert result.skipped == []
    assert result.backfilled == []


def test_sync_project_skills_reports_manifest_failure_when_restore_fails(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    managed_local = project_skills / "managed"
    managed_local.mkdir(parents=True)
    (managed_local / "notes.md").write_text("local v1\n")
    save_manifest(project_skills, {"managed": entry_manifest(entry)})
    backup_target = project_skills / ".managed.sv-sync-backup"
    original_rename = Path.rename

    def fail_manifest_save(self, entries):
        raise SvError("manifest write failed")

    def fail_restore_rename(path, target):
        if path == backup_target and target == managed_local:
            raise OSError("restore failed")
        return original_rename(path, target)

    monkeypatch.setattr("sv.project.ProjectManifestStore.save", fail_manifest_save)
    monkeypatch.setattr(Path, "rename", fail_restore_rename)

    with pytest.raises(SvError, match="manifest update failed .* rollback failed"):
        sync_project_skills([entry], project_skills)

    assert backup_target.exists()
    assert not managed_local.exists()


def test_sync_project_skills_restores_backup_when_os_error_happens_after_backup(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    managed_local = project_skills / "managed"
    managed_local.mkdir(parents=True)
    (managed_local / "notes.md").write_text("local v1\n")
    save_manifest(project_skills, {"managed": entry_manifest(entry)})
    temp_target = project_skills / ".managed.sv-sync-tmp"
    original_rename = Path.rename

    def fail_temp_rename(path, target):
        if path == temp_target and target == managed_local:
            managed_local.mkdir(parents=True)
            (managed_local / "partial.txt").write_text("partial\n")
            raise OSError("replace failed")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_temp_rename)

    with pytest.raises(SvError, match="Failed to sync skill 'managed'"):
        sync_project_skills([entry], project_skills)

    assert (managed_local / "notes.md").read_text() == "local v1\n"
    assert not (managed_local / "partial.txt").exists()
    assert_no_partial_sv_dirs(project_skills)


def test_sync_project_skills_reports_outer_backup_restore_failure(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    managed_local = project_skills / "managed"
    managed_local.mkdir(parents=True)
    (managed_local / "notes.md").write_text("local v1\n")
    save_manifest(project_skills, {"managed": entry_manifest(entry)})
    temp_target = project_skills / ".managed.sv-sync-tmp"
    backup_target = project_skills / ".managed.sv-sync-backup"
    original_rename = Path.rename
    original_rmtree = shutil.rmtree

    def fail_temp_and_backup_restore(path, target):
        if path == temp_target and target == managed_local:
            managed_local.mkdir(parents=True)
            (managed_local / "partial.txt").write_text("partial\n")
            raise OSError("replace failed")
        if path == backup_target and target == managed_local:
            raise OSError("restore failed")
        return original_rename(path, target)

    def fail_after_removing_partial_target(path, *args, **kwargs):
        if path == managed_local:
            original_rmtree(path, ignore_errors=kwargs.get("ignore_errors", False))
            raise OSError("target cleanup failed")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(Path, "rename", fail_temp_and_backup_restore)
    monkeypatch.setattr(shutil, "rmtree", fail_after_removing_partial_target)

    with pytest.raises(
        SvError, match="target cleanup failed.*rollback failed: restore failed"
    ):
        sync_project_skills([entry], project_skills)

    assert backup_target.exists()
    assert not managed_local.exists()


def test_sync_project_skills_reports_backup_restore_failure_after_os_error(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    managed_local = project_skills / "managed"
    managed_local.mkdir(parents=True)
    (managed_local / "notes.md").write_text("local v1\n")
    save_manifest(project_skills, {"managed": entry_manifest(entry)})
    temp_target = project_skills / ".managed.sv-sync-tmp"
    backup_target = project_skills / ".managed.sv-sync-backup"
    original_rename = Path.rename

    def fail_temp_and_backup_restore(path, target):
        if path == temp_target and target == managed_local:
            raise OSError("replace failed")
        if path == backup_target and target == managed_local:
            raise OSError("restore failed")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_temp_and_backup_restore)

    with pytest.raises(SvError, match="replace failed.*rollback failed: restore failed"):
        sync_project_skills([entry], project_skills)

    assert backup_target.exists()
    assert not managed_local.exists()

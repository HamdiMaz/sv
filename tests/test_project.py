from pathlib import Path
import os
import shutil

import pytest

from sv.agents import PiAdapter
from sv.catalog import SourceSkill
from sv.errors import SvError
from sv.manifest import ManifestEntry, load_manifest, save_manifest
from sv.project import (
    add_all_project_skills,
    add_project_skill,
    list_project_skills,
    normalize_skill_name,
    remove_project_skill,
    sync_project_skills,
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


def test_add_project_skill_wraps_copy_failures_and_cleans_temp(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"

    def fail_copytree(source, target):
        target.mkdir(parents=True)
        (target / "partial.txt").write_text("partial\n")
        raise OSError("copy failed")

    monkeypatch.setattr(shutil, "copytree", fail_copytree)

    with pytest.raises(SvError, match="Failed to add Pi skill 'alpha'"):
        add_project_skill(entry, project_skills)

    assert not (project_skills / "alpha").exists()
    assert not (project_skills / ".alpha.sv-add-tmp").exists()
    assert load_manifest(project_skills) == {}


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

    def fail_upsert(project_skills_dir, manifest_entry):
        raise SvError("manifest write failed")

    monkeypatch.setattr("sv.project.upsert_manifest_entry", fail_upsert)

    with pytest.raises(SvError, match="manifest write failed"):
        add_project_skill(entry, project_skills)

    assert not (project_skills / "alpha").exists()


def test_add_project_skill_reports_manifest_failure_rollback_failure(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    original_rmtree = shutil.rmtree

    def fail_upsert(project_skills_dir, manifest_entry):
        raise SvError("manifest write failed")

    def fail_rmtree(path, *args, **kwargs):
        if path.name == "alpha":
            raise OSError("rollback failed")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("sv.project.upsert_manifest_entry", fail_upsert)
    monkeypatch.setattr(shutil, "rmtree", fail_rmtree)

    with pytest.raises(SvError, match="rollback failed"):
        add_project_skill(entry, project_skills)

    assert (project_skills / "alpha").exists()


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
    ],
)
def test_normalize_skill_name_rejects_path_like_skill_names(skill: str):
    with pytest.raises(SvError, match="Invalid skill name"):
        normalize_skill_name(skill)


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

    def fail_remove_manifest_entry(project_skills_dir, skill_name):
        raise SvError("manifest write failed")

    monkeypatch.setattr("sv.project.remove_manifest_entry", fail_remove_manifest_entry)

    with pytest.raises(SvError, match="manifest write failed"):
        remove_project_skill("alpha", project_skills)

    assert (skill / "notes.md").read_text() == "alpha remote\n"
    assert load_manifest(project_skills)["alpha"].repo_id == "Org/Skills"


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


def test_sync_project_skills_backfills_unique_untracked_skill(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "legacy")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    legacy = project_skills / "legacy"
    legacy.mkdir(parents=True)
    (legacy / "notes.md").write_text("legacy local\n")

    result = sync_project_skills([entry], project_skills)

    assert result.updated == ["legacy"]
    assert result.backfilled == ["legacy"]
    assert (legacy / "notes.md").read_text() == "legacy remote\n"
    assert load_manifest(project_skills)["legacy"].repo_id == "Org/Skills"


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


def test_sync_project_skills_preserves_local_skill_when_copy_fails(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    managed_local = project_skills / "managed"
    managed_local.mkdir(parents=True)
    (managed_local / "notes.md").write_text("local v1\n")

    def fail_copytree(source, target):
        raise OSError("copy failed")

    monkeypatch.setattr(shutil, "copytree", fail_copytree)

    with pytest.raises(SvError, match="Failed to sync skill 'managed'"):
        sync_project_skills([entry], project_skills)

    assert (managed_local / "notes.md").read_text() == "local v1\n"


def test_sync_project_skills_restores_local_skill_when_replace_fails(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    managed_local = project_skills / "managed"
    managed_local.mkdir(parents=True)
    (managed_local / "notes.md").write_text("local v1\n")
    original_rename = Path.rename

    def fail_temp_rename(path, target):
        if path.name == ".managed.sv-sync-tmp":
            raise OSError("rename failed")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_temp_rename)

    with pytest.raises(SvError, match="Failed to sync skill 'managed'"):
        sync_project_skills([entry], project_skills)

    assert (managed_local / "notes.md").read_text() == "local v1\n"


def test_sync_project_skills_restores_local_skill_when_manifest_update_fails(
    tmp_path: Path, monkeypatch
):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    managed_local = project_skills / "managed"
    managed_local.mkdir(parents=True)
    (managed_local / "notes.md").write_text("local v1\n")

    def fail_upsert(project_skills_dir, manifest_entry):
        raise SvError("manifest write failed")

    monkeypatch.setattr("sv.project.upsert_manifest_entry", fail_upsert)

    with pytest.raises(SvError, match="manifest write failed"):
        sync_project_skills([entry], project_skills)

    assert (managed_local / "notes.md").read_text() == "local v1\n"
    assert load_manifest(project_skills) == {}


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

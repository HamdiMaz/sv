from pathlib import Path
import shutil

import pytest

from sv.agents import PiAdapter
from sv.errors import SvError
from sv.project import (
    add_all_project_skills,
    add_project_skill,
    list_project_skills,
    remove_project_skill,
    sync_project_skills,
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


def test_add_project_skill_creates_pi_skills_and_copies_folder(tmp_path: Path):
    source_repo = tmp_path / "source"
    source_skill = source_repo / "skills" / "alpha"
    source_skill.mkdir(parents=True)
    (source_skill / "notes.md").write_text("alpha skill\n")
    project_skills = tmp_path / "project" / ".pi" / "skills"

    result = add_project_skill("alpha", source_repo, project_skills)

    assert result.status == "added"
    assert result.skill == "alpha"
    assert result.target == project_skills / "alpha"
    assert (project_skills / "alpha" / "notes.md").read_text() == "alpha skill\n"


def test_add_project_skill_existing_skill_is_success_without_overwrite(tmp_path: Path):
    source_repo = tmp_path / "source"
    source_skill = source_repo / "skills" / "alpha"
    source_skill.mkdir(parents=True)
    (source_skill / "notes.md").write_text("remote version\n")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    existing = project_skills / "alpha"
    existing.mkdir(parents=True)
    (existing / "notes.md").write_text("local version\n")

    result = add_project_skill("alpha", source_repo, project_skills)

    assert result.status == "exists"
    assert (existing / "notes.md").read_text() == "local version\n"


def test_add_project_skill_missing_source_skill_raises_error(tmp_path: Path):
    with pytest.raises(SvError, match="Skill 'missing' was not found"):
        add_project_skill(
            "missing", tmp_path / "source", tmp_path / "project" / ".pi" / "skills"
        )


def test_add_all_project_skills_copies_all_source_skills(tmp_path: Path):
    source_repo = tmp_path / "source"
    for skill in ["beta", "alpha"]:
        skill_dir = source_repo / "skills" / skill
        skill_dir.mkdir(parents=True)
        (skill_dir / "notes.md").write_text(f"{skill} skill\n")

    project_skills = tmp_path / "project" / ".pi" / "skills"
    existing = project_skills / "beta"
    existing.mkdir(parents=True)
    (existing / "notes.md").write_text("local beta\n")

    result = add_all_project_skills(source_repo, project_skills)

    assert [(item.skill, item.status) for item in result.results] == [
        ("alpha", "added"),
        ("beta", "exists"),
    ]
    assert (project_skills / "alpha" / "notes.md").read_text() == "alpha skill\n"
    assert (project_skills / "beta" / "notes.md").read_text() == "local beta\n"


@pytest.mark.parametrize(
    "skill", ["", "   ", ".", "..", "../alpha", "alpha/beta", r"alpha\\beta"]
)
def test_add_project_skill_rejects_path_like_skill_names(skill: str, tmp_path: Path):
    with pytest.raises(SvError, match="Invalid skill name"):
        add_project_skill(
            skill, tmp_path / "source", tmp_path / "project" / ".pi" / "skills"
        )


def test_list_project_skills_returns_sorted_skill_directories(tmp_path: Path):
    project_skills = tmp_path / "project" / ".pi" / "skills"
    (project_skills / "beta").mkdir(parents=True)
    (project_skills / "alpha").mkdir()
    (project_skills / "README.md").write_text("not a skill directory\n")

    assert list_project_skills(project_skills) == ["alpha", "beta"]


def test_remove_project_skill_deletes_local_skill_directory(tmp_path: Path):
    project_skills = tmp_path / "project" / ".pi" / "skills"
    skill = project_skills / "alpha"
    skill.mkdir(parents=True)
    (skill / "notes.md").write_text("alpha skill\n")

    result = remove_project_skill("alpha", project_skills)

    assert result.skill == "alpha"
    assert result.target == skill
    assert not skill.exists()


def test_remove_project_skill_missing_skill_raises_error(tmp_path: Path):
    with pytest.raises(SvError, match="Pi skill 'missing' was not found"):
        remove_project_skill("missing", tmp_path / "project" / ".pi" / "skills")


def test_remove_project_skill_fails_on_oserror(tmp_path: Path, monkeypatch):
    project_skills = tmp_path / "project" / ".pi" / "skills"
    skill = project_skills / "alpha"
    skill.mkdir(parents=True)

    def fail_rmtree(target):
        raise OSError("Permission denied")

    monkeypatch.setattr(shutil, "rmtree", fail_rmtree)

    with pytest.raises(SvError, match="Failed to remove Pi skill 'alpha': Permission denied"):
        remove_project_skill("alpha", project_skills)


def test_sync_project_skills_updates_matching_and_leaves_unknown(tmp_path: Path):
    source_repo = tmp_path / "source"
    managed_source = source_repo / "skills" / "managed"
    managed_source.mkdir(parents=True)
    (managed_source / "notes.md").write_text("remote v2\n")

    project_skills = tmp_path / "project" / ".pi" / "skills"
    managed_local = project_skills / "managed"
    managed_local.mkdir(parents=True)
    (managed_local / "notes.md").write_text("local v1\n")
    unknown_local = project_skills / "local-only"
    unknown_local.mkdir()
    (unknown_local / "notes.md").write_text("keep me\n")

    result = sync_project_skills(source_repo, project_skills)

    assert result.no_skills_dir is False
    assert result.updated == ["managed"]
    assert result.skipped == ["local-only"]
    assert (managed_local / "notes.md").read_text() == "remote v2\n"
    assert (unknown_local / "notes.md").read_text() == "keep me\n"


def test_sync_project_skills_preserves_local_skill_when_copy_fails(
    tmp_path: Path, monkeypatch
):
    source_repo = tmp_path / "source"
    managed_source = source_repo / "skills" / "managed"
    managed_source.mkdir(parents=True)
    (managed_source / "notes.md").write_text("remote v2\n")

    project_skills = tmp_path / "project" / ".pi" / "skills"
    managed_local = project_skills / "managed"
    managed_local.mkdir(parents=True)
    (managed_local / "notes.md").write_text("local v1\n")

    def fail_copytree(source, target):
        raise OSError("copy failed")

    monkeypatch.setattr(shutil, "copytree", fail_copytree)

    with pytest.raises(SvError, match="Failed to sync skill 'managed'"):
        sync_project_skills(source_repo, project_skills)

    assert (managed_local / "notes.md").read_text() == "local v1\n"


def test_sync_project_skills_reports_no_skills_dir(tmp_path: Path):
    result = sync_project_skills(
        tmp_path / "source", tmp_path / "project" / ".pi" / "skills"
    )

    assert result.no_skills_dir is True
    assert result.updated == []
    assert result.skipped == []

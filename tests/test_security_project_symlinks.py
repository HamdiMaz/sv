from pathlib import Path
import os

import pytest

from sv.catalog import SourceSkill
from sv.cli import handle
from sv.errors import SvError
from tests.helpers import assert_no_traceback, parse_sv
from sv.project import (
    add_project_skill,
    list_project_skills,
    remove_project_skill,
    sync_project_skills,
    validate_project_skills_for_run,
)


pytestmark = [
    pytest.mark.security,
    pytest.mark.skipif(
        not hasattr(os, "symlink"), reason="symlink support is required"
    ),
]


def _make_source_skill(base: Path, name: str = "alpha") -> SourceSkill:
    source_skill = base / "source" / "skills" / name
    source_skill.mkdir(parents=True)
    return SourceSkill(
        name=name,
        description=f"{name.title()} skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=base / "source",
        source_path=source_skill,
    )


def test_add_project_skill_rejects_symlinked_pi_dir(tmp_path: Path) -> None:
    entry = _make_source_skill(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside-pi"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("outside\n")

    os.symlink(outside, project / ".pi", target_is_directory=True)

    with pytest.raises(SvError, match="Refusing to use symlinked Pi skills path"):
        add_project_skill(entry, project / ".pi" / "skills")

    assert (outside / "sentinel.txt").read_text() == "outside\n"


def test_add_project_skill_rejects_symlinked_project_skills_dir(tmp_path: Path) -> None:
    entry = _make_source_skill(tmp_path)
    project = tmp_path / "project"
    outside = tmp_path / "outside-skills"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("outside\n")
    (project / ".pi").mkdir(parents=True)
    os.symlink(
        outside,
        project / ".pi" / "skills",
        target_is_directory=True,
    )

    with pytest.raises(SvError, match="Refusing to use symlinked Pi skills path"):
        add_project_skill(entry, project / ".pi" / "skills")

    assert (outside / "sentinel.txt").read_text() == "outside\n"


def test_remove_project_skill_rejects_symlinked_pi_dir(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside-pi"
    outside_skills = outside / "skills"
    outside_skills.mkdir(parents=True)
    (outside / "sentinel.txt").write_text("outside\n")

    os.symlink(outside, project / ".pi", target_is_directory=True)

    with pytest.raises(SvError, match="Refusing to use symlinked Pi skills path"):
        remove_project_skill("alpha", project / ".pi" / "skills")

    assert (outside / "sentinel.txt").read_text() == "outside\n"


def test_remove_project_skill_rejects_symlinked_project_skill_directory(
    tmp_path: Path,
) -> None:
    project_skills = tmp_path / "project" / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    outside = tmp_path / "outside-alpha"
    outside.mkdir(parents=True)
    (outside / "sentinel.txt").write_text("outside\n")
    os.symlink(
        outside,
        project_skills / "alpha",
        target_is_directory=True,
    )

    with pytest.raises(SvError, match="Refusing to manage symlinked Pi skill 'alpha'"):
        remove_project_skill("alpha", project_skills)

    assert (outside / "sentinel.txt").read_text() == "outside\n"


def test_list_project_skills_rejects_symlinked_pi_dir(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside-pi"
    outside_skills = outside / "skills"
    outside_skills.mkdir(parents=True)
    (outside / "sentinel.txt").write_text("outside\n")

    os.symlink(outside, project / ".pi", target_is_directory=True)

    with pytest.raises(SvError, match="Refusing to use symlinked Pi skills path"):
        list_project_skills(project / ".pi" / "skills")

    assert (outside / "sentinel.txt").read_text() == "outside\n"


def test_list_project_skills_rejects_symlinked_project_skill_directory(
    tmp_path: Path,
) -> None:
    project_skills = tmp_path / "project" / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    outside = tmp_path / "outside-alpha"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("outside\n")
    os.symlink(outside, project_skills / "alpha", target_is_directory=True)

    with pytest.raises(SvError, match="Refusing to manage symlinked Pi skill 'alpha'"):
        list_project_skills(project_skills)

    assert (outside / "sentinel.txt").read_text() == "outside\n"


def test_sync_project_skills_rejects_symlinked_project_skills_dir(tmp_path: Path) -> None:
    entry = _make_source_skill(tmp_path)
    project = tmp_path / "project"
    outside = tmp_path / "outside-skills"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("outside\n")
    (project / ".pi").mkdir(parents=True)
    os.symlink(
        outside,
        project / ".pi" / "skills",
        target_is_directory=True,
    )

    with pytest.raises(SvError, match="Refusing to use symlinked Pi skills path"):
        sync_project_skills([entry], project / ".pi" / "skills")

    assert (outside / "sentinel.txt").read_text() == "outside\n"


def test_sync_project_skills_rejects_symlinked_pi_dir(tmp_path: Path) -> None:
    entry = _make_source_skill(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside-pi"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("outside\n")

    os.symlink(
        outside,
        project / ".pi",
        target_is_directory=True,
    )

    with pytest.raises(SvError, match="Refusing to use symlinked Pi skills path"):
        sync_project_skills([entry], project / ".pi" / "skills")

    assert (outside / "sentinel.txt").read_text() == "outside\n"


def test_sync_project_skills_rejects_symlinked_project_skill_directory(
    tmp_path: Path,
) -> None:
    entry = _make_source_skill(tmp_path)
    project_skills = tmp_path / "project" / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    outside = tmp_path / "outside-alpha"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("outside\n")
    os.symlink(
        outside,
        project_skills / "alpha",
        target_is_directory=True,
    )

    with pytest.raises(SvError, match="Refusing to manage symlinked Pi skill 'alpha'"):
        sync_project_skills([entry], project_skills)

    assert (outside / "sentinel.txt").read_text() == "outside\n"


def test_list_project_skills_rejects_symlinked_project_skills_path(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside-skills"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("outside\n")
    (project / ".pi").mkdir(parents=True)
    os.symlink(
        outside,
        project / ".pi" / "skills",
        target_is_directory=True,
    )

    with pytest.raises(SvError, match="Refusing to use symlinked Pi skills path"):
        list_project_skills(project / ".pi" / "skills")

    assert (outside / "sentinel.txt").read_text() == "outside\n"


def test_validate_project_skills_for_run_rejects_nested_skill_symlinks(
    tmp_path: Path,
) -> None:
    project_skills = tmp_path / "project" / ".pi" / "skills"
    alpha = project_skills / "alpha"
    alpha.mkdir(parents=True)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret\n", encoding="utf-8")
    (alpha / "secret-link.txt").symlink_to(outside)

    with pytest.raises(SvError, match="contains a symlink"):
        validate_project_skills_for_run(project_skills)

    assert outside.read_text(encoding="utf-8") == "secret\n"


def test_run_rejects_nested_project_skill_symlink_before_launching_process(
    tmp_path: Path, capsys
) -> None:
    home = tmp_path / "home"
    project_skills = tmp_path / "project" / ".pi" / "skills"
    alpha = project_skills / "alpha"
    alpha.mkdir(parents=True)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret\n", encoding="utf-8")
    (alpha / "secret-link.txt").symlink_to(outside)
    calls: list[list[str]] = []

    def process_runner(command: list[str]) -> int:
        calls.append(command)
        return 0

    exit_code = handle(
        parse_sv(["run", "pi"]),
        cwd=project_skills.parent.parent,
        home=home,
        process_runner=process_runner,
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert calls == []
    assert "contains a symlink" in captured.err
    assert_no_traceback(captured.err)
    assert outside.read_text(encoding="utf-8") == "secret\n"


def test_run_rejects_symlinked_pi_dir_before_launching_process(
    tmp_path: Path, capsys
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside-pi"
    (outside / "skills").mkdir(parents=True)
    (outside / "sentinel.txt").write_text("outside\n")
    os.symlink(outside, project / ".pi", target_is_directory=True)
    calls: list[list[str]] = []

    def process_runner(command: list[str]) -> int:
        calls.append(command)
        return 0

    exit_code = handle(
        parse_sv(["run", "pi"]),
        cwd=project,
        home=home,
        process_runner=process_runner,
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert calls == []
    assert "Refusing to use symlinked Pi skills path" in captured.err
    assert_no_traceback(captured.err)
    assert (outside / "sentinel.txt").read_text() == "outside\n"


def test_run_rejects_symlinked_project_skills_path(tmp_path: Path, capsys) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    outside = tmp_path / "outside-skills"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("outside\n")
    (project / ".pi").mkdir(parents=True)
    os.symlink(
        outside,
        project / ".pi" / "skills",
        target_is_directory=True,
    )
    calls: list[list[str]] = []

    def process_runner(command: list[str]) -> int:
        calls.append(command)
        return 0

    exit_code = handle(
        parse_sv(["run", "pi"]),
        cwd=project,
        home=home,
        process_runner=process_runner,
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert calls == []
    assert "Refusing to use symlinked Pi skills path" in captured.err
    assert_no_traceback(captured.err)
    assert (outside / "sentinel.txt").read_text() == "outside\n"


def test_run_rejects_symlinked_project_skill_directory(tmp_path: Path, capsys) -> None:
    home = tmp_path / "home"
    project_skills = tmp_path / "project" / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    outside = tmp_path / "outside-alpha"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("outside\n")
    os.symlink(
        outside,
        project_skills / "alpha",
        target_is_directory=True,
    )
    calls: list[list[str]] = []

    def process_runner(command: list[str]) -> int:
        calls.append(command)
        return 0

    exit_code = handle(
        parse_sv(["run", "pi"]),
        cwd=project_skills.parent.parent,
        home=home,
        process_runner=process_runner,
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert calls == []
    assert "Refusing to manage symlinked Pi skill 'alpha'" in captured.err
    assert_no_traceback(captured.err)
    assert (outside / "sentinel.txt").read_text() == "outside\n"

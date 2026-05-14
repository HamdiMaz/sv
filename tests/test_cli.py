from pathlib import Path
import unicodedata

import pytest

from sv.cli import _print_add_result, _print_sync_result, _print_wrapped, handle
from sv.project import AddSkillResult, SyncResult, SyncSkip
from tests.helpers import (
    assert_no_raw_control_characters,
    assert_no_traceback,
    parse_sv,
)


def parse(argv):
    return parse_sv(argv)


def display_width(value: str) -> int:
    width = 0
    for char in value:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def test_print_add_result_escapes_existing_manifest_repo_id(
    tmp_path: Path, capsys
):
    _print_add_result(
        AddSkillResult(
            skill="alpha",
            target=tmp_path / "project" / ".pi" / "skills" / "alpha",
            status="exists",
            repo_id="Org/Skills",
            existing_repo_id="Bad\x1b[2JRepo",
        )
    )

    output = capsys.readouterr().out
    assert_no_raw_control_characters(output)
    assert "Bad\\x1b[2JRepo" in output


def test_print_sync_result_escapes_manifest_repo_ids(capsys):
    _print_sync_result(
        SyncResult(
            updated=[],
            skipped=[
                SyncSkip(
                    skill="alpha",
                    reason="source-missing",
                    repo_ids=("Bad\x1b[2JRepo",),
                )
            ],
            backfilled=[],
        )
    )

    output = capsys.readouterr().out
    assert_no_raw_control_characters(output)
    assert "Bad\\x1b[2JRepo" in output


def test_run_builds_isolated_pi_command_and_forwards_args(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    calls = []

    def process_runner(command):
        calls.append(command)
        return 23

    result = run_sv(
        parse(["run", "--", "--model", "fast"]),
        cwd=project,
        home=home,
        process_runner=process_runner,
    )

    assert result.exit_code == 23
    assert calls == [["pi", "--no-skills", "--skill", ".pi/skills", "--model", "fast"]]


def test_run_reports_missing_pi_binary(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def process_runner(command):
        raise FileNotFoundError(command[0])

    result = run_sv(
        parse(["run"]),
        cwd=project,
        home=home,
        process_runner=process_runner,
    )

    assert result.exit_code == 1
    assert "Unable to run 'pi'" in result.stderr


def test_run_reports_process_launch_os_errors_without_traceback(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def process_runner(command):
        raise PermissionError("denied")

    exit_code = handle(
        parse(["run"]), cwd=project, home=home, process_runner=process_runner
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "error: Unable to run 'pi': denied" in captured.err
    assert_no_traceback(captured.err)


def test_run_escapes_control_characters_in_launch_errors(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def process_runner(command):
        raise PermissionError("denied\x1b[2J")

    exit_code = handle(
        parse(["run"]), cwd=project, home=home, process_runner=process_runner
    )

    assert exit_code == 1
    message = capsys.readouterr().err
    assert "denied\\x1b[2J" in message
    assert_no_raw_control_characters(message)


def test_run_rejects_symlinked_project_skills_path(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    outside_skills = tmp_path / "outside-skills"
    outside_skills.mkdir()
    (project / ".pi").mkdir(parents=True)
    (project / ".pi" / "skills").symlink_to(
        outside_skills, target_is_directory=True
    )
    calls = []

    def process_runner(command):
        calls.append(command)
        return 0

    exit_code = handle(
        parse(["run"]), cwd=project, home=home, process_runner=process_runner
    )

    assert exit_code == 1
    assert calls == []
    assert "Refusing to use symlinked Pi skills path" in capsys.readouterr().err


def test_run_rejects_symlinked_project_skill_directory(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    outside_skill = tmp_path / "outside-alpha"
    outside_skill.mkdir()
    project_skills = project / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    (project_skills / "alpha").symlink_to(outside_skill, target_is_directory=True)
    calls = []

    def process_runner(command):
        calls.append(command)
        return 0

    exit_code = handle(
        parse(["run"]), cwd=project, home=home, process_runner=process_runner
    )

    assert exit_code == 1
    assert calls == []
    assert "Refusing to manage symlinked Pi skill 'alpha'" in capsys.readouterr().err


def test_add_interactive_with_skill_does_not_touch_source_repo(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["add", "alpha", "-l"]), cwd=project, home=home, git_runner=git_runner
    )

    assert exit_code == 1
    assert "Use -l by itself" in capsys.readouterr().err


def test_add_invalid_skill_name_does_not_touch_source_repo(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["add", "../alpha"]), cwd=project, home=home, git_runner=git_runner
    )

    assert exit_code == 1
    assert "Invalid skill name" in capsys.readouterr().err


def test_add_qualified_repo_id_with_control_characters_does_not_touch_source_repo(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["add", "bad\x1b[2J:alpha"]),
        cwd=project,
        home=home,
        git_runner=git_runner,
    )

    assert exit_code == 1
    message = capsys.readouterr().err
    assert "bad\\x1b[2J" in message
    assert "\x1b" not in message


def test_malformed_config_reports_cli_error(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    config = home / ".sv" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("repos = [\n")

    exit_code = handle(parse(["list"]), cwd=project, home=home)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "error: Failed to read sv config" in captured.err
    assert "Traceback" not in captured.err


def test_remove_interactive_removes_selected_project_skills_without_source_repo(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    (project_skills / "alpha").mkdir(parents=True)
    (project_skills / "alpha" / "notes.md").write_text("alpha\n")
    (project_skills / "beta").mkdir()
    (project_skills / "beta" / "notes.md").write_text("beta\n")
    selector_calls = []

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    def skill_selector(skills):
        selector_calls.append(skills)
        return ["beta"]

    exit_code = handle(
        parse(["remove", "-l"]),
        cwd=project,
        home=home,
        git_runner=git_runner,
        skill_selector=skill_selector,
    )

    assert exit_code == 0
    assert selector_calls == [["alpha", "beta"]]
    assert (project_skills / "alpha" / "notes.md").read_text() == "alpha\n"
    assert not (project_skills / "beta").exists()
    assert "Removed Pi skill 'beta'" in capsys.readouterr().out


def test_remove_interactive_reports_no_project_skills(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(parse(["remove", "-l"]), cwd=project, home=home)

    assert exit_code == 0
    assert "No Pi skills found to remove." in capsys.readouterr().out


def test_remove_skill_removes_project_skill_without_source_repo(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    skill = project / ".pi" / "skills" / "alpha"
    skill.mkdir(parents=True)

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["remove", "alpha"]), cwd=project, home=home, git_runner=git_runner
    )

    assert exit_code == 0
    assert not skill.exists()
    assert "Removed Pi skill 'alpha'" in capsys.readouterr().out


def test_remove_interactive_with_skill_does_not_touch_source_repo(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["remove", "alpha", "-l"]),
        cwd=project,
        home=home,
        git_runner=git_runner,
    )

    assert exit_code == 1
    assert "Use -l by itself" in capsys.readouterr().err


def test_repo_list_table_fits_terminal_width(tmp_path: Path, capsys, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "very-long-local-skill-source-name"
    source.mkdir()

    assert handle(parse(["repo", "add", str(source)]), cwd=project, home=home) == 0
    capsys.readouterr()
    monkeypatch.setenv("COLUMNS", "40")

    exit_code = handle(parse(["repo", "list"]), cwd=project, home=home)

    assert exit_code == 0
    assert all(len(line) <= 40 for line in capsys.readouterr().out.splitlines())


def test_repo_add_and_list_use_multi_repo_config(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    assert (
        handle(parse(["repo", "add", "HamdiMaz/Skills"]), cwd=project, home=home) == 0
    )
    assert (
        handle(parse(["repo", "add", "SomeOrg/TeamSkills"]), cwd=project, home=home)
        == 0
    )
    capsys.readouterr()

    exit_code = handle(parse(["repo", "list"]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Repo" in output
    assert "URL" in output
    assert "Cache" in output
    assert "HamdiMaz/Skills" in output
    assert "https://github.com/HamdiMaz/Skills.git" in output
    assert "SomeOrg/TeamSkills" in output


def test_repo_add_reports_existing_repo(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    assert (
        handle(parse(["repo", "add", "HamdiMaz/Skills"]), cwd=project, home=home) == 0
    )
    capsys.readouterr()
    exit_code = handle(
        parse(["repo", "add", "HamdiMaz/Skills"]), cwd=project, home=home
    )

    assert exit_code == 0
    assert "already configured" in capsys.readouterr().out


def test_repo_add_reports_unresolvable_home_without_traceback(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(
        parse(["repo", "add", "~definitely-not-an-sv-user/Skills"]),
        cwd=project,
        home=home,
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "error: Could not resolve home directory" in captured.err
    assert "Traceback" not in captured.err


def test_repo_remove_escapes_control_characters_in_missing_repo(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(
        parse(["repo", "remove", "missing\x1b[2J"]), cwd=project, home=home
    )

    assert exit_code == 1
    message = capsys.readouterr().err
    assert "missing\\x1b[2J" in message
    assert "\x1b" not in message


def test_repo_remove_updates_config_without_deleting_project_skills(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    skill = project / ".pi" / "skills" / "alpha"
    skill.mkdir(parents=True)

    assert (
        handle(parse(["repo", "add", "SomeOrg/TeamSkills"]), cwd=project, home=home)
        == 0
    )
    capsys.readouterr()

    exit_code = handle(
        parse(["repo", "remove", "SomeOrg/TeamSkills"]), cwd=project, home=home
    )

    assert exit_code == 0
    assert skill.is_dir()
    assert "Removed repo SomeOrg/TeamSkills" in capsys.readouterr().out


def test_add_help_explains_skill_argument(capsys):
    with pytest.raises(SystemExit) as exc_info:
        parse(["add", "--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "Skill name or repo:skill reference" in help_text
    assert "choose a source" in help_text


def test_print_wrapped_omits_indent_when_terminal_is_too_narrow(
    capsys, monkeypatch
):
    monkeypatch.setenv("COLUMNS", "4")

    _print_wrapped("one two three")

    assert all(len(line) <= 4 for line in capsys.readouterr().out.splitlines())


def test_print_wrapped_honors_display_width_for_wide_unicode(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "20")

    _print_wrapped("Duplicate 日本語日本語日本語 skill names")

    assert all(display_width(line) <= 20 for line in capsys.readouterr().out.splitlines())


def test_config_command_is_removed_from_parser():
    with pytest.raises(SystemExit):
        parse(["config", "show"])

from argparse import Namespace
from pathlib import Path

import pytest

import sv.cli as cli_module
from sv.catalog import SourceSkill
from sv.cli import _print_add_result, _print_sync_result, _print_wrapped, handle
from sv.errors import SvError
from sv.project import AddSkillResult, SyncResult, SyncSkip
from tests.helpers import (
    assert_no_raw_control_characters,
    assert_no_traceback,
    display_width,
    parse_sv,
)


def parse(argv):
    return parse_sv(argv)


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


def test_print_wrapped_replaces_overwide_character_at_one_column(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "1")

    _print_wrapped("🤖")

    assert capsys.readouterr().out.splitlines() == ["?"]


def test_print_wrapped_keeps_combining_mark_with_base_character(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "1")

    _print_wrapped("a\u0301b")

    assert capsys.readouterr().out.splitlines() == ["a\u0301", "b"]


def test_config_command_is_removed_from_parser():
    with pytest.raises(SystemExit):
        parse(["config", "show"])


def test_default_process_runner_delegates_to_subprocess_call(monkeypatch):
    calls = []

    def fake_call(command):
        calls.append(command)
        return 17

    monkeypatch.setattr(cli_module.subprocess, "call", fake_call)

    assert cli_module.default_process_runner(("pi", "--help")) == 17
    assert calls == [["pi", "--help"]]


def test_main_parses_arguments_and_uses_current_project_paths(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    home = tmp_path / "home"
    parsed_calls = []

    monkeypatch.setattr(cli_module.Path, "cwd", lambda: project)
    monkeypatch.setattr(cli_module.Path, "home", lambda: home)

    def fake_handle(args, cwd, home):
        parsed_calls.append((args.command, args.repo_command, cwd, home))
        return 0

    monkeypatch.setattr(cli_module, "handle", fake_handle)

    assert cli_module.main(["repo", "list"]) == 0
    assert parsed_calls == [("repo", "list", project, home)]


def test_remove_without_skill_reports_actionable_error(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(parse(["remove"]), cwd=project, home=home)

    assert exit_code == 1
    assert "Specify a skill name or use -l." in capsys.readouterr().err


def test_unknown_commands_are_reported_without_tracebacks(tmp_path: Path, capsys):
    exit_code = handle(Namespace(command="mystery"), cwd=tmp_path, home=tmp_path)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Unknown command: mystery" in captured.err
    assert_no_traceback(captured.err)


def test_unknown_repo_subcommand_is_reported_without_tracebacks(
    tmp_path: Path, capsys
):
    exit_code = handle(
        Namespace(command="repo", repo_command="mystery"), cwd=tmp_path, home=tmp_path
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Unknown repo command: mystery" in captured.err
    assert_no_traceback(captured.err)


def test_empty_repo_qualified_add_reference_fails_before_git(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(parse(["add", ":alpha"]), cwd=project, home=home, git_runner=git_runner)

    assert exit_code == 1
    assert "Invalid skill reference ':alpha'. Use repo:skill." in capsys.readouterr().err


def test_missing_qualified_skill_reference_reports_exact_reference(tmp_path: Path):
    with pytest.raises(SvError, match="Org/Skills:missing"):
        cli_module._handle_add(
            "Org/Skills:missing",
            [],
            cwd=tmp_path,
            adapter=cli_module.PiAdapter(),
            skill_chooser=lambda matches: None,
        )


def test_duplicate_add_choice_can_be_cancelled(tmp_path: Path, capsys):
    first = _source_skill(tmp_path, "source-a", repo_id="Org/A")
    second = _source_skill(tmp_path, "source-b", repo_id="Org/B")

    exit_code = cli_module._handle_add(
        "alpha",
        [first, second],
        cwd=tmp_path / "project",
        adapter=cli_module.PiAdapter(),
        skill_chooser=lambda matches: None,
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Multiple source skills match 'alpha'" in output
    assert "No skill selected." in output
    assert "Org/A:alpha" in output


def test_add_all_and_interactive_report_empty_catalog(tmp_path: Path, capsys):
    project = tmp_path / "project"

    assert cli_module._handle_add_all([], cwd=project, adapter=cli_module.PiAdapter()) == 0
    assert (
        cli_module._handle_add_interactive(
            [],
            cwd=project,
            adapter=cli_module.PiAdapter(),
            skill_selector=lambda skills, **kwargs: [],
        )
        == 0
    )

    output = capsys.readouterr().out
    assert output.count("No valid skills found in configured source repos.") == 2


def test_print_add_result_existing_without_recorded_origin_mentions_requested_repo(
    tmp_path: Path, capsys
):
    _print_add_result(
        AddSkillResult(
            skill="alpha",
            target=tmp_path / "project" / ".pi" / "skills" / "alpha",
            status="exists",
            repo_id="Org/Skills",
            existing_repo_id=None,
        )
    )

    output = capsys.readouterr().out
    assert "no sv origin recorded" in output
    assert "requested Org/Skills" in output


def test_remove_interactive_cancel_leaves_project_skills_unchanged(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    skill = project / ".pi" / "skills" / "alpha"
    skill.mkdir(parents=True)
    (skill / "notes.md").write_text("alpha\n")

    exit_code = handle(
        parse(["remove", "-l"]),
        cwd=project,
        home=home,
        skill_selector=lambda skills: [],
    )

    assert exit_code == 0
    assert (skill / "notes.md").read_text() == "alpha\n"
    assert "No skills selected." in capsys.readouterr().out


def test_choose_skill_returns_none_without_tty(monkeypatch, tmp_path: Path):
    class NonTty:
        def isatty(self):
            return False

    monkeypatch.setattr(cli_module.sys, "stdin", NonTty())
    monkeypatch.setattr(cli_module.sys, "stdout", NonTty())

    assert cli_module._choose_skill([_source_skill(tmp_path, "source")]) is None


def test_choose_skill_reprompts_until_valid_selection(monkeypatch, tmp_path: Path):
    skill = _source_skill(tmp_path, "source")
    choices = iter(["bad", "3", "1"])

    class TtyOutput:
        def __init__(self):
            self.text = ""

        def isatty(self):
            return True

        def write(self, text):
            self.text += text

        def flush(self):
            return None

    output = TtyOutput()
    monkeypatch.setattr(cli_module.sys, "stdin", output)
    monkeypatch.setattr(cli_module.sys, "stdout", output)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(choices))

    assert cli_module._choose_skill([skill]) == skill
    assert "Enter a number from 1 to 1" in output.text


def test_choose_skill_accepts_quit(monkeypatch, tmp_path: Path):
    class TtyInput:
        def isatty(self):
            return True

    monkeypatch.setattr(cli_module.sys, "stdin", TtyInput())
    monkeypatch.setattr(cli_module.sys, "stdout", TtyInput())
    monkeypatch.setattr("builtins.input", lambda _prompt: "q")

    assert cli_module._choose_skill([_source_skill(tmp_path, "source")]) is None


def _source_skill(tmp_path: Path, repo_folder: str, *, repo_id: str = "Org/Skills") -> SourceSkill:
    source = tmp_path / repo_folder
    skill_dir = source / "skills" / "alpha"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("---\nname: alpha\ndescription: Alpha skill.\n---\n")
    return SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo_id,
        repo_url=f"https://github.com/{repo_id}.git",
        repo_path=source,
        source_path=skill_dir,
    )

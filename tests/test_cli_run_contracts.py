import os

import pytest

from tests.helpers import assert_no_raw_control_characters, assert_no_traceback


def test_run_builds_isolated_command_with_explicit_pi_agent(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    calls: list[list[str]] = []

    def process_runner(command: list[str]) -> int:
        calls.append(command)
        return 7

    result = run_sv(
        ["run", "pi"], cwd=project, home=home, process_runner=process_runner
    )

    assert result.exit_code == 7
    assert calls == [["pi", "--no-skills", "--skill", ".pi/skills"]]


def test_run_from_git_subdirectory_uses_repo_root_pi_skills(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    subdir = project / "subdir"
    project_skills = project / ".pi" / "skills"
    (project / ".git").mkdir(parents=True)
    (project_skills / "alpha").mkdir(parents=True)
    subdir.mkdir()
    calls: list[list[str]] = []

    def process_runner(command: list[str]) -> int:
        calls.append(command)
        return 0

    result = run_sv(
        ["run", "pi", "--", "--model", "fast"],
        cwd=subdir,
        home=home,
        process_runner=process_runner,
    )

    assert result.exit_code == 0
    assert calls == [
        ["pi", "--no-skills", "--skill", str(project_skills), "--model", "fast"]
    ]


def test_run_strips_arg_separator_after_agent_and_forwards_agent_args(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    calls: list[list[str]] = []

    def process_runner(command: list[str]) -> int:
        calls.append(command)
        return 0

    result = run_sv(
        ["run", "pi", "--", "--model", "fast"],
        cwd=project,
        home=home,
        process_runner=process_runner,
    )

    assert result.exit_code == 0
    assert calls == [["pi", "--no-skills", "--skill", ".pi/skills", "--model", "fast"]]


def test_run_forwards_agent_args_directly_after_agent(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    calls: list[list[str]] = []

    def process_runner(command: list[str]) -> int:
        calls.append(command)
        return 0

    result = run_sv(
        ["run", "pi", "--model", "fast"],
        cwd=project,
        home=home,
        process_runner=process_runner,
    )

    assert result.exit_code == 0
    assert calls == [["pi", "--no-skills", "--skill", ".pi/skills", "--model", "fast"]]


def test_run_with_empty_separator_after_agent_omits_agent_args(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    calls: list[list[str]] = []

    def process_runner(command: list[str]) -> int:
        calls.append(command)
        return 0

    result = run_sv(
        ["run", "pi", "--"], cwd=project, home=home, process_runner=process_runner
    )

    assert result.exit_code == 0
    assert calls == [["pi", "--no-skills", "--skill", ".pi/skills"]]


def test_run_without_agent_reports_supported_agents_before_launch(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    calls: list[list[str]] = []

    def process_runner(command: list[str]) -> int:
        calls.append(command)
        return 0

    result = run_sv(["run"], cwd=project, home=home, process_runner=process_runner)

    assert result.exit_code == 1
    assert calls == []
    assert "error: Specify an agent name. Supported agents: pi." in result.stderr
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)


def test_run_old_separator_without_agent_reports_supported_agents_before_launch(
    tmp_path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    calls: list[list[str]] = []

    def process_runner(command: list[str]) -> int:
        calls.append(command)
        return 0

    result = run_sv(
        ["run", "--", "--model", "fast"],
        cwd=project,
        home=home,
        process_runner=process_runner,
    )

    assert result.exit_code == 1
    assert calls == []
    assert "error: Specify an agent name. Supported agents: pi." in result.stderr
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)


def test_run_unknown_agent_reports_supported_agents_before_launch(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    calls: list[list[str]] = []

    def process_runner(command: list[str]) -> int:
        calls.append(command)
        return 0

    result = run_sv(
        ["run", "claude"], cwd=project, home=home, process_runner=process_runner
    )

    assert result.exit_code == 1
    assert calls == []
    assert "error: Unsupported agent 'claude'. Supported agents: pi." in result.stderr
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)


def test_run_unknown_agent_with_control_characters_is_escaped(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    calls: list[list[str]] = []

    def process_runner(command: list[str]) -> int:
        calls.append(command)
        return 0

    result = run_sv(
        ["run", "claude\x1b[2J"], cwd=project, home=home, process_runner=process_runner
    )

    assert result.exit_code == 1
    assert calls == []
    assert "Unsupported agent 'claude\\x1b[2J'. Supported agents: pi." in result.stderr
    assert "\x1b" not in result.stderr
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)


def test_run_missing_pi_reports_user_facing_error(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def process_runner(command: list[str]) -> int:
        raise FileNotFoundError(command[0])

    result = run_sv(
        ["run", "pi"], cwd=project, home=home, process_runner=process_runner
    )

    assert result.exit_code == 1
    assert "error: Unable to run 'pi'" in result.stderr
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)


def test_run_launch_os_error_reports_user_facing_error(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def process_runner(command: list[str]) -> int:
        raise PermissionError("denied")

    result = run_sv(
        ["run", "pi"], cwd=project, home=home, process_runner=process_runner
    )

    assert result.exit_code == 1
    assert "error: Unable to run 'pi': denied" in result.stderr
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)


def test_run_launch_error_with_control_characters_is_escaped(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def process_runner(command: list[str]) -> int:
        raise OSError("denied\x1b[2J")

    result = run_sv(
        ["run", "pi"], cwd=project, home=home, process_runner=process_runner
    )

    assert result.exit_code == 1
    assert "Unable to run 'pi': denied\\x1b[2J" in result.stderr
    assert "\x1b" not in result.stderr
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_run_rejects_symlinked_pi_skills_path(tmp_path, run_sv):
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

    result = run_sv(
        ["run", "pi"], cwd=project, home=home, process_runner=process_runner
    )

    assert result.exit_code == 1
    assert calls == []
    assert "Refusing to use symlinked Pi skills path" in result.stderr
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)
    assert (outside / "sentinel.txt").read_text() == "outside\n"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_run_rejects_symlinked_project_skill_directory(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
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

    result = run_sv(
        ["run", "pi"], cwd=project, home=home, process_runner=process_runner
    )

    assert result.exit_code == 1
    assert calls == []
    assert "Refusing to manage symlinked Pi skill 'alpha'" in result.stderr
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)
    assert (outside / "sentinel.txt").read_text() == "outside\n"

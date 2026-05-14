import subprocess

from sv.source import default_runner
from tests.helpers import (
    assert_no_raw_control_characters,
    assert_no_traceback,
    configure_source,
    make_source_repo,
    run_git,
    write_source_skill,
)


def test_update_updates_sources_and_syncs_project_skills(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    add_result = run_sv(
        ["add", "alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    assert add_result.exit_code == 0
    assert "Added Pi skill 'alpha'" in add_result.stdout

    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)

    result = run_sv(["update"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "Updating source repos..." in result.stdout
    assert "Syncing project skills..." in result.stdout
    assert "Synced Pi skill 'alpha'." in result.stdout
    assert_no_traceback(result.stdout)
    assert_no_raw_control_characters(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha v2\n"


def test_update_reports_source_pull_failure_without_syncing_project_skills(
    tmp_path, run_sv
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    add_result = run_sv(
        ["add", "alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )
    assert add_result.exit_code == 0

    project_skill = project / ".pi" / "skills" / "alpha" / "notes.md"
    assert project_skill.read_text() == "alpha v1\n"

    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")

    def git_runner(args, cwd=None):
        if args == ["git", "--version"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="git version 2.39.0\n",
            )
        if args == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=f"{source}\n")
        if args[:2] == ["git", "pull"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stderr="pull failed due to lock\n",
            )
        return default_runner(args, cwd)

    result = run_sv(["update"], cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 1
    assert "Updating source repos..." in result.stdout
    assert "Syncing project skills..." not in result.stdout
    assert "Synced Pi skill 'alpha'." not in result.stdout
    assert "error: Updating source repo failed: pull failed due to lock" in result.stderr
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)
    assert_no_traceback(result.stdout)
    assert_no_raw_control_characters(result.stdout)
    assert project_skill.read_text() == "alpha v1\n"

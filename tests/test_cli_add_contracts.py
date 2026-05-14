from pathlib import Path

from sv.config import SvPaths
from tests.helpers import assert_no_raw_control_characters, assert_no_traceback


def _assert_validation_rejected(result, project: Path) -> None:
    assert result.exit_code == 1
    assert_no_raw_control_characters(result.stderr)
    assert_no_traceback(result.stderr)
    assert not (project / ".pi" / "skills").exists()


def _forbid_git_calls(args, cwd=None):
    raise AssertionError(f"unexpected git call: {args}")


def test_add_missing_skill_fails_without_git_or_project_skill_dir(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text("repos = []\n")

    result = run_sv(
        ["add", "missing"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
    )

    _assert_validation_rejected(result, project)
    assert "Skill 'missing' was not found in configured source repos." in result.stderr


def test_add_invalid_skill_name_fails_without_git_or_project_skill_dir(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    result = run_sv(
        ["add", "../alpha"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
    )

    _assert_validation_rejected(result, project)
    assert "Invalid skill name '../alpha'." in result.stderr
    assert "Use a single source skill folder name" in result.stderr


def test_add_invalid_qualified_repo_id_with_control_characters_fails_without_git_or_mutation(
    tmp_path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    result = run_sv(
        ["add", "bad\x1b[2J:alpha"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
    )

    _assert_validation_rejected(result, project)
    assert "Invalid skill reference 'bad\\x1b[2J:alpha'." in result.stderr
    assert "Repo id cannot contain control characters." in result.stderr


def test_add_all_and_skill_are_mutually_exclusive(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    result = run_sv(
        ["add", "alpha", "--all"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
    )

    _assert_validation_rejected(result, project)
    assert "Use either a skill name or --all" in result.stderr


def test_add_interactive_and_skill_are_mutually_exclusive(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    result = run_sv(
        ["add", "alpha", "-l"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
    )

    _assert_validation_rejected(result, project)
    assert "Use -l by itself" in result.stderr

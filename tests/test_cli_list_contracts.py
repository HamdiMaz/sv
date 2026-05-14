from pathlib import Path

import pytest

from sv.config import SvPaths
from sv.source import default_runner
from tests.helpers import (
    configure_source,
    display_width,
    make_source_repo,
    run_git,
    write_source_skill,
)


def test_list_with_explicit_empty_repos_prints_next_step_and_skips_git(
    tmp_path: Path,
    run_sv,
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text("repos = []\n")

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    result = run_sv(["list"], cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 0
    assert "No skill source repos configured. Add one with 'sv repo add <owner/repo>'." in result.stdout
    assert result.stderr == ""


def test_list_with_repo_having_no_valid_skills_prints_empty_message(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)
    run_git(["rm", "-r", "skills"], source)
    run_git(["commit", "-m", "remove all skills"], source)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    result = run_sv(["list"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "No valid skills found in configured source repos." in result.stdout
    assert "alpha" not in result.stdout
    assert "beta" not in result.stdout


def test_list_skips_invalid_skills(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)

    invalid_skill = source / "skills" / "invalid"
    invalid_skill.mkdir()
    (invalid_skill / "notes.md").write_text("placeholder\n")
    run_git(["add", "skills/invalid"], source)
    run_git(["commit", "-m", "add invalid skill"], source)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    result = run_sv(["list"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    output = result.stdout
    assert "invalid" not in output
    assert "alpha" in output
    assert "beta" in output


def test_list_fails_clearly_on_unreadable_skill_metadata(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)

    broken = source / "skills" / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_bytes(b"\xff\xfe\x00")
    run_git(["add", "skills/broken/SKILL.md"], source)
    run_git(["commit", "-m", "add unreadable skill"], source)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    result = run_sv(["list"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 1
    assert "error: Failed to read SKILL.md for skill 'broken'" in result.stderr


@pytest.mark.parametrize("columns", [80, 40, 20, 5])
def test_list_output_respects_terminal_width_when_wrapping(
    tmp_path: Path, run_sv, monkeypatch, columns: int
):
    source = make_source_repo(tmp_path, "wide-source")
    write_source_skill(
        source,
        "alpha",
        "Alpha description with Japanese symbols: 日本語 日本語 日本語 日本語 to keep output wrapping behavior explicit.",
        "alpha v1\n",
    )
    run_git(["add", "skills/alpha/SKILL.md"], source)
    run_git(["commit", "-m", "rewrite alpha metadata"], source)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    monkeypatch.setenv("COLUMNS", str(columns))
    result = run_sv(["list"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert all(display_width(line) <= columns for line in result.stdout.splitlines())

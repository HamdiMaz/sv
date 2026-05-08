from pathlib import Path
import shutil
import subprocess

import pytest

from sv.cli import build_parser, handle


def parse(argv):
    return build_parser().parse_args(argv)


def run_git(args, cwd: Path):
    subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True)


def make_source_repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is required for integration tests")

    source = tmp_path / "skill-source"
    (source / "skills" / "alpha").mkdir(parents=True)
    (source / "skills" / "alpha" / "notes.md").write_text("alpha v1\n")
    (source / "skills" / "beta").mkdir(parents=True)
    (source / "skills" / "beta" / "notes.md").write_text("beta v1\n")

    run_git(["init"], source)
    run_git(["config", "user.email", "tests@example.com"], source)
    run_git(["config", "user.name", "sv tests"], source)
    run_git(["add", "skills"], source)
    run_git(["commit", "-m", "initial skills"], source)
    return source


def configure_source(source: Path, project: Path, home: Path):
    exit_code = handle(parse(["config", "repo", str(source)]), cwd=project, home=home)
    assert exit_code == 0


def test_list_and_add_from_local_git_source(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["list"]), cwd=project, home=home)

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == ["alpha", "beta"]

    exit_code = handle(parse(["add", "alpha"]), cwd=project, home=home)

    assert exit_code == 0
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha v1\n"
    assert "Added Pi skill 'alpha'" in capsys.readouterr().out

    exit_code = handle(parse(["add", "alpha"]), cwd=project, home=home)

    assert exit_code == 0
    assert "already exists" in capsys.readouterr().out


@pytest.mark.parametrize("add_args", [["add", "all"], ["add", "--all"]])
def test_add_all_adds_every_source_skill(tmp_path: Path, capsys, add_args):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(add_args), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Added Pi skill 'alpha'" in output
    assert "Added Pi skill 'beta'" in output
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha v1\n"
    assert (
        project / ".pi" / "skills" / "beta" / "notes.md"
    ).read_text() == "beta v1\n"


def test_add_all_reports_existing_skills_without_overwrite(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert handle(parse(["add", "alpha"]), cwd=project, home=home) == 0
    capsys.readouterr()

    exit_code = handle(parse(["add", "--all"]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Pi skill 'alpha' already exists" in output
    assert "Added Pi skill 'beta'" in output
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha v1\n"
    assert (
        project / ".pi" / "skills" / "beta" / "notes.md"
    ).read_text() == "beta v1\n"


def test_add_missing_skill_reports_error(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["add", "missing"]), cwd=project, home=home)

    assert exit_code == 1
    assert "Skill 'missing' was not found" in capsys.readouterr().err


def test_add_without_skill_reports_error(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(parse(["add"]), cwd=project, home=home)

    assert exit_code == 1
    assert "Specify a skill name or use --all" in capsys.readouterr().err


def test_add_skill_and_all_reports_error_without_touching_source(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["add", "alpha", "--all"]),
        cwd=project,
        home=home,
        git_runner=git_runner,
    )

    assert exit_code == 1
    assert "Use either a skill name or --all" in capsys.readouterr().err


def test_sync_updates_matching_skills_and_leaves_unknown(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert handle(parse(["add", "alpha"]), cwd=project, home=home) == 0
    capsys.readouterr()

    unknown = project / ".pi" / "skills" / "local-only"
    unknown.mkdir()
    (unknown / "notes.md").write_text("keep me\n")

    (source / "skills" / "alpha" / "notes.md").write_text("alpha v2\n")
    run_git(["add", "skills/alpha/notes.md"], source)
    run_git(["commit", "-m", "update alpha"], source)

    exit_code = handle(parse(["sync"]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Synced Pi skill 'alpha'." in output
    assert "Skipped local Pi skill 'local-only'" in output
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha v2\n"
    assert (unknown / "notes.md").read_text() == "keep me\n"


def test_sync_with_no_pi_skills_dir_exits_successfully(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["sync"]), cwd=project, home=home)

    assert exit_code == 0
    assert "No Pi skills found to sync." in capsys.readouterr().out

from pathlib import Path

import pytest

from sv.cli import build_parser, handle
from sv.config import SvPaths, load_config
from tests.helpers import configure_source, make_source_repo


def parse(argv):
    return build_parser().parse_args(argv)


def test_repo_list_with_no_configured_repos_explains_how_to_add_one(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text("repos = []\n")

    exit_code = handle(parse(["repo", "list"]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "No skill source repos configured." in output
    assert "sv repo add" in output
    assert "Repo  URL" not in output


@pytest.mark.integration
def test_list_and_add_from_local_git_source(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["list"]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    header = output.splitlines()[0]
    assert "Skill" in header
    assert "Source" in header
    assert "Description" in header
    assert "Add as" not in header
    assert "alpha" in output
    assert "Alpha skill." in output
    assert "beta" in output
    assert "Beta skill." in output
    repo_id = load_config(SvPaths.from_home(home)).repos[0].id
    assert f"{repo_id}:alpha" not in output

    exit_code = handle(parse(["add", "alpha"]), cwd=project, home=home)

    assert exit_code == 0
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha v1\n"
    assert "Added Pi skill 'alpha'" in capsys.readouterr().out

    exit_code = handle(parse(["add", "alpha"]), cwd=project, home=home)

    assert exit_code == 0
    assert "already exists" in capsys.readouterr().out

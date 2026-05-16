from pathlib import Path
import sys

import pytest

from sv.cli import build_parser, handle
from sv.config import SvPaths, load_config
from tests.helpers import configure_source, make_source_repo


def parse(argv):
    return build_parser().parse_args(argv)


class _TtyProxy:
    def __init__(self, wrapped):
        self._wrapped = wrapped

    def isatty(self):
        return True

    def write(self, value):
        return self._wrapped.write(value)

    def flush(self):
        return self._wrapped.flush()


def test_repo_add_accepts_repeated_skills_paths_in_order_without_duplicates(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(
        parse(
            [
                "repo",
                "add",
                "owner/repo",
                "--skills-path",
                "packages/agents/pi/skills",
                "--skills-path",
                "tools/skills",
                "--skills-path",
                "packages/agents/pi/skills",
            ]
        ),
        cwd=project,
        home=home,
    )

    assert exit_code == 0
    assert load_config(SvPaths.from_home(home)).repos[0].skills_paths == (
        "packages/agents/pi/skills",
        "tools/skills",
    )
    assert "Added repo owner/repo" in capsys.readouterr().out


def test_repo_add_rejects_invalid_skills_path_with_helpful_error(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(
        parse(["repo", "add", "owner/repo", "--skills-path", "../outside"]),
        cwd=project,
        home=home,
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert "--skills-path" in captured.err
    assert "unsafe path components" in captured.err
    assert not SvPaths.from_home(home).config_file.exists()


def test_repo_list_does_not_show_skills_paths_by_default(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    assert (
        handle(
            parse(
                [
                    "repo",
                    "add",
                    "owner/repo",
                    "--skills-path",
                    "packages/agents/pi/skills",
                ]
            ),
            cwd=project,
            home=home,
        )
        == 0
    )
    capsys.readouterr()

    exit_code = handle(parse(["repo", "list"]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Repo" in output
    assert "URL" in output
    assert "Cache" in output
    assert "Skills" not in output
    assert "packages/agents/pi/skills" not in output


def test_repo_dash_l_matches_repo_list_non_tty_output(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    assert handle(parse(["repo", "add", "owner/repo"]), cwd=project, home=home) == 0
    capsys.readouterr()

    list_exit_code = handle(parse(["repo", "list"]), cwd=project, home=home)
    list_output = capsys.readouterr().out
    alias_exit_code = handle(parse(["repo", "-l"]), cwd=project, home=home)
    alias_output = capsys.readouterr().out

    assert alias_exit_code == list_exit_code == 0
    assert alias_output == list_output


def test_svx_main_matches_repo_add_output_and_config(tmp_path: Path, capsys, monkeypatch):
    from sv import cli as cli_module

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(cli_module.Path, "cwd", lambda: project)
    monkeypatch.setattr(cli_module.Path, "home", lambda: home)

    exit_code = cli_module.svx_main(["owner/repo"])

    assert exit_code == 0
    assert [repo.id for repo in load_config(SvPaths.from_home(home)).repos] == [
        "owner/repo"
    ]
    assert capsys.readouterr().out == (
        "Added repo owner/repo (https://github.com/owner/repo.git)\n"
    )


def test_repo_remove_interactive_requires_yes_in_non_tty_without_mutation(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    assert handle(parse(["repo", "add", "owner/one"]), cwd=project, home=home) == 0
    assert handle(parse(["repo", "add", "owner/two"]), cwd=project, home=home) == 0
    capsys.readouterr()

    exit_code = handle(
        parse(["repo", "remove", "-l"]),
        cwd=project,
        home=home,
        skill_selector=lambda repos, **kwargs: ["owner/two"],
    )

    assert exit_code == 1
    assert "Non-TTY 'sv repo remove -l' requires --yes." in capsys.readouterr().err
    assert [repo.id for repo in load_config(SvPaths.from_home(home)).repos] == [
        "owner/one",
        "owner/two",
    ]


def test_repo_remove_interactive_removes_selected_repos_with_confirmation(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    assert handle(parse(["repo", "add", "owner/one"]), cwd=project, home=home) == 0
    assert handle(parse(["repo", "add", "owner/two"]), cwd=project, home=home) == 0
    capsys.readouterr()
    selector_calls = []

    def repo_selector(repos, **kwargs):
        selector_calls.append((repos, kwargs))
        return ["owner/two"]

    exit_code = handle(
        parse(["repo", "remove", "-l", "--yes"]),
        cwd=project,
        home=home,
        skill_selector=repo_selector,
    )

    assert exit_code == 0
    assert selector_calls[0][0] == ["owner/one", "owner/two"]
    label = selector_calls[0][1]["item_label"]("owner/two")
    assert "Repo" in label
    assert "URL" in label
    assert "owner/two" in label
    assert [repo.id for repo in load_config(SvPaths.from_home(home)).repos] == ["owner/one"]
    assert "Removed repo owner/two" in capsys.readouterr().out


def test_repo_remove_interactive_declined_confirmation_leaves_config_unchanged(
    tmp_path: Path, capsys, monkeypatch
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    assert handle(parse(["repo", "add", "owner/one"]), cwd=project, home=home) == 0
    assert handle(parse(["repo", "add", "owner/two"]), cwd=project, home=home) == 0
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")

    exit_code = handle(
        parse(["repo", "remove", "-l"]),
        cwd=project,
        home=home,
        skill_selector=lambda repos, **kwargs: ["owner/two"],
    )

    assert exit_code == 0
    assert [repo.id for repo in load_config(SvPaths.from_home(home)).repos] == [
        "owner/one",
        "owner/two",
    ]
    assert "No repos removed." in capsys.readouterr().out


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
def test_list_warns_and_skips_invalid_generic_skills(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    invalid = source / "skills" / "invalid"
    invalid.mkdir()
    (invalid / "SKILL.md").write_text("---\nname: other\ndescription: Bad.\n---\n")
    from tests.helpers import run_git

    run_git(["add", "skills/invalid/SKILL.md"], source)
    run_git(["commit", "-m", "add invalid skill"], source)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["list"]), cwd=project, home=home)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "alpha" in captured.out
    assert "invalid" not in captured.out
    assert "warning: skipping invalid skill" in captured.err
    assert "skills/invalid" in captured.err
    assert "does not match folder" in captured.err


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

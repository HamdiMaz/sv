from __future__ import annotations

from pathlib import Path

import pytest

from sv.cli import handle
from sv.config import RecommendedSource, SvPaths
from tests.helpers import make_source_repo, parse_sv


@pytest.mark.parametrize(
    "args",
    [
        ["list"],
        ["search", "alpha"],
        ["add", "alpha"],
        ["add", "--all"],
        ["sync"],
        ["update"],
        ["repo", "list"],
    ],
)
def test_commands_fail_helpfully_without_config_in_non_tty(
    tmp_path: Path, run_sv, args: list[str]
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    if args[0] in {"add", "sync", "update"}:
        (project / ".pi").mkdir()

    def git_runner(git_args, cwd=None):
        raise AssertionError(f"unexpected git call: {git_args}")

    result = run_sv(args, cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 1
    assert "No skill source repos are configured" in result.stderr
    assert "sv repo add <repo>" in result.stderr
    assert "HamdiMaz/Skills" not in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    "args",
    [
        ["list"],
        ["search", "alpha"],
        ["add", "alpha"],
        ["add", "--all"],
        ["sync"],
        ["update"],
        ["repo", "list"],
    ],
)
def test_schema_only_config_is_treated_as_first_run_missing_config(
    tmp_path: Path, run_sv, args: list[str]
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    if args[0] in {"add", "sync", "update"}:
        (project / ".pi").mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text("schema_version = 1\n")

    def git_runner(git_args, cwd=None):
        raise AssertionError(f"unexpected git call: {git_args}")

    result = run_sv(args, cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 1
    assert "No skill source repos are configured" in result.stderr
    assert "sv repo add <repo>" in result.stderr
    assert result.stdout == ""


def test_explicit_empty_repos_is_not_first_run_missing_config(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text("schema_version = 1\nrepos = []\n")

    def git_runner(git_args, cwd=None):
        raise AssertionError(f"unexpected git call: {git_args}")

    result = run_sv(["list"], cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 0
    assert "No skill source repos configured" in result.stdout
    assert result.stderr == ""


def test_tty_first_run_prompt_can_add_configured_recommended_source(
    tmp_path: Path, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    class TtyStream:
        def __init__(self):
            self.text = ""

        def isatty(self):
            return True

        def write(self, text):
            self.text += text

        def flush(self):
            return None

    output = TtyStream()
    monkeypatch.setattr("sv.cli.recommended_sources", lambda: (
        RecommendedSource(repo=str(source), description="Local test source"),
    ))
    monkeypatch.setattr("sv.cli.sys.stdin", output)
    monkeypatch.setattr("sv.cli.sys.stdout", output)
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")

    exit_code = handle(parse_sv(["repo", "list"]), cwd=project, home=home)

    assert exit_code == 0
    assert "No skill source repos are configured" in output.text
    assert "Local test source" in output.text
    assert "Added repo" in output.text
    assert str(source) in output.text
    assert SvPaths.from_home(home).config_file.exists()

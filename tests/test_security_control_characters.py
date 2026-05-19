from io import StringIO
import re
from pathlib import Path
import subprocess

import pytest

from sv.agents import PiAdapter
from sv.catalog import SourceSkill
from sv.cli import (
    _handle_list,
    _handle_sync,
    _print_add_result,
    _print_remove_result,
    _print_repo_change_result,
    _print_sync_result,
    build_parser,
    handle,
)
from sv.config import RepoChangeResult, RepoConfig, SvConfig, SvPaths
from sv.manifest import ManifestEntry, save_manifest
import sv.source_backends.github as github_module
from sv.project import AddSkillResult, RemoveSkillResult, SyncResult, SyncSkip
from sv.selector import SelectionState, _render
from tests.helpers import assert_no_raw_control_characters


pytestmark = [pytest.mark.security]


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def parse(argv):
    return build_parser().parse_args(argv)


def _strip_ansi(value: str) -> str:
    return ANSI_ESCAPE_RE.sub("", value)


def test_handle_list_escapes_control_characters_in_table_output(
    tmp_path: Path, capsys
) -> None:
    catalog = [
        SourceSkill(
            name="alpha",
            description="Alpha description with control \x1b[2J sequence",
            repo_id="Repo\x1b[2J/Skills",
            repo_url="https://github.com/Repo/Skills.git",
            repo_path=tmp_path,
            source_path=tmp_path / "skills" / "alpha",
        )
    ]

    assert _handle_list(catalog) == 0

    output = capsys.readouterr().out
    assert_no_raw_control_characters(output)
    assert "Repo\\x1b[2J/Skills" in output
    assert "Alpha description with control \\x1b[2J sequence" in output


def test_handle_list_escapes_unicode_format_controls_in_table_output(
    tmp_path: Path, capsys
) -> None:
    catalog = [
        SourceSkill(
            name="alpha",
            description="Alpha description with bidi spoof \u202eSKILL.md",
            repo_id="Repo\u200d/Skills",
            repo_url="https://github.com/Repo/Skills.git",
            repo_path=tmp_path,
            source_path=tmp_path / "skills" / "alpha",
        )
    ]

    assert _handle_list(catalog) == 0

    output = capsys.readouterr().out
    assert "Repo\\u200d/Skills" in output
    assert "bidi spoof \\u202eSKILL.md" in output
    assert "\u200d" not in output
    assert "\u202e" not in output


def test_selector_label_escapes_control_characters_before_render() -> None:
    state = SelectionState(["alpha", "beta"])
    output = StringIO()

    _render(
        state,
        output,
        item_label=lambda item: f"{item}\x1b[2J",
    )

    rendered = _strip_ansi(output.getvalue())
    assert_no_raw_control_characters(rendered)
    assert "alpha\\x1b[2J" in rendered


def test_selector_label_escapes_unicode_format_controls_before_render() -> None:
    state = SelectionState(["alpha", "beta"])
    output = StringIO()

    _render(
        state,
        output,
        item_label=lambda item: f"{item}\u202e.md",
    )

    rendered = _strip_ansi(output.getvalue())
    assert "alpha\\u202e.md" in rendered
    assert "\u202e" not in rendered


def test_cli_error_output_escapes_control_characters_in_repo_id(
    tmp_path: Path, capsys
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(parse(["add", "bad\x1b[2J:alpha"]), cwd=project, home=home)

    assert exit_code == 1
    output = capsys.readouterr().err
    assert_no_raw_control_characters(output)
    assert "bad\\x1b[2J" in output


def test_print_add_result_escapes_unicode_format_controls_in_repo_id(
    tmp_path: Path, capsys
) -> None:
    _print_add_result(
        AddSkillResult(
            skill="alpha",
            target=tmp_path / ".pi" / "skills" / "alpha",
            status="exists",
            repo_id="Org/Skills",
            existing_repo_id="Bad\u202eRepo",
        )
    )

    output = capsys.readouterr().out
    assert "Bad\\u202eRepo" in output
    assert "\u202e" not in output


def test_print_repo_change_result_escapes_unicode_format_controls_in_repo_url(
    capsys,
) -> None:
    _print_repo_change_result(
        RepoChangeResult(
            repo=RepoConfig(
                id="Org/Skills",
                url="https://example.com/skills\u202e.git",
            ),
            status="added",
        )
    )

    output = capsys.readouterr().out
    assert "https://example.com/skills\\u202e.git" in output
    assert "\u202e" not in output


def test_print_add_result_escapes_high_plane_unicode_format_controls(
    tmp_path: Path, capsys
) -> None:
    _print_add_result(
        AddSkillResult(
            skill="alpha",
            target=tmp_path / ".pi" / "skills" / "alpha",
            status="exists",
            repo_id="Org/Skills",
            existing_repo_id="Bad\U000e0001Repo",
        )
    )

    output = capsys.readouterr().out
    assert "Bad\\U000e0001Repo" in output
    assert "\U000e0001" not in output


def test_repo_list_escapes_control_characters_in_repo_url(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text("schema_version = 1\nrepos = []\n")

    monkeypatch.setattr(
        "sv.cli.ConfigStore.load",
        lambda _store: SvConfig(
            repos=(RepoConfig(id="Org/Skills", url="https://example.com/Bad\x1b[2J.git"),)
        ),
    )

    exit_code = handle(parse(["repo", "list"]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert_no_raw_control_characters(output)
    assert "https://example.com/Bad\\x1b[2J.git" in output


def test_print_add_result_escapes_manifest_repo_id(tmp_path: Path, capsys) -> None:
    _print_add_result(
        AddSkillResult(
            skill="alpha",
            target=tmp_path / ".pi" / "skills" / "alpha",
            status="exists",
            repo_id="Org/Skills",
            existing_repo_id="Bad\x1b[2JRepo",
        )
    )

    output = capsys.readouterr().out
    assert_no_raw_control_characters(output)
    assert "Bad\\x1b[2JRepo" in output


def test_print_add_result_escapes_requested_repo_id_for_added_skill(tmp_path: Path, capsys) -> None:
    _print_add_result(
        AddSkillResult(
            skill="alpha",
            target=tmp_path / ".pi" / "skills" / "alpha",
            status="added",
            repo_id="Bad\x1b[2JRepo",
        )
    )

    output = capsys.readouterr().out
    assert_no_raw_control_characters(output)
    assert "Bad\\x1b[2JRepo" in output


def test_print_add_result_escapes_added_target_path(tmp_path: Path, capsys) -> None:
    target = tmp_path / "Bad\x1b[2JProject" / ".pi" / "skills" / "alpha"

    _print_add_result(
        AddSkillResult(
            skill="alpha",
            target=target,
            status="added",
            repo_id="Org/Skills",
        )
    )

    output = capsys.readouterr().out
    assert_no_raw_control_characters(output)
    assert "Bad\\x1b[2JProject" in output


def test_print_add_result_escapes_existing_target_path(tmp_path: Path, capsys) -> None:
    target = tmp_path / "Bad\x1b[2JProject" / ".pi" / "skills" / "alpha"

    _print_add_result(
        AddSkillResult(
            skill="alpha",
            target=target,
            status="exists",
            repo_id="Org/Skills",
            existing_repo_id="Org/Skills",
        )
    )

    output = capsys.readouterr().out
    assert_no_raw_control_characters(output)
    assert "Bad\\x1b[2JProject" in output


def test_print_remove_result_escapes_target_path(tmp_path: Path, capsys) -> None:
    target = tmp_path / "Bad\x1b[2JProject" / ".pi" / "skills" / "alpha"

    _print_remove_result(RemoveSkillResult(skill="alpha", target=target))

    output = capsys.readouterr().out
    assert_no_raw_control_characters(output)
    assert "Bad\\x1b[2JProject" in output


def test_handle_sync_prints_escaped_manifest_repo_ids(tmp_path: Path, capsys) -> None:
    project = tmp_path / "project"
    skills = project / ".pi" / "skills"
    local_skill = skills / "alpha"
    local_skill.mkdir(parents=True)
    (local_skill / "notes.md").write_text("local content\n")
    save_manifest(
        skills,
        {
            "alpha": ManifestEntry(
                name="alpha",
                repo_id="Bad\x1b[2JRepo",
                repo_url="https://github.com/Org/Skills.git",
                source_path="skills/alpha",
                description="Alpha skill.",
            )
        },
    )

    exit_code = _handle_sync([], cwd=project, adapter=PiAdapter())

    assert exit_code == 0
    output = capsys.readouterr().out
    assert_no_raw_control_characters(output)
    assert "Skipped local Pi skill 'alpha': recorded source is missing (Bad\\x1b[2JRepo)." in output


def test_print_sync_result_escapes_ambiguous_repo_ids(capsys) -> None:
    _print_sync_result(
        SyncResult(
            updated=[],
            skipped=[
                SyncSkip(
                    skill="alpha",
                    reason="ambiguous",
                    repo_ids=("GoodRepo", "Bad\x1b[2JRepo"),
                )
            ],
            backfilled=[],
        )
    )

    output = capsys.readouterr().out
    assert_no_raw_control_characters(output)
    assert "Skipped local Pi skill 'alpha': multiple source repos match (GoodRepo, Bad\\x1b[2JRepo)." in output


def test_source_git_errors_escape_stderr_control_characters(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        'schema_version = 1\n[[repos]]\n'
        'id = "HamdiMaz/Skills"\n'
        'url = "https://github.com/HamdiMaz/Skills.git"\n'
    )
    repo_path = paths.source_repo_for("HamdiMaz/Skills")
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / ".git").mkdir(parents=True)

    def fail_https_api(url, headers):
        raise OSError("offline")

    monkeypatch.setattr(github_module, "_default_github_http_get", fail_https_api)

    def git_runner(args, cwd=None):
        if args == ["git", "--version"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="git version 2.0\n"
            )
        if args == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stderr="unable to read from remote \x1b[2J\n",
            )
        if args[:2] == ["gh", "api"]:
            raise FileNotFoundError("gh")
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(parse(["list"]), cwd=project, home=home, git_runner=git_runner)

    assert exit_code == 1
    output = capsys.readouterr().err
    assert_no_raw_control_characters(output)
    assert "Reading source repo remote failed: unable to read from remote \\x1b[2J" in output


def test_source_git_errors_escape_stdout_control_characters(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        'schema_version = 1\n[[repos]]\n'
        'id = "HamdiMaz/Skills"\n'
        'url = "https://github.com/HamdiMaz/Skills.git"\n'
    )
    repo_path = paths.source_repo_for("HamdiMaz/Skills")
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / ".git").mkdir(parents=True)

    def fail_https_api(url, headers):
        raise OSError("offline")

    monkeypatch.setattr(github_module, "_default_github_http_get", fail_https_api)

    def git_runner(args, cwd=None):
        if args == ["git", "--version"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="git version 2.0\n"
            )
        if args == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="https://example.com/Bad\x1b[2J.git\n",
            )
        if args[:2] == ["gh", "api"]:
            raise FileNotFoundError("gh")
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(parse(["list"]), cwd=project, home=home, git_runner=git_runner)

    assert exit_code == 1
    output = capsys.readouterr().err
    assert_no_raw_control_characters(output)
    assert "Configured source repo is https://github.com/HamdiMaz/Skills.git, but existing source clone uses https://example.com/Bad\\x1b[2J.git." in output


def test_source_git_errors_escape_os_error_control_characters(
    tmp_path: Path, capsys
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        'schema_version = 1\n[[repos]]\n'
        'id = "HamdiMaz/Skills"\n'
        'url = "https://github.com/HamdiMaz/Skills.git"\n'
    )

    def git_runner(args, cwd=None):
        raise PermissionError("Git command denied\x1b[2J")

    exit_code = handle(parse(["list"]), cwd=project, home=home, git_runner=git_runner)

    assert exit_code == 1
    output = capsys.readouterr().err
    assert_no_raw_control_characters(output)
    assert "denied\\x1b[2J" in output

import pytest

from sv import cli as cli_module
from tests.helpers import parse_sv


@pytest.mark.parametrize(
    (
        "args",
        "required_phrases",
    ),
    [
        (
            ["--help"],
            [
                "project-local AI agent skills.",
                "source repos",
                "cache",
            ],
        ),
        (
            ["init", "--help"],
            [
                "Create a skill-vault repository scaffold",
                "Target folder",
            ],
        ),
        (
            ["index", "--help"],
            [
                "Scan this repo for valid skills",
                ".sv/index.toml",
            ],
        ),
        (
            ["search", "--help"],
            [
                "Search source skill names",
                "Search query",
            ],
        ),
        (
            ["status", "--help"],
            [
                "Show sv-managed skill status",
                "project, skill-vault, or global source context",
            ],
        ),
        (
            ["cache", "--help"],
            [
                "Inspect or clean the global sv cache",
                "status",
                "clean",
            ],
        ),
        (
            ["add", "--help"],
            [
                "repo:skill",
                "interactive list",
                "--all",
                "--repo",
                "skill-vault",
            ],
        ),
        (
            ["remove", "--help"],
            [
                "interactive list",
                "--all",
                "--yes",
                "sv-managed",
            ],
        ),
        (
            ["repo", "--help"],
            [
                "source repos",
                "-l",
                "repo list",
            ],
        ),
        (
            ["repo", "add", "--help"],
            [
                "GitHub owner/repo",
                "svx <repo>",
            ],
        ),
        (
            ["repo", "remove", "--help"],
            [
                "Repo ID",
                "interactive list",
                "--yes",
            ],
        ),
        (
            ["repo", "list", "--help"],
            [
                "usage: sv repo list",
            ],
        ),
        (
            ["run", "--help"],
            [
                "Arguments forwarded to pi after --.",
            ],
        ),
        (
            ["sync", "--help"],
            [
                "usage: sv sync",
            ],
        ),
        (
            ["update", "--help"],
            [
                "usage: sv update",
            ],
        ),
    ],
)
def test_cli_help_is_available(args, required_phrases, capsys):
    with pytest.raises(SystemExit) as exc_info:
        parse_sv(args)

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    for phrase in required_phrases:
        assert phrase in output


def test_removed_command_still_fails_to_parse(capsys):
    with pytest.raises(SystemExit) as exc_info:
        parse_sv(["config", "show"])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "invalid choice: 'config'" in error


def test_sv_add_repo_is_not_a_supported_alias(capsys):
    with pytest.raises(SystemExit) as exc_info:
        parse_sv(["add", "repo", "owner/repo"])

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "unrecognized arguments: owner/repo" in error


def test_svx_help_describes_repo_add_alias(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli_module.svx_main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "usage: svx" in output
    assert "Add a skill source repo" in output
    assert "alias for 'sv repo add'" in output
    assert "--skills-path" in output

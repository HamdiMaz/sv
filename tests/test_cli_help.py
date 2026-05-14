import pytest

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
            ],
        ),
        (
            ["add", "--help"],
            [
                "repo:skill",
                "interactive list",
            ],
        ),
        (
            ["remove", "--help"],
            [
                "interactive list",
            ],
        ),
        (
            ["repo", "--help"],
            [
                "source repos",
            ],
        ),
        (
            ["repo", "add", "--help"],
            [
                "GitHub owner/repo",
            ],
        ),
        (
            ["repo", "remove", "--help"],
            [
                "Repo ID",
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

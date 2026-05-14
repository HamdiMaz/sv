import pytest

from sv.cli import build_parser, handle
from sv.config import SvPaths, load_config
from sv.errors import SvError
from sv.project import normalize_skill_name

from tests.helpers import assert_no_raw_control_characters, assert_no_traceback


def parse(argv):
    return build_parser().parse_args(argv)


pytestmark = pytest.mark.security


INVALID_SKILL_NAMES = [
    "",
    "   ",
    ".",
    "..",
    ".alpha",
    "../alpha",
    "alpha/beta",
    r"alpha\\beta",
    "repo:skill",
    "bad\x1bname",
]


INVALID_REPO_IDS = [
    "../../outside",
    "Org//Repo",
    "Org/../Repo",
    "Org\\Repo",
    "Org/",
    "Org/Bad\x1bRepo",
]


def _toml_escape_repo_id(repo_id: str) -> str:
    parts: list[str] = []
    for char in repo_id:
        if char == "\\":
            parts.append("\\\\")
        elif ord(char) < 0x20:
            parts.append(f"\\u{ord(char):04x}")
        else:
            parts.append(char)
    return "".join(parts)


def test_normalize_skill_name_rejects_path_like_inputs() -> None:
    for skill_name in INVALID_SKILL_NAMES:
        with pytest.raises(SvError, match="Invalid skill name"):
            normalize_skill_name(skill_name)


@pytest.mark.parametrize("command,skill_name",
    [
        ("add", ""),
        ("add", "   "),
        ("add", "."),
        ("add", ".."),
        ("add", ".alpha"),
        ("add", "../alpha"),
        ("add", "alpha/beta"),
        ("add", r"alpha\\beta"),
        ("add", "bad\x1bname"),
        ("remove", ""),
        ("remove", "   "),
        ("remove", "."),
        ("remove", ".."),
        ("remove", ".alpha"),
        ("remove", "../alpha"),
        ("remove", "alpha/beta"),
        ("remove", r"alpha\\beta"),
        ("remove", "repo:skill"),
        ("remove", "bad\x1bname"),
    ],
)
def test_security_paths_reject_invalid_skill_inputs_without_filesystem_mutation(
    command: str, skill_name: str, tmp_path, capsys
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text("repos = []\n")
    config_before = paths.config_file.read_text()

    def failing_git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    result = handle(
        parse([command, skill_name]),
        cwd=project,
        home=home,
        git_runner=failing_git_runner,
    )
    captured = capsys.readouterr()

    assert result == 1
    assert "error:" in captured.err
    assert "Invalid skill name" in captured.err
    assert_no_traceback(captured.err)
    assert_no_raw_control_characters(captured.err)
    assert paths.config_file.read_text() == config_before
    assert not (project / ".pi").exists()


@pytest.mark.parametrize(
    ("repo_input", "expected_error"),
    [
        ("Org/..", "unsafe path components"),
        ("bad\x1brepo", "repo cannot contain control characters"),
    ],
)
def test_repo_add_rejects_invalid_repo_inputs_without_filesystem_mutation(
    repo_input: str, expected_error: str, tmp_path, capsys
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text("repos = []\n")
    config_before = paths.config_file.read_text()
    project_skills = project / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    sentinel = project_skills / "kept.txt"
    sentinel.write_text("do not touch\n")

    result = handle(parse(["repo", "add", repo_input]), cwd=project, home=home)
    captured = capsys.readouterr()

    assert result == 1
    assert "error:" in captured.err
    assert expected_error in captured.err
    assert_no_traceback(captured.err)
    assert_no_raw_control_characters(captured.err)
    assert paths.config_file.read_text() == config_before
    assert sentinel.read_text() == "do not touch\n"
    assert not paths.sources_dir.exists()


@pytest.mark.parametrize("repo_id", INVALID_REPO_IDS)
def test_load_config_rejects_invalid_repo_ids_and_preserves_file(tmp_path, repo_id: str) -> None:
    home = tmp_path / "home"
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    config_toml = (
        "[[repos]]\n"
        f'id = "{_toml_escape_repo_id(repo_id)}"\n'
        'url = "https://github.com/Org/Skills.git"\n'
    )
    paths.config_file.write_text(config_toml)

    with pytest.raises(SvError, match="sv config"):
        load_config(paths)

    assert paths.config_file.read_text() == config_toml


@pytest.mark.parametrize("repo_id", INVALID_REPO_IDS)
def test_load_failure_on_invalid_repo_ids_does_not_mutate_project_files(
    tmp_path, capsys, repo_id: str
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    project_skills = project / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    sentinel = project_skills / "kept.txt"
    sentinel.write_text("do not touch\n")

    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    config_toml = (
        "[[repos]]\n"
        f'id = "{_toml_escape_repo_id(repo_id)}"\n'
        'url = "https://github.com/Org/Skills.git"\n'
    )
    paths.config_file.write_text(config_toml)

    result = handle(parse(["list"]), cwd=project, home=home)
    captured = capsys.readouterr()

    assert result == 1
    assert "sv config" in captured.err
    assert "Traceback" not in captured.err
    assert (project / ".pi").is_dir()
    assert (project / ".pi" / "skills").is_dir()
    assert sentinel.read_text() == "do not touch\n"
    assert paths.config_file.read_text() == config_toml

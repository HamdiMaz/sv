from pathlib import Path
import subprocess

import pytest

from sv.cli import build_parser, handle
from sv.config import SvPaths, load_config
from sv.source import default_runner
from tests.helpers import (
    assert_no_raw_control_characters,
    assert_no_traceback,
    configure_source,
    make_source_repo,
    run_git,
    write_source_skill,
)


def parse(argv):
    return build_parser().parse_args(argv)


def _assert_validation_rejected(result, project: Path) -> None:
    assert result.exit_code == 1
    assert_no_raw_control_characters(result.stderr)
    assert_no_traceback(result.stderr)
    assert not (project / ".pi" / "skills").exists()


def _assert_existing_skill_unchanged(
    result, project: Path, skill_name: str, expected_content: str
) -> None:
    assert result.exit_code == 0
    assert_no_raw_control_characters(result.stdout)
    assert_no_traceback(result.stdout)
    target = project / ".pi" / "skills" / skill_name
    assert target.exists()
    assert (target / "notes.md").read_text() == expected_content


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


def test_add_invalid_skill_name_fails_without_git_or_project_skill_dir(
    tmp_path, run_sv
):
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


@pytest.mark.integration
def test_add_existing_skill_from_same_recorded_origin_reports_unchanged(
    tmp_path, run_sv
):
    source = make_source_repo(tmp_path, "source-a")
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    repo_id = load_config(SvPaths.from_home(home)).repos[0].id

    first = run_sv(
        ["add", f"{repo_id}:alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )
    assert first.exit_code == 0
    assert "Added Pi skill 'alpha'" in first.stdout

    target = project / ".pi" / "skills" / "alpha"
    original = (target / "notes.md").read_text()

    second = run_sv(
        ["add", f"{repo_id}:alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    _assert_existing_skill_unchanged(second, project, "alpha", original)
    assert (
        f"Pi skill 'alpha' already exists at {target} from {repo_id}." in second.stdout
    )


@pytest.mark.integration
def test_add_existing_skill_from_different_recorded_origin_prompts_remove(
    tmp_path, run_sv
):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha from b\n")
    run_git(["add", "skills/alpha"], source_b)
    run_git(["commit", "-m", "update alpha in b"], source_b)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    repo_ids = [repo.id for repo in load_config(SvPaths.from_home(home)).repos]

    first = run_sv(
        ["add", f"{repo_ids[0]}:alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )
    assert first.exit_code == 0

    target = project / ".pi" / "skills" / "alpha"
    original = (target / "notes.md").read_text()

    second = run_sv(
        ["add", f"{repo_ids[1]}:alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    _assert_existing_skill_unchanged(second, project, "alpha", original)
    assert (
        f"Pi skill 'alpha' already exists at {target} (currently from {repo_ids[0]}; "
        f"requested {repo_ids[1]}). Run 'sv remove alpha' first if you want to switch sources."
    ) in second.stdout


@pytest.mark.integration
def test_add_existing_skill_without_recorded_origin_asks_to_replace(tmp_path, run_sv):
    source = make_source_repo(tmp_path, "source-a")
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    repo_id = load_config(SvPaths.from_home(home)).repos[0].id

    target = project / ".pi" / "skills" / "alpha"
    target.parent.mkdir(parents=True)
    target.mkdir()
    (target / "notes.md").write_text("local custom\n")

    result = run_sv(
        ["add", f"{repo_id}:alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    _assert_existing_skill_unchanged(result, project, "alpha", "local custom\n")
    assert (
        f"Pi skill 'alpha' already exists at {target} (no sv origin recorded; requested {repo_id}). "
        "Run 'sv remove alpha' first if you want to replace it."
    ) in result.stdout


@pytest.mark.parametrize("add_args", [["add", "all"], ["add", "--all"]])
@pytest.mark.integration
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
    assert (project / ".pi" / "skills" / "beta" / "notes.md").read_text() == "beta v1\n"


@pytest.mark.integration
def test_add_interactive_adds_selected_skills(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()
    selector_calls = []

    def skill_selector(skills, **kwargs):
        selector_calls.append((list(skills), kwargs))
        return [skills[1]]

    exit_code = handle(
        parse(["add", "-l"]),
        cwd=project,
        home=home,
        skill_selector=skill_selector,
    )

    assert exit_code == 0
    assert selector_calls[0][0][0].name == "alpha"
    assert "item_label" in selector_calls[0][1]
    item_label = selector_calls[0][1]["item_label"]
    first_skill = selector_calls[0][0][0]
    label = item_label(first_skill)
    assert first_skill.repo_id in label
    assert f"{first_skill.repo_id}:alpha" not in label
    assert not (project / ".pi" / "skills" / "alpha").exists()
    assert (project / ".pi" / "skills" / "beta" / "notes.md").read_text() == "beta v1\n"
    assert "Added Pi skill 'beta'" in capsys.readouterr().out


@pytest.mark.integration
def test_add_interactive_reports_no_selection(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(
        parse(["add", "-l"]),
        cwd=project,
        home=home,
        skill_selector=lambda skills, **kwargs: [],
    )

    assert exit_code == 0
    assert "No skills selected." in capsys.readouterr().out


@pytest.mark.integration
def test_add_interactive_uses_cached_source_without_pull(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert handle(parse(["list"]), cwd=project, home=home) == 0
    capsys.readouterr()
    git_calls = []

    def git_runner(args, cwd=None):
        git_calls.append((list(args), cwd))
        if args == ["git", "--version"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="git version 2.0\n"
            )
        if args == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=f"{source}\n"
            )
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["add", "-l"]),
        cwd=project,
        home=home,
        git_runner=git_runner,
        skill_selector=lambda skills, **kwargs: [skills[0]],
    )

    assert exit_code == 0
    assert [call[0] for call in git_calls] == [
        ["git", "--version"],
        ["git", "remote", "get-url", "origin"],
    ]
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha v1\n"


@pytest.mark.integration
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
    assert (project / ".pi" / "skills" / "beta" / "notes.md").read_text() == "beta v1\n"


@pytest.mark.integration
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


@pytest.mark.integration
def test_add_qualified_skill_selects_repo_when_names_overlap(tmp_path: Path, capsys):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha from b\n")
    run_git(["add", "skills/alpha"], source_b)
    run_git(["commit", "-m", "update alpha in b"], source_b)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    capsys.readouterr()

    repo_id_b = load_config(SvPaths.from_home(home)).repos[1].id
    exit_code = handle(parse(["add", f"{repo_id_b}:alpha"]), cwd=project, home=home)

    assert exit_code == 0
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha from b\n"
    assert "Added Pi skill 'alpha'" in capsys.readouterr().out


@pytest.mark.integration
def test_add_duplicate_skill_uses_choice_callback(tmp_path: Path, capsys, monkeypatch):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha from b\n")
    run_git(["add", "skills/alpha"], source_b)
    run_git(["commit", "-m", "update alpha in b"], source_b)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    capsys.readouterr()
    monkeypatch.setenv("COLUMNS", "40")
    choices = []

    def choose_skill(matches):
        choices.append([(match.name, match.repo_id) for match in matches])
        return matches[1]

    exit_code = handle(
        parse(["add", "alpha"]),
        cwd=project,
        home=home,
        skill_chooser=choose_skill,
    )

    assert exit_code == 0
    assert len(choices) == 1
    assert choices[0][0][0] == "alpha"
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha from b\n"
    output = capsys.readouterr().out
    assert "Multiple source skills match 'alpha'" in output
    assert "Alpha from" in output
    assert "B." in output
    choice_table_output = output.split("Added Pi skill", maxsplit=1)[0]
    assert all(len(line) <= 40 for line in choice_table_output.splitlines())


@pytest.mark.integration
def test_add_duplicate_skill_without_tty_reports_error(tmp_path: Path, capsys):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha from b\n")
    run_git(["add", "skills/alpha"], source_b)
    run_git(["commit", "-m", "update alpha in b"], source_b)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["add", "alpha"]), cwd=project, home=home)

    assert exit_code == 1
    assert not (project / ".pi" / "skills" / "alpha").exists()
    error = capsys.readouterr().err
    assert "Multiple source skills match 'alpha'" in error
    assert "Use a qualified skill reference" in error


@pytest.mark.integration
def test_add_duplicate_skill_choice_table_does_not_repeat_skill_name(
    tmp_path: Path, capsys
):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha from b\n")
    run_git(["add", "skills/alpha"], source_b)
    run_git(["commit", "-m", "update alpha in b"], source_b)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    capsys.readouterr()

    exit_code = handle(
        parse(["add", "alpha"]),
        cwd=project,
        home=home,
        skill_chooser=lambda matches: matches[0],
    )

    assert exit_code == 0
    choice_output = capsys.readouterr().out.split("Added Pi skill", maxsplit=1)[0]
    assert "Skill" not in choice_output.splitlines()[1]
    table_lines = choice_output.splitlines()[3:]
    assert not any("  alpha  " in line for line in table_lines)


@pytest.mark.integration
def test_add_interactive_rejects_selected_duplicate_skill_names_without_copying(
    tmp_path: Path, capsys
):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha from b\n")
    run_git(["add", "skills/alpha"], source_b)
    run_git(["commit", "-m", "update alpha in b"], source_b)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    capsys.readouterr()

    def select_both_alpha(skills, **kwargs):
        return [skill for skill in skills if skill.name == "alpha"]

    exit_code = handle(
        parse(["add", "-l"]),
        cwd=project,
        home=home,
        skill_selector=select_both_alpha,
    )

    assert exit_code == 1
    assert not (project / ".pi" / "skills" / "alpha").exists()
    error = capsys.readouterr().err
    assert "Select only one source for duplicate skill 'alpha'" in error
    assert "repo:skill" in error


@pytest.mark.integration
def test_add_all_reports_duplicate_source_skills_without_copying(
    tmp_path: Path, capsys
):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha from b\n")
    run_git(["add", "skills/alpha"], source_b)
    run_git(["commit", "-m", "update alpha in b"], source_b)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["add", "--all"]), cwd=project, home=home)

    assert exit_code == 1
    assert not (project / ".pi" / "skills" / "alpha").exists()
    assert not (project / ".pi" / "skills" / "beta").exists()
    error = capsys.readouterr().err
    assert "Duplicate source skill 'alpha'" in error
    assert "Use qualified skill references" in error


@pytest.mark.integration
def test_invalid_skill_is_not_listed_or_added_by_all(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    invalid = source / "skills" / "invalid"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text("---\nname: other\ndescription: Bad.\n---\n")
    run_git(["add", "skills/invalid"], source)
    run_git(["commit", "-m", "add invalid skill"], source)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    assert handle(parse(["list"]), cwd=project, home=home) == 0
    assert "invalid" not in capsys.readouterr().out

    assert handle(parse(["add", "--all"]), cwd=project, home=home) == 0
    assert not (project / ".pi" / "skills" / "invalid").exists()

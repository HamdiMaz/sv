from pathlib import Path

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


def test_add_invalid_skill_name_fails_without_git_or_project_skill_dir(tmp_path, run_sv):
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


def test_add_existing_skill_from_same_recorded_origin_reports_unchanged(tmp_path, run_sv):
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
    assert f"Pi skill 'alpha' already exists at {target} from {repo_id}." in second.stdout


def test_add_existing_skill_from_different_recorded_origin_prompts_remove(tmp_path, run_sv):
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

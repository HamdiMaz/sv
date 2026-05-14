import pytest

from sv.config import SvPaths, load_config
from sv.source import default_runner
from tests.helpers import assert_no_traceback, make_source_repo, run_git, write_source_skill


pytestmark = pytest.mark.integration


def _assert_success(result) -> None:
    assert result.exit_code == 0
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)
    assert result.stderr == ""


def test_local_git_user_journey_smoke(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    repo_add = run_sv(
        ["repo", "add", str(source)],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    _assert_success(repo_add)
    assert "Added repo" in repo_add.stdout
    assert str(source) in repo_add.stdout
    configured_repo = load_config(SvPaths.from_home(home)).repos[0]

    repo_list = run_sv(["repo", "list"], cwd=project, home=home)

    _assert_success(repo_list)
    assert configured_repo.id in repo_list.stdout
    assert "URL" in repo_list.stdout
    assert "Cache" in repo_list.stdout

    list_result = run_sv(
        ["list"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    _assert_success(list_result)
    assert "alpha" in list_result.stdout
    assert "Alpha skill." in list_result.stdout
    assert "beta" in list_result.stdout
    assert "Beta skill." in list_result.stdout

    add_result = run_sv(
        ["add", "alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    _assert_success(add_result)
    assert "Added Pi skill 'alpha'" in add_result.stdout
    alpha_notes = project / ".pi" / "skills" / "alpha" / "notes.md"
    assert alpha_notes.read_text() == "alpha v1\n"
    assert not (project / ".pi" / "skills" / "beta").exists()

    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)

    sync_result = run_sv(
        ["sync"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    _assert_success(sync_result)
    assert "Synced Pi skill 'alpha'." in sync_result.stdout
    assert alpha_notes.read_text() == "alpha v2\n"

    remove_result = run_sv(["remove", "alpha"], cwd=project, home=home)

    _assert_success(remove_result)
    assert "Removed Pi skill 'alpha'" in remove_result.stdout
    assert not (project / ".pi" / "skills" / "alpha").exists()

    process_calls: list[list[str]] = []

    def process_runner(command: list[str]) -> int:
        process_calls.append(command)
        return 0

    run_result = run_sv(
        ["run", "--", "--help"],
        cwd=project,
        home=home,
        process_runner=process_runner,
    )

    _assert_success(run_result)
    assert process_calls == [["pi", "--no-skills", "--skill", ".pi/skills", "--help"]]

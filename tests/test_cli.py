from pathlib import Path

from sv.cli import build_parser, handle
from sv.config import SvPaths, load_config


def parse(argv):
    return build_parser().parse_args(argv)


def test_config_repo_writes_global_config(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(
        parse(["config", "repo", "HamdiMaz/Skills"]), cwd=project, home=home
    )

    assert exit_code == 0
    assert (
        load_config(SvPaths.from_home(home)).repo
        == "https://github.com/HamdiMaz/Skills.git"
    )
    assert (
        "Set source repo to https://github.com/HamdiMaz/Skills.git"
        in capsys.readouterr().out
    )


def test_config_repo_reports_empty_repo_value(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(parse(["config", "repo", "   "]), cwd=project, home=home)

    assert exit_code == 1
    assert "repo cannot be empty" in capsys.readouterr().err


def test_config_show_prints_effective_config(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(parse(["config", "show"]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "repo = https://github.com/HamdiMaz/Skills.git" in output
    assert f"source = {home / '.sv' / 'sources' / 'default' / 'repo'}" in output
    assert f"pi_skills = {project / '.pi' / 'skills'}" in output


def test_run_builds_isolated_pi_command_and_forwards_args(tmp_path: Path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    calls = []

    def process_runner(command):
        calls.append(command)
        return 23

    exit_code = handle(
        parse(["run", "--", "--model", "fast"]),
        cwd=project,
        home=home,
        process_runner=process_runner,
    )

    assert exit_code == 23
    assert calls == [["pi", "--no-skills", "--skill", ".pi/skills", "--model", "fast"]]


def test_run_reports_missing_pi_binary(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def process_runner(command):
        raise FileNotFoundError(command[0])

    exit_code = handle(
        parse(["run"]), cwd=project, home=home, process_runner=process_runner
    )

    assert exit_code == 1
    assert "Unable to run 'pi'" in capsys.readouterr().err


def test_add_interactive_with_skill_does_not_touch_source_repo(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["add", "alpha", "-l"]), cwd=project, home=home, git_runner=git_runner
    )

    assert exit_code == 1
    assert "Use -l by itself" in capsys.readouterr().err


def test_add_invalid_skill_name_does_not_touch_source_repo(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["add", "../alpha"]), cwd=project, home=home, git_runner=git_runner
    )

    assert exit_code == 1
    assert "Invalid skill name" in capsys.readouterr().err


def test_remove_interactive_removes_selected_project_skills_without_source_repo(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    (project_skills / "alpha").mkdir(parents=True)
    (project_skills / "alpha" / "notes.md").write_text("alpha\n")
    (project_skills / "beta").mkdir()
    (project_skills / "beta" / "notes.md").write_text("beta\n")
    selector_calls = []

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    def skill_selector(skills):
        selector_calls.append(skills)
        return ["beta"]

    exit_code = handle(
        parse(["remove", "-l"]),
        cwd=project,
        home=home,
        git_runner=git_runner,
        skill_selector=skill_selector,
    )

    assert exit_code == 0
    assert selector_calls == [["alpha", "beta"]]
    assert (project_skills / "alpha" / "notes.md").read_text() == "alpha\n"
    assert not (project_skills / "beta").exists()
    assert "Removed Pi skill 'beta'" in capsys.readouterr().out


def test_remove_interactive_reports_no_project_skills(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(parse(["remove", "-l"]), cwd=project, home=home)

    assert exit_code == 0
    assert "No Pi skills found to remove." in capsys.readouterr().out


def test_remove_skill_removes_project_skill_without_source_repo(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    skill = project / ".pi" / "skills" / "alpha"
    skill.mkdir(parents=True)

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["remove", "alpha"]), cwd=project, home=home, git_runner=git_runner
    )

    assert exit_code == 0
    assert not skill.exists()
    assert "Removed Pi skill 'alpha'" in capsys.readouterr().out


def test_remove_interactive_with_skill_does_not_touch_source_repo(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["remove", "alpha", "-l"]),
        cwd=project,
        home=home,
        git_runner=git_runner,
    )

    assert exit_code == 1
    assert "Use -l by itself" in capsys.readouterr().err

from pathlib import Path
import shutil
import subprocess

import pytest

from sv.cli import build_parser, handle
from sv.config import SvPaths, derive_repo_id, load_config


def parse(argv):
    return build_parser().parse_args(argv)


def run_git(args, cwd: Path):
    subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True)


def write_source_skill(source: Path, name: str, description: str, body: str) -> None:
    skill_dir = source / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"
    )
    (skill_dir / "notes.md").write_text(body)


def make_source_repo(tmp_path: Path, name: str = "skill-source") -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is required for integration tests")

    source = tmp_path / name
    write_source_skill(source, "alpha", "Alpha skill.", "alpha v1\n")
    write_source_skill(source, "beta", "Beta skill.", "beta v1\n")

    run_git(["init"], source)
    run_git(["config", "user.email", "tests@example.com"], source)
    run_git(["config", "user.name", "sv tests"], source)
    run_git(["add", "skills"], source)
    run_git(["commit", "-m", "initial skills"], source)
    return source


def configure_source(source: Path, project: Path, home: Path):
    exit_code = handle(parse(["repo", "add", str(source)]), cwd=project, home=home)
    assert exit_code == 0


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


def test_list_with_no_configured_repos_explains_how_to_add_one(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text("repos = []\n")

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["list"]), cwd=project, home=home, git_runner=git_runner
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "No skill source repos configured." in output
    assert "sv repo add" in output


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
    assert "Repo" in header
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


@pytest.mark.parametrize("add_args", [["add", "all"], ["add", "--all"]])
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


def test_add_skill_and_all_reports_error_without_touching_source(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["add", "alpha", "--all"]),
        cwd=project,
        home=home,
        git_runner=git_runner,
    )

    assert exit_code == 1
    assert "Use either a skill name or --all" in capsys.readouterr().err


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


def test_add_existing_skill_from_different_origin_explains_source_conflict(
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
    repo_ids = [repo.id for repo in load_config(SvPaths.from_home(home)).repos]
    assert handle(parse(["add", f"{repo_ids[0]}:alpha"]), cwd=project, home=home) == 0
    capsys.readouterr()

    exit_code = handle(parse(["add", f"{repo_ids[1]}:alpha"]), cwd=project, home=home)

    assert exit_code == 0
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha v1\n"
    output = capsys.readouterr().out
    assert f"currently from {repo_ids[0]}" in output
    assert f"requested {repo_ids[1]}" in output
    assert "sv remove alpha" in output


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


def test_list_table_fits_terminal_width(tmp_path: Path, capsys, monkeypatch):
    source = make_source_repo(tmp_path)
    write_source_skill(
        source,
        "alpha",
        "Alpha description with enough details to wrap cleanly in a narrow terminal.",
        "alpha v2\n",
    )
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "lengthen alpha description"], source)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()
    monkeypatch.setenv("COLUMNS", "40")

    exit_code = handle(parse(["list"]), cwd=project, home=home)

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert all(len(line) <= 40 for line in lines)


def test_list_and_add_coalesce_equivalent_repo_aliases(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\nid = "Org/Skills"\nurl = "https://github.com/Org/Skills"\n\n'
        '[[repos]]\nid = "Mirror/Skills"\nurl = "git@github.com:Org/Skills.git"\n'
    )
    repo_path = paths.source_repo_for("Org/Skills")
    (repo_path / ".git").mkdir(parents=True)
    write_source_skill(repo_path, "alpha", "Alpha skill.", "alpha v1\n")

    def git_runner(args, cwd=None):
        if args == ["git", "--version"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="git\n")
        if args == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="https://github.com/Org/Skills.git\n",
            )
        if args == ["git", "pull", "--ff-only"]:
            return subprocess.CompletedProcess(args=args, returncode=0)
        raise AssertionError(f"unexpected git call: {args}")

    assert handle(parse(["list"]), cwd=project, home=home, git_runner=git_runner) == 0
    list_output = capsys.readouterr().out
    assert list_output.count("alpha") == 1
    assert "Add as" not in list_output

    exit_code = handle(
        parse(["add", "Mirror/Skills:alpha"]),
        cwd=project,
        home=home,
        git_runner=git_runner,
    )

    assert exit_code == 0
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == (
        "alpha v1\n"
    )


def test_list_coalesces_repeated_repo_config_entries(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    repo_id = derive_repo_id(str(source))
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        "[[repos]]\n"
        f'id = "{repo_id}"\n'
        f'url = "{source}"\n'
        "\n"
        "[[repos]]\n"
        f'id = "{repo_id}"\n'
        f'url = "{source}"\n'
    )

    exit_code = handle(parse(["list"]), cwd=project, home=home)

    assert exit_code == 0
    rows = [line for line in capsys.readouterr().out.splitlines() if line.startswith("alpha  ")]
    assert len(rows) == 1


def test_list_wraps_duplicate_guidance_to_terminal_width(
    tmp_path: Path, capsys, monkeypatch
):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    capsys.readouterr()
    monkeypatch.setenv("COLUMNS", "40")

    exit_code = handle(parse(["list"]), cwd=project, home=home)

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("Tip: duplicate skill names") for line in lines)
    assert all(len(line) <= 40 for line in lines)


def test_list_shows_qualified_references_for_duplicate_skill_names(
    tmp_path: Path, capsys
):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    shutil.rmtree(source_b / "skills" / "beta")
    run_git(["rm", "-r", "skills/beta"], source_b)
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha from b\n")
    write_source_skill(source_b, "gamma", "Gamma from B.", "gamma from b\n")
    run_git(["add", "skills/alpha", "skills/gamma"], source_b)
    run_git(["commit", "-m", "update source b skills"], source_b)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    repo_ids = [repo.id for repo in load_config(SvPaths.from_home(home)).repos]
    capsys.readouterr()

    exit_code = handle(parse(["list"]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Add as" in output
    assert f"{repo_ids[0]}:alpha" in output
    assert f"{repo_ids[1]}:alpha" in output
    assert f"{repo_ids[0]}:beta" not in output
    assert f"{repo_ids[1]}:gamma" not in output
    assert "Tip: duplicate skill names are available" in output


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


def test_sync_with_no_pi_skills_dir_exits_successfully(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["sync"]), cwd=project, home=home)

    assert exit_code == 0
    assert "No Pi skills found to sync." in capsys.readouterr().out


def test_sync_uses_manifest_origin_when_multiple_repos_have_same_skill(
    tmp_path: Path, capsys
):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha b v1\n")
    run_git(["add", "skills/alpha"], source_b)
    run_git(["commit", "-m", "add alpha b"], source_b)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    repo_id_b = load_config(SvPaths.from_home(home)).repos[1].id
    assert handle(parse(["add", f"{repo_id_b}:alpha"]), cwd=project, home=home) == 0
    capsys.readouterr()

    write_source_skill(source_a, "alpha", "Alpha from A.", "alpha a v2\n")
    run_git(["add", "skills/alpha"], source_a)
    run_git(["commit", "-m", "update alpha a"], source_a)
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha b v2\n")
    run_git(["add", "skills/alpha"], source_b)
    run_git(["commit", "-m", "update alpha b"], source_b)

    exit_code = handle(parse(["sync"]), cwd=project, home=home)

    assert exit_code == 0
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha b v2\n"
    assert "Synced Pi skill 'alpha'." in capsys.readouterr().out


def test_sync_backfills_unique_legacy_skill(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    legacy = project / ".pi" / "skills" / "alpha"
    legacy.mkdir(parents=True)
    (legacy / "notes.md").write_text("legacy local\n")
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["sync"]), cwd=project, home=home)

    assert exit_code == 0
    assert (legacy / "notes.md").read_text() == "alpha v1\n"
    output = capsys.readouterr().out
    assert "Synced Pi skill 'alpha'." in output
    assert "Recorded origin for legacy Pi skill 'alpha'." in output


def test_sync_skips_ambiguous_legacy_skill(tmp_path: Path, capsys):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    home = tmp_path / "home"
    project = tmp_path / "project"
    legacy = project / ".pi" / "skills" / "alpha"
    legacy.mkdir(parents=True)
    (legacy / "notes.md").write_text("legacy local\n")
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["sync"]), cwd=project, home=home)

    assert exit_code == 0
    assert (legacy / "notes.md").read_text() == "legacy local\n"
    output = capsys.readouterr().out
    assert "Skipped local Pi skill 'alpha': multiple source repos match" in output


def test_update_pulls_sources_then_syncs_project_skills(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert handle(parse(["add", "alpha"]), cwd=project, home=home) == 0
    capsys.readouterr()

    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)

    exit_code = handle(parse(["update"]), cwd=project, home=home)

    assert exit_code == 0
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha v2\n"
    output = capsys.readouterr().out
    assert "Updating source repos..." in output
    assert "Syncing project skills..." in output
    assert "Synced Pi skill 'alpha'." in output

from pathlib import Path

from sv.config import SvPaths, load_config
from sv.source import default_runner
from tests.helpers import (
    assert_no_partial_sv_dirs,
    configure_source,
    make_source_repo,
    run_git,
    write_source_skill,
)


def test_list_shows_compact_duplicate_rows_with_qualified_references(
    tmp_path: Path, run_sv
):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")

    run_git(["rm", "-r", "skills/beta"], source_b)
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha from b\n")
    write_source_skill(source_b, "gamma", "Gamma skill.", "gamma from b\n")
    run_git(["add", "skills/alpha", "skills/gamma"], source_b)
    run_git(["commit", "-m", "replace beta with gamma"], source_b)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)

    output = run_sv(
        ["list"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    ).stdout

    main_section, _, duplicate_section = output.partition("Duplicate skill names:")
    assert main_section.strip()
    main_lines = [line for line in main_section.splitlines() if line.strip()]
    assert any("Skill" in line and "Source" in line for line in main_lines)
    assert any("Description" in line for line in main_lines)
    assert not any("Add as" in line for line in main_lines)

    alpha_rows = [line for line in main_lines if line.startswith("alpha")]
    assert len(alpha_rows) == 1
    assert "2 sources" in alpha_rows[0]
    assert "Choose a source below." in alpha_rows[0]

    assert "Duplicate skill names:" in output
    assert "beta" in output
    assert "gamma" in output

    duplicate_lines = [line for line in duplicate_section.splitlines() if line.strip()]
    header = next(line for line in duplicate_lines if line.startswith("Skill"))
    assert "Skill" in header
    assert "Repo" in header
    assert "Description" in header
    assert "Add as" in header

    duplicate_alpha_rows = [
        line for line in duplicate_lines if line.startswith("alpha")
    ]
    assert len(duplicate_alpha_rows) == 1
    assert "Alpha skill." in duplicate_section
    assert "Alpha from B." in duplicate_section
    assert ":alpha" in duplicate_section

    repo_ids = [repo.id for repo in load_config(SvPaths.from_home(home)).repos]
    for repo_id in repo_ids:
        assert f"{repo_id}:alpha" in duplicate_section


def test_add_alpha_without_tty_errors_with_qualified_guidance_and_copies_nothing(
    tmp_path: Path, run_sv
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

    result = run_sv(
        ["add", "alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    assert result.exit_code == 1
    assert "Multiple source skills match 'alpha'" in result.stderr
    assert "Use a qualified skill reference" in result.stderr
    assert not (project / ".pi" / "skills" / "alpha").exists()
    assert_no_partial_sv_dirs(project / ".pi" / "skills")


def test_add_alpha_with_chooser_callback_installs_selected_duplicate_source(
    tmp_path: Path, run_sv
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

    def choose_second(matches):
        return matches[1]

    result = run_sv(
        ["add", "alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
        skill_chooser=choose_second,
    )

    assert result.exit_code == 0
    assert_no_partial_sv_dirs(project / ".pi" / "skills")
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha from b\n"

    choice_output = result.stdout.split("Added Pi skill", maxsplit=1)[0]
    assert "Repo" in choice_output
    assert "Description" in choice_output
    assert "Add as" in choice_output
    table_header = next(
        line
        for line in choice_output.splitlines()
        if "Repo" in line and "Description" in line and "Add as" in line
    )
    assert "Skill" not in table_header
    assert not any("  alpha  " in line for line in choice_output.splitlines())

    for repo_id in repo_ids:
        assert f"{repo_id}:alpha" in choice_output


def test_add_qualified_alpha_installs_exact_source(
    tmp_path: Path, run_sv
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

    result = run_sv(
        ["add", f"{repo_ids[1]}:alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    assert result.exit_code == 0
    assert_no_partial_sv_dirs(project / ".pi" / "skills")
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha from b\n"
    assert f"from {repo_ids[1]}" in result.stdout
    assert "Multiple source skills match" not in result.stderr


def test_add_interactive_selector_rejects_duplicate_skills_without_copying(
    tmp_path: Path, run_sv
):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    run_git(["rm", "-r", "skills/beta"], source_b)
    write_source_skill(source_b, "gamma", "Gamma from B.", "gamma from b\n")
    run_git(["add", "skills/alpha", "skills/gamma"], source_b)
    run_git(["commit", "-m", "replace beta with gamma"], source_b)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)

    def select_alpha_and_beta(entries, **_kwargs):
        return [entry for entry in entries if entry.name in {"alpha", "beta"}]

    result = run_sv(
        ["add", "-l"],
        cwd=project,
        home=home,
        git_runner=default_runner,
        skill_selector=select_alpha_and_beta,
    )

    assert result.exit_code == 1
    assert "Select only one source for duplicate skill 'alpha'" in result.stderr
    assert "repo:skill" in result.stderr
    assert not (project / ".pi" / "skills" / "alpha").exists()
    assert not (project / ".pi" / "skills" / "beta").exists()
    assert not (project / ".pi" / "skills" / "gamma").exists()
    assert_no_partial_sv_dirs(project / ".pi" / "skills")


def test_add_all_rejects_duplicate_source_skills_without_copying(
    tmp_path: Path, run_sv
):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    run_git(["rm", "-r", "skills/beta"], source_b)
    write_source_skill(source_b, "gamma", "Gamma from B.", "gamma from b\n")
    run_git(["add", "skills/alpha", "skills/gamma"], source_b)
    run_git(["commit", "-m", "replace beta with gamma"], source_b)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)

    result = run_sv(
        ["add", "--all"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    assert result.exit_code == 1
    assert "Duplicate source skill 'alpha'" in result.stderr
    assert "Use qualified skill references" in result.stderr
    assert not (project / ".pi" / "skills" / "alpha").exists()
    assert not (project / ".pi" / "skills" / "beta").exists()
    assert not (project / ".pi" / "skills" / "gamma").exists()
    assert_no_partial_sv_dirs(project / ".pi" / "skills")

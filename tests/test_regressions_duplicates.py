from pathlib import Path
import sys

import pytest

from sv import cli as cli_module
from sv.config import SvPaths, load_config
from sv.source import default_runner
from tests.helpers import (
    assert_no_partial_sv_dirs,
    configure_source,
    make_source_repo,
    run_git,
    write_source_skill,
)


pytestmark = pytest.mark.integration


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


def test_add_same_repo_duplicates_without_tty_lists_paths_descriptions_and_scriptable_choices(
    tmp_path: Path, run_sv
):
    source = make_source_repo(tmp_path, "source-a")
    team_alpha = source / "team" / "skills" / "alpha"
    team_alpha.mkdir(parents=True)
    (team_alpha / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Team alpha.\n---\n\n# alpha\n"
    )
    (team_alpha / "notes.md").write_text("alpha from team\n")
    run_git(["add", "team/skills/alpha"], source)
    run_git(["commit", "-m", "add team alpha"], source)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    repo_id = load_config(SvPaths.from_home(home)).repos[0].id

    result = run_sv(
        ["add", "alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    assert result.exit_code == 1
    assert "Multiple source skills match 'alpha'" in result.stderr
    assert "Repo" in result.stderr
    assert "Path" in result.stderr
    assert "Description" in result.stderr
    assert "Add as" in result.stderr
    assert f"{repo_id}" in result.stderr
    assert "skills/alpha" in result.stderr
    assert "team/skills/alpha" in result.stderr
    assert "Alpha skill." in result.stderr
    assert "Team alpha." in result.stderr
    assert f"{repo_id}:alpha" in result.stderr
    assert f"{repo_id}:team/skills/alpha" in result.stderr
    assert not (project / ".pi" / "skills" / "alpha").exists()
    assert_no_partial_sv_dirs(project / ".pi" / "skills")


def test_add_same_repo_duplicates_without_tty_keeps_long_scriptable_refs_unwrapped(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path, "source-a")
    long_root = "team-with-a-very-long-name-that-forces-wrapping"
    long_alpha = source / long_root / "skills" / "alpha"
    long_alpha.mkdir(parents=True)
    (long_alpha / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Long path alpha.\n---\n\n# alpha\n"
    )
    (long_alpha / "notes.md").write_text("alpha from long path\n")
    run_git(["add", f"{long_root}/skills/alpha"], source)
    run_git(["commit", "-m", "add long path alpha"], source)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    repo_id = load_config(SvPaths.from_home(home)).repos[0].id
    monkeypatch.setenv("COLUMNS", "50")

    result = run_sv(
        ["add", "alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    long_reference = f"{repo_id}:{long_root}/skills/alpha"
    assert result.exit_code == 1
    assert long_reference in result.stderr
    assert any(long_reference in line for line in result.stderr.splitlines())
    assert not (project / ".pi" / "skills" / "alpha").exists()
    assert_no_partial_sv_dirs(project / ".pi" / "skills")


def test_add_same_repo_duplicates_tty_cancel_suggests_path_qualified_reference(
    tmp_path: Path, capsys, monkeypatch
):
    from sv.cli import build_parser, handle

    source = make_source_repo(tmp_path, "source-a")
    run_git(["rm", "-r", "skills"], source)
    for root, description in (
        ("team-a", "Team A alpha."),
        ("team-b", "Team B alpha."),
    ):
        skill_dir = source / root / "skills" / "alpha"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: alpha\ndescription: {description}\n---\n\n# alpha\n"
        )
        (skill_dir / "notes.md").write_text(f"alpha from {root}\n")
    run_git(["add", "-A"], source)
    run_git(["commit", "-m", "replace root skills with team alphas"], source)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    repo_id = load_config(SvPaths.from_home(home)).repos[0].id
    capsys.readouterr()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        cli_module,
        "select_skills",
        lambda matches, *, item_label: [],
    )

    exit_code = handle(
        build_parser().parse_args(["add", "alpha"]),
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert f"Rerun with {repo_id}:team-a/skills/alpha" in output
    assert f"Rerun with {repo_id}:alpha" not in output
    assert not (project / ".pi" / "skills" / "alpha").exists()
    assert_no_partial_sv_dirs(project / ".pi" / "skills")


def test_add_same_repo_duplicates_in_tty_shows_paths_and_accepts_checkbox_selection(
    tmp_path: Path, capsys, monkeypatch
):
    from sv.cli import build_parser, handle

    source = make_source_repo(tmp_path, "source-a")
    team_alpha = source / "team" / "skills" / "alpha"
    team_alpha.mkdir(parents=True)
    (team_alpha / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Team alpha.\n---\n\n# alpha\n"
    )
    (team_alpha / "notes.md").write_text("alpha from team\n")
    run_git(["add", "team/skills/alpha"], source)
    run_git(["commit", "-m", "add team alpha"], source)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    repo_id = load_config(SvPaths.from_home(home)).repos[0].id
    capsys.readouterr()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        cli_module,
        "select_skills",
        lambda matches, *, item_label: [matches[1]],
    )

    exit_code = handle(
        build_parser().parse_args(["add", "alpha"]),
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    chooser_output = output.split("Added Pi skill", maxsplit=1)[0]
    assert "Multiple source skills match 'alpha'" in chooser_output
    assert "Repo" in chooser_output
    assert "Path" in chooser_output
    assert "Description" in chooser_output
    assert "Add as" in chooser_output
    assert "skills/alpha" in chooser_output
    assert "team/skills/alpha" in chooser_output
    assert f"{repo_id}:alpha" in chooser_output
    assert f"{repo_id}:team/skills/alpha" in chooser_output
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha from team\n"


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

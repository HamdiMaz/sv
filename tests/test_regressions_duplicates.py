from pathlib import Path

from sv.config import SvPaths, load_config
from sv.source import default_runner
from tests.helpers import configure_source, run_git, write_source_skill, make_source_repo


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

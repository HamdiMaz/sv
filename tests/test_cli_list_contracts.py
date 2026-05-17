from pathlib import Path
import base64
import shutil
import subprocess

import pytest

from sv.cli import build_parser, handle
from sv.config import SvPaths, derive_repo_id, load_config
import sv.source as source_module
from sv.source import default_runner
from tests.helpers import (
    configure_source,
    display_width,
    make_source_repo,
    run_git,
    write_source_skill,
)


def parse(argv):
    return build_parser().parse_args(argv)


def test_list_uses_github_index_without_cloning_or_downloading_skill_folders(
    tmp_path: Path,
    run_sv,
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        'schema_version = 1\n[[repos]]\n'
        'id = "Org/Skills"\n'
        'url = "https://github.com/Org/Skills.git"\n'
    )
    index_text = """
    schema_version = 1
    kind = "skill-vault"
    generated_by = "sv"
    generated_at = "2026-05-15T00:00:00Z"

    [[skills]]
    name = "alpha"
    description = "Alpha from remote index."
    source_path = "skills/alpha"
    content_hash = "sha256:8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8"
    skill_file_hash = "sha256:63b6db06d8ca622e5986709498b492dfc53aad421a238dba44b7146d37d934bf"
    """
    calls: list[tuple[list[str], Path | None]] = []

    def git_runner(args, cwd=None):
        command = list(args)
        calls.append((command, cwd))
        if command == [
            "gh",
            "api",
            "/repos/Org/Skills/contents/.sv/index.toml",
            "--jq",
            ".content",
        ]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=base64.b64encode(index_text.encode("utf-8")).decode("ascii"),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    result = run_sv(["list"], cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 0
    assert "alpha" in result.stdout
    assert "Alpha from remote index." in result.stdout
    assert result.stderr == ""
    assert calls == [
        (
            [
                "gh",
                "api",
                "/repos/Org/Skills/contents/.sv/index.toml",
                "--jq",
                ".content",
            ],
            None,
        )
    ]


def test_list_reports_future_index_schema_even_when_another_repo_has_entries(
    tmp_path: Path,
    run_sv,
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        'schema_version = 1\n[[repos]]\n'
        'id = "Org/Skills"\n'
        'url = "https://github.com/Org/Skills.git"\n\n'
        '[[repos]]\n'
        'id = "Org/Future"\n'
        'url = "https://github.com/Org/Future.git"\n'
    )
    valid_index = """
    schema_version = 1
    kind = "skill-vault"
    generated_by = "sv"
    generated_at = "2026-05-15T00:00:00Z"

    [[skills]]
    name = "alpha"
    description = "Alpha from remote index."
    source_path = "skills/alpha"
    content_hash = "sha256:8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8"
    skill_file_hash = "sha256:63b6db06d8ca622e5986709498b492dfc53aad421a238dba44b7146d37d934bf"
    """
    future_index = """
    schema_version = 999
    kind = "skill-vault"
    generated_by = "future-sv"
    generated_at = "2026-05-15T00:00:00Z"
    """

    def git_runner(args, cwd=None):
        command = list(args)
        if command == [
            "gh",
            "api",
            "/repos/Org/Skills/contents/.sv/index.toml",
            "--jq",
            ".content",
        ]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=base64.b64encode(valid_index.encode("utf-8")).decode("ascii"),
                stderr="",
            )
        if command == [
            "gh",
            "api",
            "/repos/Org/Future/contents/.sv/index.toml",
            "--jq",
            ".content",
        ]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=base64.b64encode(future_index.encode("utf-8")).decode("ascii"),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    result = run_sv(["list"], cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 1
    assert "schema_version 999" in result.stderr
    assert "update sv" in result.stderr


def test_list_with_explicit_empty_repos_prints_next_step_and_skips_git(
    tmp_path: Path,
    run_sv,
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text("schema_version = 1\nrepos = []\n")

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    result = run_sv(["list"], cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 0
    assert (
        "No skill source repos configured. Add one with 'sv repo add <owner/repo>'."
        in result.stdout
    )
    assert result.stderr == ""


@pytest.mark.integration
def test_list_with_repo_having_no_valid_skills_prints_empty_message(
    tmp_path: Path, run_sv
):
    source = make_source_repo(tmp_path)
    run_git(["rm", "-r", "skills"], source)
    run_git(["commit", "-m", "remove all skills"], source)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    result = run_sv(["list"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "No valid skills found in configured source repos." in result.stdout
    assert "alpha" not in result.stdout
    assert "beta" not in result.stdout


@pytest.mark.integration
def test_list_skips_invalid_skills(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)

    invalid_skill = source / "skills" / "invalid"
    invalid_skill.mkdir()
    (invalid_skill / "notes.md").write_text("placeholder\n")
    run_git(["add", "skills/invalid"], source)
    run_git(["commit", "-m", "add invalid skill"], source)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    result = run_sv(["list"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    output = result.stdout
    assert "invalid" not in output
    assert "alpha" in output
    assert "beta" in output


@pytest.mark.integration
def test_list_fails_clearly_on_unreadable_skill_metadata(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)

    broken = source / "skills" / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_bytes(b"\xff\xfe\x00")
    run_git(["add", "skills/broken/SKILL.md"], source)
    run_git(["commit", "-m", "add unreadable skill"], source)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    result = run_sv(["list"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "broken" not in result.stdout
    assert "Failed to read SKILL.md for skill 'broken'" in result.stderr


@pytest.mark.parametrize("columns", [80, 40, 20, 5])
@pytest.mark.integration
def test_list_output_respects_terminal_width_when_wrapping(
    tmp_path: Path, run_sv, monkeypatch, columns: int
):
    source = make_source_repo(tmp_path, "wide-source")
    write_source_skill(
        source,
        "alpha",
        "Alpha description with Japanese symbols: 日本語 日本語 日本語 日本語 to keep output wrapping behavior explicit.",
        "alpha v1\n",
    )
    run_git(["add", "skills/alpha/SKILL.md"], source)
    run_git(["commit", "-m", "rewrite alpha metadata"], source)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    monkeypatch.setenv("COLUMNS", str(columns))
    result = run_sv(["list"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert all(display_width(line) <= columns for line in result.stdout.splitlines())


def test_list_and_add_coalesce_equivalent_repo_aliases(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        'schema_version = 1\n[[repos]]\nid = "Org/Skills"\nurl = "https://github.com/Org/Skills"\n\n'
        '[[repos]]\nid = "Mirror/Skills"\nurl = "git@github.com:Org/Skills.git"\n'
    )
    repo_path = paths.source_repo_for("Org/Skills")
    (repo_path / ".git").mkdir(parents=True)
    write_source_skill(repo_path, "alpha", "Alpha skill.", "alpha v1\n")

    def fail_https_api(url, headers):
        raise OSError("offline")

    monkeypatch.setattr(source_module, "_default_github_http_get", fail_https_api)

    def git_runner(args, cwd=None):
        if args == ["git", "--version"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="git\n")
        if args == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="https://github.com/Org/Skills.git\n",
            )
        if args[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(args=args, returncode=0)
        if args[:2] == ["git", "sparse-checkout"]:
            return subprocess.CompletedProcess(args=args, returncode=0)
        if args[:2] == ["git", "checkout"]:
            return subprocess.CompletedProcess(args=args, returncode=0)
        if args[:2] == ["gh", "api"]:
            raise FileNotFoundError("gh")
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


@pytest.mark.integration
def test_list_coalesces_repeated_repo_config_entries(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    repo_id = derive_repo_id(str(source))
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        "schema_version = 1\n[[repos]]\n"
        f'id = "{repo_id}"\n'
        f'url = "{source}"\n'
        "\n"
        "[[repos]]\n"
        f'id = "{repo_id}"\n'
        f'url = "{source}"\n'
    )

    exit_code = handle(parse(["list"]), cwd=project, home=home)

    assert exit_code == 0
    rows = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("alpha  ")
    ]
    assert len(rows) == 1


@pytest.mark.integration
def test_list_wraps_duplicate_guidance_to_terminal_width(
    tmp_path: Path, capsys, monkeypatch
):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "very-long-source-b-name")
    write_source_skill(
        source_b,
        "alpha",
        "Alpha 日本語 description from the second source with enough detail to wrap.",
        "alpha from b\n",
    )
    run_git(["add", "skills/alpha"], source_b)
    run_git(["commit", "-m", "lengthen duplicate alpha description"], source_b)
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
    assert any(line.startswith("Duplicate skill names:") for line in lines)
    assert any(line.startswith("Tip: use the exact") for line in lines)
    output = "\n".join(lines)
    assert "日本" in output
    assert "語" in output
    assert all(display_width(line) <= 40 for line in lines)


@pytest.mark.integration
def test_list_groups_duplicate_skill_names_in_main_table(tmp_path: Path, capsys):
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

    exit_code = handle(parse(["list"]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    main_section = output.split("\n\n", maxsplit=1)[0]
    main_alpha_rows = [
        line for line in main_section.splitlines() if line.startswith("alpha")
    ]
    assert len(main_alpha_rows) == 1
    assert "2 sources" in main_alpha_rows[0]
    assert "Choose a source below" in main_alpha_rows[0]
    assert "Alpha skill." not in main_section
    assert "Alpha from B." not in main_section
    duplicate_section = output.split("Duplicate skill names:", maxsplit=1)[1]
    assert "Description" in duplicate_section
    assert "Alpha skill." in duplicate_section
    assert "Alpha from B." in duplicate_section
    duplicate_alpha_rows = [
        line for line in duplicate_section.splitlines() if line.startswith("alpha")
    ]
    assert len(duplicate_alpha_rows) == 1


@pytest.mark.integration
def test_list_shows_skill_name_once_per_duplicate_group(tmp_path: Path, capsys):
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

    exit_code = handle(parse(["list"]), cwd=project, home=home)

    assert exit_code == 0
    duplicate_section = capsys.readouterr().out.split(
        "Duplicate skill names:", maxsplit=1
    )[1]
    duplicate_alpha_rows = [
        line for line in duplicate_section.splitlines() if line.startswith("alpha")
    ]
    assert len(duplicate_alpha_rows) == 1
    assert "Alpha skill." in duplicate_section
    assert "Alpha from B." in duplicate_section


@pytest.mark.integration
def test_add_accepts_path_qualified_reference_for_same_repo_duplicate(
    tmp_path: Path, run_sv
):
    source = make_source_repo(tmp_path)
    team_alpha = source / "team" / "skills" / "alpha"
    team_alpha.mkdir(parents=True)
    (team_alpha / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Team alpha.\n---\n\n# alpha\n"
    )
    (team_alpha / "notes.md").write_text("alpha from team path\n")
    run_git(["add", "team/skills/alpha"], source)
    run_git(["commit", "-m", "add team alpha"], source)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    repo_id = load_config(SvPaths.from_home(home)).repos[0].id

    result = run_sv(
        ["add", f"{repo_id}:team/skills/alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    assert result.exit_code == 0
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == (
        "alpha from team path\n"
    )


def test_list_shows_duplicate_references_without_widening_main_skill_table(
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
    sections = output.split("\n\n")
    assert sections[0].splitlines()[0].startswith("Skill")
    assert "Add as" not in sections[0]
    assert "Duplicate skill names:" in output
    assert "Add as" in output
    assert f"{repo_ids[0]}:alpha" in output
    assert f"{repo_ids[1]}:alpha" in output
    assert f"{repo_ids[0]}:beta" not in output
    assert f"{repo_ids[1]}:gamma" not in output
    assert "Tip: use the exact 'Add as' value" in output

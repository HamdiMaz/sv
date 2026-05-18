from dataclasses import replace
from io import StringIO
from pathlib import Path
import shutil
import sys

import pytest

from sv.catalog import SourceSkill
from sv import cli as cli_module
from sv.cli import _choose_skill, build_parser, handle
from sv.config import SvPaths, load_config
from sv.errors import SvError
from sv.hashing import sha256_skill_directory
from sv.manifest import load_manifest
from sv.source import default_runner
from sv.source_cache import load_cached_catalog, save_cached_catalog, _cached_catalog_hash
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


def _expire_cached_metadata(home: Path) -> None:
    paths = SvPaths.from_home(home)
    repo = load_config(paths).repos[0]
    document = load_cached_catalog(paths, repo)
    assert document is not None
    expired_without_hash = replace(
        document,
        catalog_hash="sha256:" + ("0" * 64),
        refreshed_at="2000-01-01T00:00:00Z",
    )
    save_cached_catalog(
        paths,
        repo,
        replace(expired_without_hash, catalog_hash=_cached_catalog_hash(expired_without_hash)),
    )


class _TtyStream(StringIO):
    def isatty(self):
        return True


def _source_skill(tmp_path: Path, name: str, repo_id: str, description: str) -> SourceSkill:
    repo_path = tmp_path / repo_id.replace("/", "-")
    source_path = repo_path / "skills" / name
    return SourceSkill(
        name=name,
        description=description,
        repo_id=repo_id,
        repo_url=f"https://example.com/{repo_id}.git",
        repo_path=repo_path,
        source_path=source_path,
    )


def test_add_list_picker_provides_structured_skill_source_description_columns(tmp_path):
    matches = [
        _source_skill(tmp_path, "alpha", "Org/Short", "First alpha."),
        _source_skill(tmp_path, "beta", "LongerOrg/Skills", "Second beta."),
    ]
    selector_calls = []

    def fake_select_skills(skills, **kwargs):
        selector_calls.append((skills, kwargs))
        return []

    exit_code = cli_module._handle_add_interactive(
        matches,
        tmp_path / "project",
        cli_module.PiAdapter(),
        fake_select_skills,
    )

    assert exit_code == 0
    assert selector_calls[0][0] == matches
    kwargs = selector_calls[0][1]
    assert kwargs["header_columns"] == ["Skill", "Source", "Description"]
    assert kwargs["item_columns"](matches[0]) == ["alpha", "Org/Short", "First alpha."]
    assert kwargs["item_columns"](matches[1]) == [
        "beta",
        "LongerOrg/Skills",
        "Second beta.",
    ]


def test_add_list_picker_keeps_one_argument_selector_compatibility(tmp_path):
    matches = [_source_skill(tmp_path, "alpha", "Org/Short", "First alpha.")]

    exit_code = cli_module._handle_add_interactive(
        matches,
        tmp_path / "project",
        cli_module.PiAdapter(),
        lambda skills: [],
    )

    assert exit_code == 0


def test_duplicate_source_chooser_uses_aligned_source_table(monkeypatch, tmp_path):
    matches = [
        _source_skill(tmp_path, "alpha", "Org/Short", "First alpha."),
        _source_skill(tmp_path, "alpha", "LongerOrg/Skills", "Second alpha."),
    ]
    selector_calls = []

    def fake_select_skills(skills, **kwargs):
        selector_calls.append((skills, kwargs))
        return [skills[1]]

    monkeypatch.setattr(sys, "stdin", _TtyStream())
    monkeypatch.setattr(sys, "stdout", _TtyStream())
    monkeypatch.setattr("sv.cli.select_skills", fake_select_skills)

    selected = _choose_skill(matches)

    assert selected == matches[1]
    assert selector_calls[0][0] == matches
    kwargs = selector_calls[0][1]
    header_label = kwargs["header_label"]
    item_label = kwargs["item_label"]
    first_label = item_label(matches[0])
    second_label = item_label(matches[1])
    assert header_label.startswith("Skill")
    assert header_label.index("Source") == first_label.index(matches[0].repo_id)
    assert header_label.index("Description") == first_label.index(matches[0].description)
    assert first_label.index(matches[0].description) == second_label.index(matches[1].description)


@pytest.mark.integration
def test_add_from_git_subdirectory_targets_repo_root(tmp_path, run_sv):
    source = make_source_repo(tmp_path, "source-a")
    home = tmp_path / "home"
    project = tmp_path / "project"
    subdir = project / "nested" / "work"
    subdir.mkdir(parents=True)
    run_git(["init"], project)
    configure_source(source, project, home)
    repo_id = load_config(SvPaths.from_home(home)).repos[0].id

    result = run_sv(
        ["add", f"{repo_id}:alpha"],
        cwd=subdir,
        home=home,
        git_runner=default_runner,
    )

    assert result.exit_code == 0
    assert (project / ".pi" / "skills" / "alpha" / "SKILL.md").exists()
    assert not (subdir / ".pi").exists()
    manifest = load_manifest(project / ".pi" / "skills")
    assert list(manifest) == ["alpha"]


def test_add_missing_skill_fails_without_git_or_project_skill_dir(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text("schema_version = 1\nrepos = []\n")

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


def test_add_invalid_qualified_repo_id_with_unicode_format_controls_fails_without_git_or_mutation(
    tmp_path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    result = run_sv(
        ["add", "bad\u202e:alpha"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
    )

    _assert_validation_rejected(result, project)
    assert "Invalid skill reference 'bad\\u202e:alpha'." in result.stderr
    assert "Repo id cannot contain Unicode format controls." in result.stderr


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
def test_add_existing_skill_from_different_path_same_repo_prompts_remove(
    tmp_path, run_sv
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

    first = run_sv(
        ["add", f"{repo_id}:alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )
    assert first.exit_code == 0

    target = project / ".pi" / "skills" / "alpha"
    original = (target / "notes.md").read_text()

    second = run_sv(
        ["add", f"{repo_id}:team/skills/alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    _assert_existing_skill_unchanged(second, project, "alpha", original)
    assert (
        f"Pi skill 'alpha' already exists at {target} (currently from {repo_id}; "
        f"requested {repo_id}:team/skills/alpha). Run 'sv remove alpha' first "
        "if you want to switch sources."
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


@pytest.mark.integration
def test_add_writes_canonical_project_manifest_without_legacy_manifest(
    tmp_path: Path, capsys
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    paths = SvPaths.from_home(home)
    repo_id = load_config(paths).repos[0].id
    capsys.readouterr()

    exit_code = handle(parse(["add", "alpha"]), cwd=project, home=home)

    assert exit_code == 0
    project_skills = project / ".pi" / "skills"
    installed = project_skills / "alpha"
    assert (installed / "notes.md").read_text() == "alpha v1\n"
    assert not (paths.source_repo_for(repo_id) / "skills" / "alpha" / "notes.md").exists()
    assert not (paths.source_repo_for(repo_id) / "skills" / "beta" / "notes.md").exists()
    assert (project / ".sv" / "manifest.toml").is_file()
    assert not (project_skills / ".sv-manifest.toml").exists()
    manifest = load_manifest(project_skills)
    assert sorted(manifest) == ["alpha"]
    assert manifest["alpha"].repo_id == repo_id
    assert manifest["alpha"].target_path == ".pi/skills/alpha"
    assert manifest["alpha"].source_content_hash == sha256_skill_directory(installed)
    assert manifest["alpha"].installed_content_hash == sha256_skill_directory(installed)
    assert manifest["alpha"].local_content_hash == sha256_skill_directory(installed)


@pytest.mark.integration
def test_add_validates_indexed_materialized_skill_before_mutating_project(
    tmp_path: Path, capsys
):
    source = make_source_repo(tmp_path)
    skill_file = source / "skills" / "alpha" / "SKILL.md"
    skill_file.write_text("---\nname: not-alpha\ndescription: Wrong.\n---\n")
    (source / ".sv").mkdir()
    (source / ".sv" / "index.toml").write_text(
        "schema_version = 1\n"
        'kind = "skill-vault"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "\n"
        "[[skills]]\n"
        'name = "alpha"\n'
        'description = "Indexed alpha."\n'
        'source_path = "skills/alpha"\n'
        'content_hash = "sha256:1bc04b5291c26a46d918139138b992d2de976d6851d0893b0476b85bfbdfc6e6"\n'
        'skill_file_hash = "sha256:85d1715969d51968062095f0adfde266871fb5d5306daf7b9427670c2492d097"\n'
    )
    run_git(["add", ".sv/index.toml", "skills/alpha/SKILL.md"], source)
    run_git(["commit", "-m", "add invalid indexed alpha"], source)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["add", "alpha"]), cwd=project, home=home)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "does not match folder" in captured.err
    assert not (project / ".pi" / "skills" / "alpha").exists()
    assert not (project / ".pi" / "skills" / ".alpha.sv-add-tmp").exists()
    assert not (project / ".sv" / "manifest.toml").exists()


@pytest.mark.integration
def test_add_all_adds_every_source_skill(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["add", "--all"]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Added Pi skill 'alpha'" in output
    assert "Added Pi skill 'beta'" in output
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha v1\n"
    assert (project / ".pi" / "skills" / "beta" / "notes.md").read_text() == "beta v1\n"


@pytest.mark.integration
def test_add_all_treats_all_as_a_literal_skill_name(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    write_source_skill(source, "all", "Literal all skill.", "literal all\n")
    run_git(["add", "skills/all"], source)
    run_git(["commit", "-m", "add literal all skill"], source)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["add", "all"]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Added Pi skill 'all'" in output
    assert (project / ".pi" / "skills" / "all" / "notes.md").read_text() == "literal all\n"
    assert not (project / ".pi" / "skills" / "alpha").exists()
    assert not (project / ".pi" / "skills" / "beta").exists()


@pytest.mark.integration
def test_add_all_missing_literal_all_reports_error_without_copying(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["add", "all"]), cwd=project, home=home)

    assert exit_code == 1
    assert "Skill 'all' was not found" in capsys.readouterr().err
    assert not (project / ".pi").exists()


@pytest.mark.integration
def test_add_all_repo_installs_every_skill_from_requested_source(tmp_path: Path, capsys):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha from b\n")
    write_source_skill(source_b, "beta", "Beta from B.", "beta from b\n")
    run_git(["add", "skills/alpha", "skills/beta"], source_b)
    run_git(["commit", "-m", "update b skills"], source_b)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    repo_id_b = load_config(SvPaths.from_home(home)).repos[1].id
    capsys.readouterr()

    exit_code = handle(parse(["add", "--all", "--repo", repo_id_b]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Added Pi skill 'alpha'" in output
    assert "Added Pi skill 'beta'" in output
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha from b\n"
    assert (project / ".pi" / "skills" / "beta" / "notes.md").read_text() == "beta from b\n"


@pytest.mark.integration
def test_add_all_repo_reports_duplicate_skills_in_requested_source_before_copying(
    tmp_path: Path, capsys
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
    capsys.readouterr()

    exit_code = handle(parse(["add", "--all", "--repo", repo_id]), cwd=project, home=home)

    assert exit_code == 1
    assert not (project / ".pi").exists()
    error = capsys.readouterr().err
    assert "Duplicate source skill 'alpha'" in error
    assert f"{repo_id}:alpha" in error
    assert f"{repo_id}:team/skills/alpha" in error


@pytest.mark.integration
def test_add_all_repo_resolves_duplicate_skills_in_requested_source_before_copying(
    tmp_path: Path, capsys, monkeypatch
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
    selector_calls = []
    capsys.readouterr()

    def select_team_alpha(matches, **kwargs):
        selector_calls.append((matches, kwargs))
        assert not (project / ".pi").exists()
        return [
            next(
                match
                for match in matches
                if match.source_relative_path == "team/skills/alpha"
            )
        ]

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli_module, "select_skills", select_team_alpha)

    exit_code = handle(
        parse(["add", "--all", "--repo", repo_id]),
        cwd=project,
        home=home,
    )

    assert exit_code == 0
    selected_matches, selector_kwargs = selector_calls[0]
    assert [(match.repo_id, match.source_relative_path) for match in selected_matches] == [
        (repo_id, "skills/alpha"),
        (repo_id, "team/skills/alpha"),
    ]
    assert selector_kwargs["header_columns"] == ["Skill", "Source", "Description"]
    assert selector_kwargs["item_columns"](selected_matches[1]) == [
        "alpha",
        f"{repo_id}:team/skills/alpha",
        "Team alpha.",
    ]
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha from team\n"
    output = capsys.readouterr().out
    assert "Multiple selected sources provide 'alpha'" in output
    assert f"{repo_id}:team/skills/alpha" in output
    assert "Added Pi skill 'alpha'" in output


@pytest.mark.integration
def test_add_all_repo_unknown_source_fails_without_copying(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["add", "--all", "--repo", "missing/repo"]), cwd=project, home=home)

    assert exit_code == 1
    assert "Repo 'missing/repo' was not found" in capsys.readouterr().err
    assert not (project / ".pi").exists()


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
    assert "header_label" in selector_calls[0][1]
    item_label = selector_calls[0][1]["item_label"]
    header_label = selector_calls[0][1]["header_label"]
    first_skill = selector_calls[0][0][0]
    label = item_label(first_skill)
    assert header_label.startswith("Skill  Source")
    assert header_label.endswith("Description")
    assert first_skill.repo_id in label
    assert label.index(first_skill.repo_id) == header_label.index("Source")
    assert label.index(first_skill.description) == header_label.index("Description")
    assert f"{first_skill.repo_id}:alpha" not in label
    assert not (project / ".pi" / "skills" / "alpha").exists()
    assert (project / ".pi" / "skills" / "beta" / "notes.md").read_text() == "beta v1\n"
    assert "Added Pi skill 'beta'" in capsys.readouterr().out


@pytest.mark.integration
def test_add_interactive_requires_tty_without_mutating_project(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    result = run_sv(
        ["add", "-l"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Interactive skill selection requires a TTY." in result.stderr
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)
    assert not (project / ".pi").exists()


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
        if args[:2] in (["git", "fetch"], ["git", "pull"]):
            raise AssertionError(f"unexpected source refresh: {args}")
        return default_runner(args, cwd)

    selected = []

    def select_first(skills, **kwargs):
        selected.append(skills[0].name)
        return [skills[0]]

    exit_code = handle(
        parse(["add", "-l"]),
        cwd=project,
        home=home,
        git_runner=git_runner,
        skill_selector=select_first,
    )

    assert exit_code == 0
    assert not any(call[0][:2] in (["git", "fetch"], ["git", "pull"]) for call in git_calls)
    assert (
        project / ".pi" / "skills" / selected[0] / "notes.md"
    ).read_text() == f"{selected[0]} v1\n"


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
def test_add_interactive_resolves_selected_duplicate_skill_names_before_copying(
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
    repo_id_a, repo_id_b = repo_ids
    capsys.readouterr()
    chooser_calls = []

    def select_beta_and_both_alpha(skills, **kwargs):
        selected = []
        beta_selected = False
        for skill in skills:
            if skill.name == "alpha":
                selected.append(skill)
            elif skill.name == "beta" and not beta_selected:
                selected.append(skill)
                beta_selected = True
        return selected

    def choose_b_alpha(matches):
        chooser_calls.append([(match.name, match.repo_id) for match in matches])
        assert not (project / ".pi").exists()
        return next(match for match in matches if match.repo_id == repo_id_b)

    exit_code = handle(
        parse(["add", "-l"]),
        cwd=project,
        home=home,
        skill_selector=select_beta_and_both_alpha,
        skill_chooser=choose_b_alpha,
    )

    assert exit_code == 0
    assert chooser_calls == [[("alpha", repo_id_a), ("alpha", repo_id_b)]]
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha from b\n"
    assert (project / ".pi" / "skills" / "beta" / "notes.md").read_text() == "beta v1\n"
    output = capsys.readouterr().out
    assert "Multiple selected sources provide 'alpha'" in output
    assert "Added Pi skill 'alpha'" in output
    assert "Added Pi skill 'beta'" in output


@pytest.mark.integration
def test_add_interactive_duplicate_resolution_cancel_does_not_copy_any_selection(
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

    def select_beta_and_both_alpha(skills, **kwargs):
        selected = []
        beta_selected = False
        for skill in skills:
            if skill.name == "alpha":
                selected.append(skill)
            elif skill.name == "beta" and not beta_selected:
                selected.append(skill)
                beta_selected = True
        return selected

    def cancel_duplicate_choice(matches):
        assert [match.name for match in matches] == ["alpha", "alpha"]
        assert not (project / ".pi").exists()
        return None

    exit_code = handle(
        parse(["add", "-l"]),
        cwd=project,
        home=home,
        skill_selector=select_beta_and_both_alpha,
        skill_chooser=cancel_duplicate_choice,
    )

    assert exit_code == 0
    assert not (project / ".pi").exists()
    assert "No skill selected for duplicate 'alpha'." in capsys.readouterr().out


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
def test_add_all_resolves_duplicate_source_skills_before_copying(
    tmp_path: Path, capsys
):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    run_git(["rm", "-r", "skills/beta"], source_b)
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha from b\n")
    run_git(["add", "-A"], source_b)
    run_git(["commit", "-m", "keep only alpha from b"], source_b)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    repo_ids = [repo.id for repo in load_config(SvPaths.from_home(home)).repos]
    repo_id_a, repo_id_b = repo_ids
    capsys.readouterr()
    chooser_calls = []

    def choose_b_alpha(matches):
        chooser_calls.append([(match.name, match.repo_id) for match in matches])
        assert not (project / ".pi").exists()
        return next(match for match in matches if match.repo_id == repo_id_b)

    exit_code = handle(
        parse(["add", "--all"]),
        cwd=project,
        home=home,
        skill_chooser=choose_b_alpha,
    )

    assert exit_code == 0
    assert chooser_calls == [[("alpha", repo_id_a), ("alpha", repo_id_b)]]
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha from b\n"
    assert (
        project / ".pi" / "skills" / "beta" / "notes.md"
    ).read_text() == "beta v1\n"
    output = capsys.readouterr().out
    assert "Multiple selected sources provide 'alpha'" in output
    assert "Added Pi skill 'alpha'" in output
    assert "Added Pi skill 'beta'" in output


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


def test_add_fails_closed_when_cached_metadata_writeback_is_unsafe(
    tmp_path: Path, run_sv, monkeypatch: pytest.MonkeyPatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    def fail_writeback(*_args, **_kwargs) -> None:
        raise SvError("unsupported path in cached metadata writeback")

    monkeypatch.setattr(cli_module, "record_cached_skill_body_hash", fail_writeback)

    result = run_sv(["add", "alpha"], cwd=project, home=home)

    assert result.exit_code == 1
    assert "unsupported path in cached metadata writeback" in result.stderr
    assert not (project / ".pi" / "skills" / "alpha").exists()


def test_add_uses_cached_metadata_and_cached_skill_body_when_source_unavailable(
    tmp_path: Path, run_sv
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    assert run_sv(["add", "alpha"], cwd=project, home=home).exit_code == 0
    shutil.rmtree(project / ".pi" / "skills" / "alpha")
    shutil.rmtree(source / ".git")

    def fail_git(args, cwd=None):
        raise AssertionError(f"--cached must not call Git or GitHub backends: {args}")

    result = run_sv(["add", "alpha", "--cached"], cwd=project, home=home, git_runner=fail_git)

    assert result.exit_code == 0
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == "alpha v1\n"


def test_add_cached_fails_without_cached_skill_body_and_does_not_call_source(
    tmp_path: Path, run_sv
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert run_sv(["list"], cwd=project, home=home).exit_code == 0
    shutil.rmtree(source / ".git")

    def fail_git(args, cwd=None):
        raise AssertionError(f"--cached must not call Git or GitHub backends: {args}")

    result = run_sv(["add", "alpha", "--cached"], cwd=project, home=home, git_runner=fail_git)

    assert result.exit_code == 1
    assert "cached skill body" in result.stderr.lower()


def test_add_warns_and_uses_stale_cached_metadata_when_refresh_fails(
    tmp_path: Path, run_sv
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    assert run_sv(["add", "alpha"], cwd=project, home=home).exit_code == 0
    shutil.rmtree(project / ".pi" / "skills" / "alpha")
    _expire_cached_metadata(home)
    shutil.rmtree(source / ".git")

    result = run_sv(["add", "alpha"], cwd=project, home=home)

    assert result.exit_code == 0
    assert "using stale cached metadata" in result.stderr.lower()
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == "alpha v1\n"


def test_add_all_warns_and_uses_stale_cached_metadata_when_refresh_fails(
    tmp_path: Path, run_sv
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    assert run_sv(["add", "--all"], cwd=project, home=home).exit_code == 0
    shutil.rmtree(project / ".pi" / "skills")
    _expire_cached_metadata(home)
    shutil.rmtree(source / ".git")

    result = run_sv(["add", "--all"], cwd=project, home=home)

    assert result.exit_code == 0
    assert "using stale cached metadata" in result.stderr.lower()
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == "alpha v1\n"

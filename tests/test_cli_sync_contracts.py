from sv.config import SvPaths, load_config
from sv.manifest import ManifestEntry, load_manifest, save_manifest
from sv.source import default_runner
from tests.helpers import (
    assert_no_partial_sv_dirs,
    assert_no_raw_control_characters,
    assert_no_traceback,
    configure_source,
    make_source_repo,
    run_git,
    write_source_skill,
)


def _assert_sync_success(result) -> None:
    assert result.exit_code == 0
    assert_no_raw_control_characters(result.stdout)
    assert_no_traceback(result.stdout)


def test_sync_with_no_pi_skills_dir_reports_no_skills_to_sync(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    result = run_sv(["sync"], cwd=project, home=home, git_runner=default_runner)

    _assert_sync_success(result)
    assert "No Pi skills found to sync." in result.stdout


def test_sync_updates_managed_skill_with_recorded_origin_when_duplicates_exist(tmp_path, run_sv):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha b v1\n")
    run_git(["add", "skills/alpha"], source_b)
    run_git(["commit", "-m", "update alpha b v1"], source_b)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    repo_ids = [repo.id for repo in load_config(SvPaths.from_home(home)).repos]
    assert repo_ids, "expected two configured repos"

    add_result = run_sv(
        ["add", f"{repo_ids[1]}:alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )
    _assert_sync_success(add_result)
    assert "Added Pi skill 'alpha'" in add_result.stdout

    write_source_skill(source_a, "alpha", "Alpha from A.", "alpha a v2\n")
    run_git(["add", "skills/alpha"], source_a)
    run_git(["commit", "-m", "update alpha a v2"], source_a)
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha b v2\n")
    run_git(["add", "skills/alpha"], source_b)
    run_git(["commit", "-m", "update alpha b v2"], source_b)

    result = run_sv(["sync"], cwd=project, home=home, git_runner=default_runner)
    _assert_sync_success(result)

    managed = project / ".pi" / "skills" / "alpha"
    assert (managed / "notes.md").read_text() == "alpha b v2\n"
    assert "Synced Pi skill 'alpha'." in result.stdout
    assert load_manifest(managed.parent)["alpha"].repo_id == repo_ids[1]


def test_sync_skips_local_skill_with_missing_recorded_source(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    project_skills = project / ".pi" / "skills"
    local = project_skills / "alpha"
    local.mkdir(parents=True)
    (local / "notes.md").write_text("local alpha\n")
    save_manifest(
        project_skills,
        {
            "alpha": ManifestEntry(
                name="alpha",
                repo_id="Other/Skills",
                repo_url="https://github.com/Other/Skills.git",
                source_path="skills/alpha",
                description="Alpha skill.",
            )
        },
    )

    result = run_sv(["sync"], cwd=project, home=home, git_runner=default_runner)
    _assert_sync_success(result)
    assert "No matching Pi skills found to sync." in result.stdout
    assert (
        "Skipped local Pi skill 'alpha': recorded source is missing (Other/Skills)."
        in result.stdout
    )
    assert (local / "notes.md").read_text() == "local alpha\n"
    assert_no_partial_sv_dirs(project_skills)


def test_sync_backfills_unique_legacy_skill(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    legacy = project_skills / "alpha"
    legacy.mkdir(parents=True)
    (legacy / "notes.md").write_text("legacy local\n")
    configure_source(source, project, home)

    result = run_sv(["sync"], cwd=project, home=home, git_runner=default_runner)
    _assert_sync_success(result)
    assert (legacy / "notes.md").read_text() == "alpha v1\n"
    assert "Synced Pi skill 'alpha'." in result.stdout
    assert "Recorded origin for legacy Pi skill 'alpha'." in result.stdout
    manifest = load_manifest(project_skills)
    repo_id = load_config(SvPaths.from_home(home)).repos[0].id
    assert manifest["alpha"].repo_id == repo_id


def test_sync_skips_ambiguous_legacy_skill(tmp_path, run_sv):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    home = tmp_path / "home"
    project = tmp_path / "project"
    legacy = project / ".pi" / "skills" / "alpha"
    legacy.mkdir(parents=True)
    (legacy / "notes.md").write_text("legacy local\n")
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    project_skills = project / ".pi" / "skills"

    result = run_sv(["sync"], cwd=project, home=home, git_runner=default_runner)
    _assert_sync_success(result)
    assert "No matching Pi skills found to sync." in result.stdout
    assert "Skipped local Pi skill 'alpha': multiple source repos match" in result.stdout
    assert (legacy / "notes.md").read_text() == "legacy local\n"
    assert_no_partial_sv_dirs(project_skills)


def test_sync_skips_local_only_skill(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    local_only = project_skills / "local_only"
    local_only.mkdir(parents=True)
    (local_only / "notes.md").write_text("local only\n")
    configure_source(source, project, home)

    result = run_sv(["sync"], cwd=project, home=home, git_runner=default_runner)
    _assert_sync_success(result)
    assert "No matching Pi skills found to sync." in result.stdout
    assert "Skipped local Pi skill 'local_only'." in result.stdout
    assert (local_only / "notes.md").read_text() == "local only\n"
    assert_no_partial_sv_dirs(project_skills)

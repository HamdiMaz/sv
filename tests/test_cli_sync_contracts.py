import subprocess

import pytest

from sv.config import SvPaths, load_config
from sv.hashing import sha256_skill_directory
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


pytestmark = pytest.mark.integration


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


def test_sync_replaces_locally_modified_managed_skill_and_clears_state(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    add_result = run_sv(
        ["add", "alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )
    _assert_sync_success(add_result)

    managed = project / ".pi" / "skills" / "alpha"
    (managed / "notes.md").write_text("alpha local edit\n")
    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)

    update_result = run_sv(["update"], cwd=project, home=home, git_runner=default_runner)
    _assert_sync_success(update_result)
    pre_sync_entry = load_manifest(managed.parent)["alpha"]
    assert pre_sync_entry.modified is True
    assert pre_sync_entry.update_available is True

    result = run_sv(["sync"], cwd=project, home=home, git_runner=default_runner)

    _assert_sync_success(result)
    assert "Synced Pi skill 'alpha'." in result.stdout
    assert (managed / "notes.md").read_text() == "alpha v2\n"
    synced_hash = sha256_skill_directory(managed, expected_name="alpha")
    manifest_entry = load_manifest(managed.parent)["alpha"]
    assert manifest_entry.source_content_hash == synced_hash
    assert manifest_entry.installed_content_hash == synced_hash
    assert manifest_entry.local_content_hash == synced_hash
    assert manifest_entry.modified is False
    assert manifest_entry.update_available is False
    assert_no_partial_sv_dirs(managed.parent)


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
        "Skipped local Pi skill 'alpha': recorded source is missing (Other/Skills). orphan state recorded."
        in result.stdout
    )
    assert (local / "notes.md").read_text() == "local alpha\n"
    manifest_entry = load_manifest(project_skills)["alpha"]
    assert manifest_entry.orphan is True
    assert manifest_entry.local_content_hash == sha256_skill_directory(
        local, expected_name="alpha"
    )
    assert_no_partial_sv_dirs(project_skills)


def test_sync_marks_missing_source_orphan_and_reattaches_when_it_reappears(
    tmp_path, run_sv
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    add_result = run_sv(
        ["add", "alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )
    _assert_sync_success(add_result)
    project_skills = project / ".pi" / "skills"
    local = project_skills / "alpha"

    run_git(["rm", "-r", "skills/alpha"], source)
    run_git(["commit", "-m", "remove alpha"], source)

    orphan_result = run_sv(["sync"], cwd=project, home=home, git_runner=default_runner)

    _assert_sync_success(orphan_result)
    assert "No matching Pi skills found to sync." in orphan_result.stdout
    assert "orphan state recorded" in orphan_result.stdout
    assert (local / "notes.md").read_text() == "alpha v1\n"
    assert load_manifest(project_skills)["alpha"].orphan is True

    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "restore alpha"], source)

    reattach_result = run_sv(
        ["sync"], cwd=project, home=home, git_runner=default_runner
    )

    _assert_sync_success(reattach_result)
    assert "Synced Pi skill 'alpha'." in reattach_result.stdout
    assert (local / "notes.md").read_text() == "alpha v2\n"
    manifest_entry = load_manifest(project_skills)["alpha"]
    assert manifest_entry.orphan is False
    assert manifest_entry.modified is False
    assert manifest_entry.update_available is False
    assert_no_partial_sv_dirs(project_skills)


def test_sync_aborts_on_partial_source_refresh_failure_without_marking_orphan(
    tmp_path, run_sv
):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)

    add_result = run_sv(
        ["add", "alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )
    _assert_sync_success(add_result)
    configure_source(source_b, project, home)

    project_skills = project / ".pi" / "skills"
    project_skill = project_skills / "alpha" / "notes.md"

    def git_runner(args, cwd=None):
        if args[:2] == ["git", "fetch"] and cwd is not None and "source-a" in str(cwd):
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stderr="source-a fetch failed\n",
            )
        return default_runner(args, cwd)

    result = run_sv(["sync"], cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 1
    assert "source-a fetch failed" in result.stderr
    assert "Synced Pi skill 'alpha'." not in result.stdout
    assert project_skill.read_text() == "alpha v1\n"
    assert load_manifest(project_skills)["alpha"].orphan is False
    assert_no_traceback(result.stdout)
    assert_no_raw_control_characters(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)


def test_sync_keeps_unique_unmanaged_skill_unmanaged(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    unmanaged = project_skills / "alpha"
    unmanaged.mkdir(parents=True)
    (unmanaged / "notes.md").write_text("unmanaged local\n")
    configure_source(source, project, home)

    result = run_sv(["sync"], cwd=project, home=home, git_runner=default_runner)
    _assert_sync_success(result)
    assert "No matching Pi skills found to sync." in result.stdout
    assert "Skipped local Pi skill 'alpha'." in result.stdout
    assert (unmanaged / "notes.md").read_text() == "unmanaged local\n"
    assert load_manifest(project_skills) == {}


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

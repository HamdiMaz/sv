import subprocess

import pytest

from sv.cli import _update_sources_and_catalog_from_repos
from sv.config import RepoConfig, SvPaths
from sv.errors import SvError
from sv.manifest import load_global_manifest, load_manifest
from sv.source import FakeSourceBackend, SourceBackendError, default_runner
from tests.helpers import (
    assert_no_raw_control_characters,
    assert_no_traceback,
    configure_source,
    make_source_repo,
    run_git,
    write_source_skill,
)


pytestmark = pytest.mark.integration


class _FailingMetadataBackend:
    name = "failing-metadata"

    def read_index(self):
        raise SourceBackendError("reading .sv/index.toml", "first backend failed")

    def list_candidate_skill_files(self, configured_skills_paths=()):
        return []

    def read_file(self, path):
        raise AssertionError("failing backend should not read files")

    def materialize_folder(self, source_path, destination):
        raise AssertionError("failing backend should not materialize")


class _WorkingMetadataBackend:
    name = "working-metadata"

    def read_index(self):
        return None

    def list_candidate_skill_files(self, configured_skills_paths=()):
        return ["skills/alpha/SKILL.md"]

    def read_file(self, path):
        assert path == "skills/alpha/SKILL.md"
        return b"---\nname: alpha\ndescription: Alpha skill.\n---\n"

    def materialize_folder(self, source_path, destination):
        raise AssertionError("catalog refresh should not materialize")


def test_update_catalog_refresh_fails_closed_when_one_repo_cannot_refresh(
    tmp_path, monkeypatch
):
    working_repo = RepoConfig(id="Org/Working", url="https://github.com/Org/Working.git")
    failing_repo = RepoConfig(id="Org/Failing", url="https://github.com/Org/Failing.git")
    paths = SvPaths.from_home(tmp_path / "home")

    def fake_backends_for_repo(repo_config, paths_arg, *, runner, update):
        assert paths_arg == paths
        assert update is True
        if repo_config == working_repo:
            return (
                FakeSourceBackend(
                    {
                        "skills/alpha/SKILL.md": (
                            "---\nname: alpha\ndescription: Alpha skill.\n---\n"
                        )
                    }
                ),
            )
        assert repo_config == failing_repo
        return (_FailingMetadataBackend(),)

    monkeypatch.setattr(
        "sv.cli.source_backends_for_repo",
        fake_backends_for_repo,
    )

    with pytest.raises(SvError, match="Org/Failing"):
        _update_sources_and_catalog_from_repos(
            [working_repo, failing_repo],
            paths,
            default_runner,
            update=True,
            record_global_source_state=False,
            lightweight_discovery=True,
        )


def test_update_catalog_refresh_allows_successful_backend_fallback(
    tmp_path, monkeypatch
):
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    paths = SvPaths.from_home(tmp_path / "home")

    def fake_backends_for_repo(repo_config, paths_arg, *, runner, update):
        assert repo_config == repo
        assert paths_arg == paths
        assert update is True
        return (_FailingMetadataBackend(), _WorkingMetadataBackend())

    monkeypatch.setattr(
        "sv.cli.source_backends_for_repo",
        fake_backends_for_repo,
    )

    catalog = _update_sources_and_catalog_from_repos(
        [repo],
        paths,
        default_runner,
        update=True,
        record_global_source_state=False,
        lightweight_discovery=True,
        allow_partial_failures=False,
    )

    assert [(entry.name, entry.repo_id, entry.source_backend) for entry in catalog] == [
        ("alpha", "Org/Skills", "working-metadata")
    ]


def test_update_updates_sources_and_syncs_project_skills(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    nested = project / "nested" / "work"
    nested.mkdir(parents=True)
    run_git(["init"], project)
    configure_source(source, project, home)

    add_result = run_sv(
        ["add", "alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    assert add_result.exit_code == 0
    assert "Added Pi skill 'alpha'" in add_result.stdout

    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)

    result = run_sv(["update"], cwd=nested, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "Updating source repos..." in result.stdout
    assert "Updating project skills..." in result.stdout
    assert "Updated Pi skill 'alpha'." in result.stdout
    assert_no_traceback(result.stdout)
    assert_no_raw_control_characters(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)
    assert (
        project / ".pi" / "skills" / "alpha" / "notes.md"
    ).read_text() == "alpha v2\n"
    assert not (nested / ".pi").exists()
    assert not (nested / ".sv").exists()


def test_update_preserves_local_edits_and_marks_update_available(tmp_path, run_sv):
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
    assert add_result.exit_code == 0

    project_skill = project / ".pi" / "skills" / "alpha" / "notes.md"
    project_skill.write_text("alpha local edit\n")
    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)

    result = run_sv(["update"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "Updating source repos..." in result.stdout
    assert "Updating project skills..." in result.stdout
    assert "Skipped local Pi skill 'alpha': local edits preserved" in result.stdout
    assert project_skill.read_text() == "alpha local edit\n"
    manifest_text = (project / ".sv" / "manifest.toml").read_text()
    assert "modified = true" in manifest_text
    assert "update_available = true" in manifest_text
    assert_no_traceback(result.stdout)
    assert_no_raw_control_characters(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)


def test_update_marks_missing_source_orphan_and_reattaches_when_it_reappears(
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
    assert add_result.exit_code == 0

    local_skill = project / ".pi" / "skills" / "alpha"
    run_git(["rm", "-r", "skills/alpha"], source)
    run_git(["commit", "-m", "remove alpha"], source)

    orphan_result = run_sv(["update"], cwd=project, home=home, git_runner=default_runner)

    assert orphan_result.exit_code == 0
    assert (
        "Skipped local Pi skill 'alpha': recorded source is missing"
        in orphan_result.stdout
    )
    assert "orphan state recorded" in orphan_result.stdout
    assert (local_skill / "notes.md").read_text() == "alpha v1\n"
    assert load_manifest(project / ".pi" / "skills")["alpha"].orphan is True

    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "restore alpha"], source)

    reattach_result = run_sv(
        ["update"], cwd=project, home=home, git_runner=default_runner
    )

    assert reattach_result.exit_code == 0
    assert "Updated Pi skill 'alpha'." in reattach_result.stdout
    assert (local_skill / "notes.md").read_text() == "alpha v2\n"
    manifest_entry = load_manifest(project / ".pi" / "skills")["alpha"]
    assert manifest_entry.orphan is False
    assert manifest_entry.modified is False
    assert manifest_entry.update_available is False


def test_update_from_git_subdirectory_marks_root_manifest_orphan_and_reattaches(
    tmp_path, run_sv
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    nested = project / "nested" / "work"
    nested.mkdir(parents=True)
    run_git(["init"], project)
    configure_source(source, project, home)

    add_result = run_sv(
        ["add", "alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )
    assert add_result.exit_code == 0

    run_git(["rm", "-r", "skills/alpha"], source)
    run_git(["commit", "-m", "remove alpha"], source)

    orphan_result = run_sv(["update"], cwd=nested, home=home, git_runner=default_runner)

    assert orphan_result.exit_code == 0
    assert "orphan state recorded" in orphan_result.stdout
    assert load_manifest(project / ".pi" / "skills")["alpha"].orphan is True
    assert not (nested / ".pi").exists()
    assert not (nested / ".sv").exists()

    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "restore alpha"], source)

    reattach_result = run_sv(
        ["update"], cwd=nested, home=home, git_runner=default_runner
    )

    assert reattach_result.exit_code == 0
    assert "Updated Pi skill 'alpha'." in reattach_result.stdout
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == "alpha v2\n"
    assert load_manifest(project / ".pi" / "skills")["alpha"].orphan is False
    assert not (nested / ".pi").exists()
    assert not (nested / ".sv").exists()


def test_update_aborts_on_partial_source_refresh_failure_without_marking_orphan(
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
    assert add_result.exit_code == 0
    configure_source(source_b, project, home)

    project_skill = project / ".pi" / "skills" / "alpha" / "notes.md"

    def git_runner(args, cwd=None):
        if args[:2] == ["git", "fetch"] and cwd is not None and "source-a" in str(cwd):
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stderr="source-a fetch failed\n",
            )
        return default_runner(args, cwd)

    result = run_sv(["update"], cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 1
    assert "source-a fetch failed" in result.stderr
    assert "Updating project skills..." not in result.stdout
    assert project_skill.read_text() == "alpha v1\n"
    assert load_manifest(project / ".pi" / "skills")["alpha"].orphan is False
    states_by_url = {
        state.repo_url: state for state in load_global_manifest(SvPaths.from_home(home)).values()
    }
    assert states_by_url[str(source_a)].last_refresh_status == "error"
    assert states_by_url[str(source_b)].last_refresh_status == "ok"
    assert_no_traceback(result.stdout)
    assert_no_raw_control_characters(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)


def test_update_reports_source_fetch_failure_without_syncing_project_skills(
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
    assert add_result.exit_code == 0

    project_skill = project / ".pi" / "skills" / "alpha" / "notes.md"
    assert project_skill.read_text() == "alpha v1\n"

    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")

    def git_runner(args, cwd=None):
        if args == ["git", "--version"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="git version 2.39.0\n",
            )
        if args == ["git", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=f"{source}\n")
        if args[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stderr="fetch failed due to lock\n",
            )
        return default_runner(args, cwd)

    result = run_sv(["update"], cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 1
    assert "Updating source repos..." in result.stdout
    assert "Updating project skills..." not in result.stdout
    assert "Updated Pi skill 'alpha'." not in result.stdout
    assert "fetch failed due to lock" in result.stderr
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stderr)
    assert_no_traceback(result.stdout)
    assert_no_raw_control_characters(result.stdout)
    assert project_skill.read_text() == "alpha v1\n"

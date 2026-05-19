from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import cast
import os
import subprocess
import sys

import pytest

import sv.cli as cli_module
import sv.hashing as hashing_module
import sv.index as index_module
import sv.materialization as materialization_module
import sv.project as project_module
import sv.selector as selector_module
import sv.source as source_module
import sv.source_backends.git as git_module
import sv.table as table_module
import sv.tomlutil as tomlutil_module
from sv.catalog import SourceCatalogResult, SourceSkill
from sv.config import RepoConfig, SvPaths, _save_config
from sv.errors import SvError
from sv.manifest import GlobalSourceState, ManifestEntry, load_global_manifest, load_manifest, save_manifest
from sv.project import AddSkillResult, SyncResult, SyncSkip
from sv.materialization import (
    install_materialized_skill_folder,
    replace_with_materialized_skill_folder,
)
from sv.source import (
    GitHubGhApiBackend,
    GitHubHttpsApiBackend,
    GitHubHttpResponse,
    GitHubRepoRef,
    GitLocalSourceBackend,
    GitTreelessPartialBackend,
    SourceBackendError,
)
from sv.table import TableState, browse_table
from sv.tomlutil import load_toml_document, require_schema_version


class _TtyStream(StringIO):
    def isatty(self):
        return True

    def fileno(self):
        return 0


class _NoFilenoTty(StringIO):
    def isatty(self):
        return True


class _FakeTermios:
    TCSADRAIN = 1
    error = OSError

    @staticmethod
    def tcgetattr(fd):
        return ["settings"]

    @staticmethod
    def tcsetattr(fd, when, settings):
        return None


class _FakeTty:
    @staticmethod
    def setcbreak(fd):
        return None


def _completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def _source_skill(tmp_path: Path, repo_id: str, repo_url: str, name: str = "alpha") -> SourceSkill:
    repo_path = tmp_path / repo_id.replace("/", "_")
    skill_path = repo_path / "skills" / name
    skill_path.mkdir(parents=True, exist_ok=True)
    (skill_path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}.\n---\n")
    return SourceSkill(
        name=name,
        description=f"{name}.",
        repo_id=repo_id,
        repo_url=repo_url,
        repo_path=repo_path,
        source_path=skill_path,
    )


def test_github_repo_ref_full_name_and_gh_api_error_edges():
    assert GitHubRepoRef("Org", "Repo").full_name == "Org/Repo"

    def os_error(_args, _cwd):
        raise OSError("network\ndown")

    gh_backend = GitHubGhApiBackend(GitHubRepoRef("Org", "Repo"), runner=os_error)
    with pytest.raises(SourceBackendError) as os_exc:
        gh_backend.read_file("skills/alpha/SKILL.md")
    assert os_exc.value.detail == "gh api failed: network\\x0adown"

    empty_failure = GitHubGhApiBackend(
        GitHubRepoRef("Org", "Repo"),
        runner=lambda args, cwd: _completed(args, returncode=2),
    )
    with pytest.raises(SourceBackendError) as status_exc:
        empty_failure.list_candidate_skill_files()
    assert "status 2" in status_exc.value.detail

    bad_base64 = GitHubGhApiBackend(
        GitHubRepoRef("Org", "Repo"),
        runner=lambda args, cwd: _completed(args, stdout="not-base64"),
    )
    with pytest.raises(SourceBackendError, match="valid base64"):
        bad_base64.read_file("skills/alpha/SKILL.md")


def test_github_gh_api_read_index_returns_none_only_when_root_is_accessible():
    calls: list[list[str]] = []
    responses = iter([
        _completed(["gh", "api"], returncode=1, stderr="HTTP 404: Not Found"),
        _completed(["gh", "api"], stdout="[]"),
    ])

    def runner(args, cwd):
        calls.append(list(args))
        return next(responses)

    backend = GitHubGhApiBackend(GitHubRepoRef("Org", "Repo"), runner=runner)

    assert backend.read_index() is None
    assert calls == [
        ["gh", "api", "/repos/Org/Repo/contents/.sv/index.toml", "--jq", ".content"],
        ["gh", "api", "/repos/Org/Repo/contents"],
    ]


def test_github_gh_api_backend_rejects_unsupported_file_content_encoding():
    calls: list[list[str]] = []
    responses = iter([
        _completed(["gh", "api"], stdout=""),
        _completed(["gh", "api"], stdout="none\n"),
    ])

    def runner(args, cwd):
        calls.append(list(args))
        return next(responses)

    backend = GitHubGhApiBackend(GitHubRepoRef("Org", "Repo"), runner=runner)

    with pytest.raises(SourceBackendError, match="unsupported file content encoding"):
        backend.read_file("skills/alpha/SKILL.md")

    assert calls == [
        ["gh", "api", "/repos/Org/Repo/contents/skills/alpha/SKILL.md", "--jq", ".content"],
        ["gh", "api", "/repos/Org/Repo/contents/skills/alpha/SKILL.md", "--jq", ".encoding"],
    ]


def test_default_runner_decodes_invalid_subprocess_output_with_replacement():
    result = source_module.default_runner(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'\\xff'); sys.stderr.buffer.write(b'\\xfe'); sys.exit(3)",
        ]
    )

    assert result.returncode == 3
    assert result.stdout == "�"
    assert result.stderr == "�"


def test_github_https_backend_allows_empty_base64_file_content():
    backend = GitHubHttpsApiBackend(
        GitHubRepoRef("Org", "Repo"),
        http_get=lambda _url, _headers: GitHubHttpResponse(
            200, b'{"content":"","encoding":"base64"}'
        ),
        env={},
    )

    assert backend.read_file("skills/empty.txt") == b""


def test_github_https_backend_rejects_malformed_success_responses_and_requests():
    backend = GitHubHttpsApiBackend(
        GitHubRepoRef("Org", "Repo"),
        http_get=lambda _url, _headers: GitHubHttpResponse(200, b"not-json"),
        env={},
    )
    with pytest.raises(SourceBackendError, match="not valid JSON"):
        backend.read_file("skills/alpha/SKILL.md")

    missing_content = GitHubHttpsApiBackend(
        GitHubRepoRef("Org", "Repo"),
        http_get=lambda _url, _headers: GitHubHttpResponse(200, b"{}"),
        env={},
    )
    with pytest.raises(SourceBackendError, match="did not contain file content"):
        missing_content.read_file("skills/alpha/SKILL.md")

    invalid_utf8 = GitHubHttpsApiBackend(
        GitHubRepoRef("Org", "Repo"),
        http_get=lambda _url, _headers: GitHubHttpResponse(200, b"\xff"),
        env={},
    )
    with pytest.raises(SourceBackendError, match="valid UTF-8"):
        invalid_utf8.list_candidate_skill_files()

    with pytest.raises(SourceBackendError, match="request was empty"):
        backend._run_api([], operation="testing")
    with pytest.raises(SourceBackendError, match="endpoint must start"):
        backend._request_endpoint("repos/Org/Repo", operation="testing")
    with pytest.raises(SourceBackendError, match="Unsupported GitHub HTTPS API arguments"):
        backend._run_api(["/repos/Org/Repo/contents", "--jq", ".name"], operation="testing")


def test_github_https_backend_wraps_request_os_error_and_plain_404_hint():
    def fail_request(_url, _headers):
        raise OSError("socket closed")

    backend = GitHubHttpsApiBackend(GitHubRepoRef("Org", "Private"), http_get=fail_request, env={})
    with pytest.raises(SourceBackendError, match="socket closed"):
        backend.list_candidate_skill_files()

    not_found = GitHubHttpsApiBackend(
        GitHubRepoRef("Org", "Private"),
        http_get=lambda _url, _headers: GitHubHttpResponse(404, b"Not Found"),
        env={},
    )
    with pytest.raises(SourceBackendError) as exc_info:
        not_found.read_file("skills/private/SKILL.md")
    assert "HTTP 404" in exc_info.value.detail
    assert exc_info.value.hint is not None and "private repo" in exc_info.value.hint


def test_fake_source_backend_read_and_materialize_error_edges(tmp_path: Path):
    backend = source_module.FakeSourceBackend({"skills/alpha/SKILL.md": "alpha\n"})

    with pytest.raises(SourceBackendError, match="was not found"):
        backend.read_file("skills/missing/SKILL.md")
    with pytest.raises(SourceBackendError, match="does not contain files"):
        backend.materialize_folder("skills/missing", tmp_path / "missing")

    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "old.txt").write_text("old\n")
    backend.materialize_folder("skills/alpha", destination)
    assert not (destination / "old.txt").exists()
    assert (destination / "SKILL.md").read_text() == "alpha\n"

    outside = tmp_path / "outside"
    outside.write_text("outside\n")
    symlink_destination = tmp_path / "symlink"
    symlink_destination.symlink_to(outside)
    with pytest.raises(SvError, match="must not be a symlink"):
        backend.materialize_folder("skills/alpha", symlink_destination)


def test_source_cleanup_helpers_remove_failed_file_and_report_cleanup_errors(tmp_path: Path, monkeypatch):
    repo_file = tmp_path / "repo-file"
    repo_file.write_text("partial")
    git_module._remove_failed_lightweight_checkout(repo_file)
    assert not repo_file.exists()

    repo_dir = tmp_path / "repo-dir"
    repo_dir.mkdir()
    monkeypatch.setattr(git_module.shutil, "rmtree", lambda _path: (_ for _ in ()).throw(OSError("busy")))
    with pytest.raises(SvError, match="incomplete lightweight"):
        git_module._remove_failed_lightweight_checkout(repo_dir)


def test_tomlutil_load_and_schema_version_edge_cases(tmp_path: Path):
    directory = tmp_path / "as-directory.toml"
    directory.mkdir()
    with pytest.raises(SvError, match="Failed to read sv document"):
        load_toml_document(directory, "sv document")

    path = tmp_path / "state.toml"
    for bad_version in ("1", True):
        with pytest.raises(SvError, match="schema_version must be an integer"):
            require_schema_version(
                {"schema_version": bad_version},
                path=path,
                document_name="sv state",
                current_version=1,
            )

    migrated = require_schema_version(
        {"schema_version": 0, "value": "old"},
        path=path,
        document_name="sv state",
        current_version=2,
        migrations={
            0: lambda data: {**data, "schema_version": 1},
            1: lambda data: {**data, "schema_version": 2, "value": "new"},
        },
    )
    assert migrated == {"schema_version": 2, "value": "new"}

    with pytest.raises(SvError, match="schema_version must be an integer"):
        require_schema_version(
            {"schema_version": 0},
            path=path,
            document_name="sv state",
            current_version=1,
            migrations={0: lambda data: {**data, "schema_version": True}},
        )


def test_tomlutil_atomic_write_replaces_stale_temp_and_cleans_fdopen_write_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "state.toml"
    stale_temp = tmp_path / ".state.toml.tmp"
    stale_temp.write_text("stale")
    tomlutil_module.atomic_write_text(
        target,
        "new\n",
        document_name="sv state",
        temp_name=".state.toml.tmp",
    )
    assert target.read_text() == "new\n"
    assert not stale_temp.exists()

    broken = tmp_path / "broken.tmp"
    original_fdopen = os.fdopen

    def failing_fdopen(fd, *args, **kwargs):
        class FailingFile:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                os.close(fd)
                return False

            def write(self, _text):
                raise OSError("disk vanished")

        return FailingFile()

    monkeypatch.setattr(os, "fdopen", failing_fdopen)
    with pytest.raises(OSError, match="disk vanished"):
        tomlutil_module._write_new_file_no_follow(broken, "content")
    monkeypatch.setattr(os, "fdopen", original_fdopen)
    assert not broken.exists()


def test_materialization_reports_rename_and_rollback_failures(tmp_path: Path, monkeypatch):
    materialized = tmp_path / ".alpha.sv-add-tmp"
    materialized.mkdir()
    (materialized / "SKILL.md").write_text("remote\n")
    target = tmp_path / "alpha"
    original_rename = Path.rename

    def fail_install_rename(self, destination):
        if self == materialized:
            raise OSError("rename denied")
        return original_rename(self, destination)

    monkeypatch.setattr(Path, "rename", fail_install_rename)
    with pytest.raises(SvError, match="rename denied"):
        install_materialized_skill_folder(materialized, target, error_message="Failed add")
    assert not materialized.exists()

    materialized.mkdir()
    (materialized / "SKILL.md").write_text("remote\n")
    monkeypatch.setattr(
        materialization_module,
        "remove_materialization_path",
        lambda _path, **_kwargs: (_ for _ in ()).throw(OSError("rollback denied")),
    )
    monkeypatch.setattr(Path, "rename", original_rename)
    with pytest.raises(SvError, match="rollback failed: rollback denied"):
        install_materialized_skill_folder(
            materialized,
            target,
            error_message="Failed add",
            after_install=lambda: (_ for _ in ()).throw(SvError("manifest failed")),
        )


def test_replace_materialized_folder_restores_backup_on_replace_failure_and_reports_callback_rollback_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "alpha"
    target.mkdir()
    (target / "SKILL.md").write_text("local\n")
    materialized = tmp_path / ".alpha.sv-sync-tmp"
    materialized.mkdir()
    (materialized / "SKILL.md").write_text("remote\n")
    backup = tmp_path / ".alpha.sv-sync-backup"
    original_rename = Path.rename

    def fail_materialized_rename(self, destination):
        if self == materialized:
            raise OSError("replace denied")
        return original_rename(self, destination)

    monkeypatch.setattr(Path, "rename", fail_materialized_rename)
    with pytest.raises(SvError, match="replace denied"):
        replace_with_materialized_skill_folder(materialized, target, backup, error_message="Failed sync")
    assert (target / "SKILL.md").read_text() == "local\n"

    monkeypatch.setattr(Path, "rename", original_rename)
    materialized.mkdir()
    (materialized / "SKILL.md").write_text("remote\n")
    monkeypatch.setattr(
        materialization_module,
        "restore_materialization_backup",
        lambda _target, _backup: (_ for _ in ()).throw(OSError("restore denied")),
    )
    with pytest.raises(SvError, match="post-operation callback failed.*restore denied"):
        replace_with_materialized_skill_folder(
            materialized,
            target,
            backup,
            error_message="Failed sync",
            after_replace=lambda: (_ for _ in ()).throw(ValueError("hook failed")),
        )


def test_manifest_parsing_rejects_global_and_project_edge_shapes(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path / "home")
    paths.global_manifest_file.parent.mkdir(parents=True)
    paths.global_manifest_file.write_text("schema_version = 1\nsources = {}\n")
    with pytest.raises(SvError, match="sources must be a list"):
        load_global_manifest(paths)

    paths.global_manifest_file.write_text("schema_version = 1\n[[sources]]\nrepo_id = 1\nrepo_url = \"url\"\n")
    with pytest.raises(SvError, match="repo_id.*string"):
        load_global_manifest(paths)

    paths.global_manifest_file.write_text(
        "schema_version = 1\n[[sources]]\nrepo_id = \"repo\"\nrepo_url = \"url\"\ncatalog_skill_count = true\n"
    )
    with pytest.raises(SvError, match="catalog_skill_count.*integer"):
        load_global_manifest(paths)

    project_skills = tmp_path / "project" / ".pi" / "skills"
    manifest_path = tmp_path / "project" / ".sv" / "manifest.toml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("schema_version = 1\nskills = {}\n")
    with pytest.raises(SvError, match="skills must be a list"):
        load_manifest(project_skills)

    manifest_path.write_text("schema_version = 1\n[[skills]]\nname = \"alpha\"\n")
    with pytest.raises(SvError, match="missing 'source_repo_id'"):
        load_manifest(project_skills)

    manifest_path.write_text(
        "schema_version = 1\n[[skills]]\n"
        "name = \"alpha\"\nsource_repo_id = \"repo\"\nsource_repo_url = \"url\"\n"
        "source_path = \"skills/alpha\"\ndescription = \"Alpha\"\nmodified = \"yes\"\n"
    )
    with pytest.raises(SvError, match="modified.*boolean"):
        load_manifest(project_skills)


def test_manifest_duplicate_keys_and_symlinked_manifest_dir(tmp_path: Path):
    project_skills = tmp_path / "project" / ".pi" / "skills"
    manifest_path = tmp_path / "project" / ".sv" / "manifest.toml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        "schema_version = 1\n"
        "[[skills]]\nname = \"alpha\"\nsource_repo_id = \"A\"\nsource_repo_url = \"url\"\nsource_path = \"skills/alpha\"\ndescription = \"Alpha\"\n"
        "[[skills]]\nname = \"alpha\"\ntarget_kind = \"skill-vault\"\ntarget_path = \"skills/alpha\"\nsource_repo_id = \"B\"\nsource_repo_url = \"url\"\nsource_path = \"skills/alpha\"\ndescription = \"Alpha\"\n"
        "[[skills]]\nname = \"alpha\"\ntarget_kind = \"skill-vault\"\ntarget_path = \"skills/alpha\"\nsource_repo_id = \"C\"\nsource_repo_url = \"url\"\nsource_path = \"skills/alpha\"\ndescription = \"Alpha\"\n"
    )

    entries = load_manifest(project_skills)

    assert set(entries) == {"alpha", "skill-vault::skills/alpha:alpha", "skill-vault::skills/alpha:alpha:2"}

    home = tmp_path / "home"
    paths = SvPaths.from_home(home)
    outside = tmp_path / "outside"
    outside.mkdir()
    paths.global_manifest_file.parent.parent.mkdir(parents=True)
    paths.global_manifest_file.parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SvError, match="symlinked sv manifest directory"):
        save_manifest(home / ".pi" / "skills", {})


def test_cli_global_source_state_helpers_skip_project_manifests_and_match_equivalent_sources(tmp_path: Path):
    home = tmp_path / "home"
    paths = SvPaths.from_home(home)
    save_manifest(
        home / ".pi" / "skills",
        {
            "alpha": ManifestEntry(
                name="alpha",
                repo_id="Org/Skills",
                repo_url="https://github.com/Org/Skills.git",
                source_path="skills/alpha",
                description="Alpha",
            )
        },
    )
    original = paths.global_manifest_file.read_text()

    cli_module._record_global_source_refresh_failure(
        paths,
        [RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")],
        "failed",
        "2026-01-01T00:00:00Z",
    )
    assert paths.global_manifest_file.read_text() == original
    assert cli_module._load_global_manifest_for_source_state(paths) is None

    entry = _source_skill(tmp_path, "Canonical", "https://github.com/Org/Skills.git")
    alias = RepoConfig(id="Alias", url="https://github.com/Org/Skills")
    assert cli_module._catalog_entries_for_repo(alias, [entry], {}) == (entry,)
    invalid = RepoConfig(id="Invalid", url="::not-a-url::")
    assert cli_module._catalog_entries_for_repo(invalid, [entry], {}) == ()

    repos = [alias, RepoConfig(id="Candidate", url="https://github.com/Org/Skills.git")]
    assert cli_module._source_refresh_metadata_for_repo(alias, repos, {"Candidate": "github-api"}) == "github-api"
    assert cli_module._source_refresh_metadata_for_repo(invalid, repos, {"Candidate": "github-api"}) is None

    previous = GlobalSourceState(repo_id="old", repo_url="https://github.com/Org/Skills.git", source_commit="abc")
    assert cli_module._previous_state_for_same_source(previous, "https://github.com/Org/Skills") == previous
    assert cli_module._previous_state_for_same_source(previous, "::bad::") is None


def test_cli_status_catalog_and_global_status_empty_branches(tmp_path: Path, capsys):
    paths = SvPaths.from_home(tmp_path / "home")
    assert cli_module._status_catalog_if_configured(paths, cli_module.default_runner, record_global_source_state=False) is None

    _save_config(paths, cli_module.SvConfig(repos=()))
    assert cli_module._status_catalog_if_configured(paths, cli_module.default_runner, record_global_source_state=False) is None

    result = cli_module._handle_global_status(paths)
    output = capsys.readouterr().out
    assert result == 0
    assert "No global skill sources configured" in output


def test_cli_project_and_vault_status_entry_filters_and_symlink_rejections(tmp_path: Path):
    project_skills = tmp_path / "project" / ".pi" / "skills"
    (project_skills / "alpha").mkdir(parents=True)
    (project_skills / "blocked").write_text("not a directory", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (project_skills / "linked").symlink_to(outside, target_is_directory=True)
    save_manifest(
        project_skills,
        {
            "alpha": ManifestEntry(
                name="alpha",
                repo_id="Repo",
                repo_url="url",
                source_path="skills/alpha",
                description="Alpha",
                target_kind="project-agent",
                target_agent="pi",
                target_path=".pi/skills/alpha",
            ),
            "blocked": ManifestEntry(
                name="blocked",
                repo_id="Repo",
                repo_url="url",
                source_path="skills/blocked",
                description="Blocked",
                target_kind="project-agent",
                target_agent="pi",
                target_path=".pi/skills/blocked",
            ),
            "ignored": ManifestEntry(
                name="ignored",
                repo_id="Repo",
                repo_url="url",
                source_path="skills/ignored",
                description="Ignored",
                target_kind="project-agent",
                target_agent="other",
                target_path=".pi/skills/ignored",
            ),
            "linked": ManifestEntry(
                name="linked",
                repo_id="Repo",
                repo_url="url",
                source_path="skills/linked",
                description="Linked",
                target_kind="project-agent",
                target_agent="pi",
                target_path=".pi/skills/linked",
            ),
        },
    )
    with pytest.raises(SvError, match="symlinked Pi skill"):
        cli_module._project_status_entries(project_skills)
    (project_skills / "linked").unlink()
    project_entries = cli_module._project_status_entries(project_skills)
    assert [
        (entry.name, entry.target_missing, entry.target_invalid)
        for entry in project_entries
    ] == [
        ("alpha", False, False),
        ("blocked", False, True),
        ("linked", True, False),
    ]

    vault_skills = tmp_path / "vault" / "skills"
    (vault_skills / "alpha").mkdir(parents=True)
    (vault_skills / "blocked").write_text("not a directory", encoding="utf-8")
    (vault_skills / "linked").symlink_to(outside, target_is_directory=True)
    save_manifest(
        vault_skills,
        {
            "alpha": ManifestEntry(
                name="alpha",
                repo_id="Repo",
                repo_url="url",
                source_path="skills/alpha",
                description="Alpha",
                target_kind="skill-vault",
                target_agent=None,
                target_path="skills/alpha",
            ),
            "blocked": ManifestEntry(
                name="blocked",
                repo_id="Repo",
                repo_url="url",
                source_path="skills/blocked",
                description="Blocked",
                target_kind="skill-vault",
                target_agent=None,
                target_path="skills/blocked",
            ),
            "linked": ManifestEntry(
                name="linked",
                repo_id="Repo",
                repo_url="url",
                source_path="skills/linked",
                description="Linked",
                target_kind="skill-vault",
                target_agent=None,
                target_path="skills/linked",
            ),
        },
    )
    with pytest.raises(SvError, match="symlinked vault skill"):
        cli_module._vault_status_entries(vault_skills)
    (vault_skills / "linked").unlink()
    vault_entries = cli_module._vault_status_entries(vault_skills)
    assert [
        (entry.name, entry.target_missing, entry.target_invalid) for entry in vault_entries
    ] == [
        ("alpha", False, False),
        ("blocked", False, True),
        ("linked", True, False),
    ]


def test_selector_empty_and_filtered_interactive_paths(monkeypatch):
    from sv.selector import SelectionState, _format_help_line, select_skills

    empty = SelectionState([])
    assert "No skills to show" in _format_help_line(empty)
    filtered = SelectionState(["alpha", "beta"])
    filtered.set_filter("missing")
    assert "0-0 of 0 matching 2" in _format_help_line(filtered)

    output = _TtyStream()
    key_inputs = iter(["filter:zz", "enter", "quit"])
    monkeypatch.setitem(sys.modules, "termios", _FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", _FakeTty)
    monkeypatch.setattr("sv.selector._read_key", lambda _fd: next(key_inputs))

    selected = select_skills(["alpha"], stdin=_TtyStream(), stdout=output)

    assert selected == []
    assert "Showing 0-0 of 0" in output.getvalue()

    confirm_output = _TtyStream()
    confirm_keys = iter(["space", "enter"])
    monkeypatch.setattr("sv.selector._read_key", lambda _fd: next(confirm_keys))
    assert select_skills(["alpha"], stdin=_TtyStream(), stdout=confirm_output) == ["alpha"]

    filter_output = _TtyStream()
    filter_keys = iter(["filter", "enter"])
    monkeypatch.setattr("sv.selector._read_key", lambda _fd: next(filter_keys))
    monkeypatch.setattr("sv.selector._read_filter_query", lambda _fd: "alpha")
    assert select_skills(["alpha"], stdin=_TtyStream(), stdout=filter_output) == []


def test_table_remaining_interactive_branches(monkeypatch):
    state = TableState(["Skill"], [["alpha"], ["beta"]], cursor=5, viewport_start=5)
    assert state.current_row() == ["beta"]
    state.move_up()
    assert state.current_row() == ["alpha"]

    with pytest.raises(SvError, match="could not read terminal settings"):
        browse_table(["Skill"], [["alpha"]], stdin=_NoFilenoTty(), stdout=_TtyStream())

    output = _TtyStream()
    key_inputs = iter(["filter:none", "enter", "action:a", "unknown", "eof"])
    monkeypatch.setitem(sys.modules, "termios", _FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", _FakeTty)
    monkeypatch.setattr("sv.table._read_key", lambda _fd: next(key_inputs))
    actions: list[list[str]] = []

    selected = browse_table(
        ["Skill"],
        [["alpha"]],
        stdin=_TtyStream(),
        stdout=output,
        key_actions={"a": lambda row: actions.append(list(row))},
        key_help="a action",
    )

    assert selected is None
    assert actions == []
    assert "a action" in output.getvalue()


def test_table_wrap_helpers_cover_preferred_and_hard_width_edges():
    assert table_module._wrap_unspaced_value("abc/def", 4) == ["abc/", "def"]
    assert table_module._wrap_unspaced_value("界", 1) == ["?"]
    assert table_module._wrap_display_width("a界b", 2) == ["a", "界", "b"]
    assert table_module._fit_text("abcdef", 2) == ".."


def test_source_github_directory_listing_filters_and_materialization_edges(tmp_path: Path):
    listing_responses = iter([
        _completed(["gh", "api"], stdout='[{"name":"team","path":"team","type":"dir"}]'),
        _completed(["gh", "api"], stdout='[{"name":"README.md","path":"skills/README.md","type":"file"}]'),
        _completed(["gh", "api"], stdout='[{"name":"README.md","path":"team/skills/README.md","type":"file"}]'),
        _completed(["gh", "api"], stdout='[{"name":"README.md","path":"custom/README.md","type":"file"}]'),
    ])
    backend = GitHubGhApiBackend(
        GitHubRepoRef("Org", "Repo"),
        runner=lambda args, cwd: next(listing_responses),
    )
    assert backend.list_candidate_skill_files(("custom",)) == []

    symlink_backend = GitHubGhApiBackend(
        GitHubRepoRef("Org", "Repo"),
        runner=lambda args, cwd: _completed(
            args,
            stdout='[{"name":"link","path":"skills/alpha/link","type":"symlink"}]',
        ),
    )
    with pytest.raises(SourceBackendError, match="unsupported GitHub content type symlink"):
        symlink_backend.materialize_folder("skills/alpha", tmp_path / "materialized")

    empty_backend = GitHubGhApiBackend(
        GitHubRepoRef("Org", "Repo"),
        runner=lambda args, cwd: _completed(args, stdout="[]"),
    )
    with pytest.raises(SourceBackendError, match="does not contain files"):
        empty_backend.materialize_folder("skills/empty", tmp_path / "empty")

    destination_link = tmp_path / "destination-link"
    outside = tmp_path / "outside"
    outside.write_text("outside")
    destination_link.symlink_to(outside)
    with pytest.raises(SvError, match="destination must not be a symlink"):
        empty_backend.materialize_folder("skills/alpha", destination_link)


def test_source_github_materialize_replaces_existing_file_and_rejects_root_file_path(tmp_path: Path):
    responses = iter([
        _completed(["gh", "api"], stdout='[{"name":"SKILL.md","path":"skills/alpha/SKILL.md","type":"file"}]'),
        _completed(["gh", "api"], stdout="YWxwaGEK"),
    ])
    backend = GitHubGhApiBackend(GitHubRepoRef("Org", "Repo"), runner=lambda args, cwd: next(responses))
    destination = tmp_path / "destination"
    destination.write_text("old")

    backend.materialize_folder("skills/alpha", destination)

    assert (destination / "SKILL.md").read_text() == "alpha\n"

    bad_responses = iter([
        _completed(["gh", "api"], stdout='[{"name":"alpha","path":"skills/alpha","type":"file"}]'),
        _completed(["gh", "api"], stdout="YWxwaGEK"),
    ])
    bad_backend = GitHubGhApiBackend(GitHubRepoRef("Org", "Repo"), runner=lambda args, cwd: next(bad_responses))
    with pytest.raises(SourceBackendError, match="is not inside skills/alpha"):
        bad_backend.materialize_folder("skills/alpha", tmp_path / "bad")


def test_source_git_local_backend_read_list_and_materialize_edges(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "skills" / "alpha").mkdir(parents=True)
    (repo / "skills" / "alpha" / "SKILL.md").write_text("alpha\n")
    (repo / "skills" / ".hidden").mkdir()
    (repo / "skills" / ".hidden" / "SKILL.md").write_text("hidden\n")
    (repo / "skills" / "not-a-directory").write_text("skip\n")
    backend = GitLocalSourceBackend(repo)

    assert backend.list_candidate_skill_files() == ["skills/alpha/SKILL.md"]

    with pytest.raises(SourceBackendError, match="could not be read"):
        backend.read_file("skills/missing/SKILL.md")
    assert backend.read_index() is None

    original_iterdir = Path.iterdir

    def fail_skills_iterdir(self):
        if self == repo / "skills":
            raise OSError("cannot list")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", fail_skills_iterdir)
    with pytest.raises(SourceBackendError, match="could not be listed"):
        backend.list_candidate_skill_files()
    monkeypatch.setattr(Path, "iterdir", original_iterdir)

    with pytest.raises(SourceBackendError, match="does not contain files"):
        backend.materialize_folder("skills/missing", tmp_path / "missing")

    existing_file = tmp_path / "existing-file"
    existing_file.write_text("old\n")
    backend.materialize_folder("skills/alpha", existing_file)
    assert (existing_file / "SKILL.md").read_text() == "alpha\n"

    existing_dir = tmp_path / "existing-dir"
    existing_dir.mkdir()
    (existing_dir / "old.txt").write_text("old\n")
    backend.materialize_folder("skills/alpha", existing_dir)
    assert (existing_dir / "SKILL.md").read_text() == "alpha\n"
    assert not (existing_dir / "old.txt").exists()

    destination = tmp_path / "dest-link"
    outside = tmp_path / "outside"
    outside.mkdir()
    destination.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SvError, match="destination must not be a symlink"):
        backend.materialize_folder("skills/alpha", destination)

    (repo / "skills" / "alpha" / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SvError, match="must not contain symlinks"):
        backend.materialize_folder("skills/alpha", tmp_path / "with-link")


def test_source_git_sparse_local_metadata_and_cleanup_error_branches(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    backend = GitTreelessPartialBackend("https://example.com/repo.git", repo)
    assert not backend._local_metadata_checkout_available(("custom",))

    repo.mkdir()
    (repo / "team" / "skills").mkdir(parents=True)
    assert backend._local_metadata_checkout_available(())
    (repo / "team" / "skills").rmdir()
    (repo / "team").rmdir()
    (repo / "custom").mkdir()
    assert backend._local_metadata_checkout_available(("bad:name", "missing", "custom"))

    original_iterdir = Path.iterdir

    def fail_repo_iterdir(self):
        if self == repo:
            raise OSError("cannot iterate")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", fail_repo_iterdir)
    assert not backend._local_metadata_checkout_available(())
    monkeypatch.setattr(Path, "iterdir", original_iterdir)

    target = repo / "custom"
    monkeypatch.setattr(Path, "is_file", lambda self: self == target)
    monkeypatch.setattr(Path, "unlink", lambda self: (_ for _ in ()).throw(OSError("unlink denied")))
    with pytest.raises(SvError, match="selected source cache folder"):
        git_module._remove_backend_cache_folder(repo, "custom")


def test_cli_failure_reports_and_mixed_refresh_state(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path / "home")
    repos = [
        RepoConfig(id="Good", url="https://github.com/Org/Good.git"),
        RepoConfig(id="Bad", url="https://github.com/Org/Bad.git"),
    ]
    entry = _source_skill(tmp_path, "Good", "https://github.com/Org/Good.git")
    api_failure = source_module.SourceBackendFailure(
        repo_id="Bad",
        repo_url="https://github.com/Org/Bad.git",
        backend="github-gh-api",
        operation="listing",
        detail="HTTP 403 rate limit",
        hint="gh auth login",
    )
    git_failure = source_module.SourceBackendFailure(
        repo_id="Bad",
        repo_url="https://github.com/Org/Bad.git",
        backend="git-treeless-partial",
        operation="listing",
        detail="git failed",
    )

    report = cli_module._failure_report((api_failure, git_failure))
    assert "GitHub API auth/rate limits" in report
    assert cli_module._failure_mentions_auth_or_rate_limit(api_failure)
    assert cli_module._first_index_parse_failure((api_failure,)) is None
    assert cli_module._first_skill_metadata_failure((api_failure,)) is None

    cli_module._record_mixed_source_refresh(
        paths,
        repos,
        [entry],
        ["Good"],
        (api_failure, git_failure),
        "2026-01-01T00:00:00Z",
        refreshed_backends_by_repo={"Good": "fake"},
        index_hashes_by_repo={"Good": "sha256:1bc04b5291c26a46d918139138b992d2de976d6851d0893b0476b85bfbdfc6e6"},
    )
    states = load_global_manifest(paths)
    assert states["Good"].last_refresh_status == "ok"
    assert states["Bad"].last_refresh_status == "error"


def test_cli_print_helpers_and_interactive_source_rows(tmp_path: Path, capsys):
    first = _source_skill(tmp_path, "A", "https://github.com/Org/A.git")
    second = SourceSkill(
        name="alpha",
        description="other.",
        repo_id="A",
        repo_url="https://github.com/Org/A.git",
        repo_path=first.repo_path,
        source_path=first.repo_path / "other" / "alpha",
        source_relative_path="other/alpha",
    )
    rows, entries = cli_module._interactive_source_skill_rows([first, second])
    assert rows[0][1] == "A:skills/alpha"
    assert cli_module._entry_for_interactive_row(rows[1], rows, entries) == second
    assert cli_module._entry_for_interactive_row(["missing"], rows, entries) is None

    cli_module._print_source_skill_detail(second)
    detail_output = capsys.readouterr().out
    assert "Path: other/alpha" in detail_output
    assert "Add as:" not in detail_output

    assert cli_module._handle_search("zzz", [first]) == 0
    assert "No matching skills" in capsys.readouterr().out
    assert cli_module._handle_search("alpha", []) == 0
    assert "No valid skills" in capsys.readouterr().out
    assert cli_module._handle_list([]) == 0
    assert "No valid skills" in capsys.readouterr().out

    cli_module._print_add_result(
        AddSkillResult(
            skill="alpha",
            target=tmp_path / "alpha",
            status="exists",
            repo_id="Requested",
            existing_repo_id="Existing",
            source_reference="Requested:alpha",
            existing_source_reference="Existing:alpha",
        )
    )
    assert "currently from Existing:alpha" in capsys.readouterr().out
    cli_module._print_add_result(
        AddSkillResult(
            skill="beta",
            target=tmp_path / "beta",
            status="exists",
            repo_id="Requested",
            source_reference="Requested:beta",
        )
    )
    assert "no sv origin recorded" in capsys.readouterr().out


def test_cli_update_sync_duplicate_and_selector_helper_branches(tmp_path: Path, capsys):
    cli_module._print_update_result(SyncResult(updated=[], skipped=[], backfilled=[], no_skills_dir=True))
    assert "No Pi skills found to update" in capsys.readouterr().out
    cli_module._print_update_result(SyncResult(updated=[], skipped=[SyncSkip("alpha", "unchanged")], backfilled=[]))
    assert "No matching Pi skills found to update" in capsys.readouterr().out
    cli_module._print_update_result(
        SyncResult(
            updated=[],
            skipped=[
                SyncSkip("alpha", "modified"),
                SyncSkip("beta", "source-missing", repo_ids=("Repo\nA",)),
                SyncSkip("gamma", "other"),
            ],
            backfilled=[],
        )
    )
    output = capsys.readouterr().out
    assert "local edits preserved" in output
    assert "Repo\\x0aA" in output
    assert "Skipped local Pi skill 'gamma'" in output

    cli_module._print_sync_result(
        SyncResult(
            updated=[],
            skipped=[
                SyncSkip("alpha", "ambiguous", source_references=("A:alpha", "B:alpha")),
                SyncSkip("beta", "ambiguous", repo_ids=("A", "B")),
                SyncSkip("gamma", "source-missing", repo_ids=("Missing",)),
                SyncSkip("delta", "other"),
            ],
            backfilled=["legacy"],
        )
    )
    output = capsys.readouterr().out
    assert "Recorded origin for legacy" in output
    assert "multiple source skills match" in output
    assert "multiple source repos match" in output
    assert "orphan state recorded" in output

    with pytest.raises(SvError, match="Duplicate source skill"):
        cli_module._raise_on_duplicate_source_skills(
            [
                _source_skill(tmp_path, "One", "https://github.com/Org/One.git"),
                _source_skill(tmp_path, "Two", "https://github.com/Org/Two.git"),
            ]
        )

    def one_arg_selector(items):
        return [items[0]]

    assert cli_module._call_selector(one_arg_selector, ["alpha"], item_label=str) == ["alpha"]

    def bad_selector(_items, **_kwargs):
        raise TypeError("real failure")

    with pytest.raises(TypeError, match="real failure"):
        cli_module._call_selector(bad_selector, ["alpha"], item_label=str)


def test_cli_vault_replacement_warning_branches(tmp_path: Path, capsys):
    vault_skills = tmp_path / "vault" / "skills"
    target = vault_skills / "alpha"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("---\nname: alpha\ndescription: Alpha\n---\n")
    entry = _source_skill(tmp_path, "Repo", "https://github.com/Org/Repo.git")

    with pytest.raises(SvError, match="Use --replace"):
        cli_module._resolve_vault_replacement(entry, vault_skills, replace_existing=False)

    assert cli_module._resolve_vault_replacement(entry, vault_skills, replace_existing=True)
    assert "not sv-managed" in capsys.readouterr().err

    save_manifest(
        vault_skills,
        {
            "alpha": ManifestEntry(
                name="alpha",
                repo_id="Repo",
                repo_url="url",
                source_path="skills/alpha",
                description="Alpha",
                target_kind="skill-vault",
                target_agent=None,
                target_path="skills/alpha",
            )
        },
    )
    baseline_warning = cli_module._vault_skill_replacement_warning("alpha", target, vault_skills)
    assert baseline_warning is not None
    assert "no recorded baseline" in baseline_warning

    assert cli_module._context_target_kind(cli_module.LocalContext(tmp_path / "project")) == "project-agent"
    assert cli_module._context_target_kind(cli_module.LocalContext(tmp_path / "vault", "skill-vault")) == "skill-vault"


def test_cli_source_table_omits_numbered_choice_column(tmp_path: Path):
    first = _source_skill(tmp_path, "RepoA", "https://github.com/Org/RepoA.git")
    second = _source_skill(tmp_path, "RepoB", "https://github.com/Org/RepoB.git")

    table = cli_module._format_source_skill_choices([first, second])

    lines = table.splitlines()
    assert lines[0] == "Repo          Path          Description               Add as"
    assert lines[2].startswith("RepoA")
    assert lines[3].startswith("RepoB")


def test_add_list_picker_labels_align_skill_source_description_columns(tmp_path: Path):
    short = _source_skill(
        tmp_path, "RepoA", "https://github.com/Org/RepoA.git", name="a"
    )
    longer = _source_skill(
        tmp_path,
        "LongerRepo",
        "https://github.com/Org/LongerRepo.git",
        name="longer-name",
    )

    labeler = cli_module._source_skill_picker_labeler([short, longer])
    first_label = labeler(short)
    second_label = labeler(longer)

    first_source_column = first_label.index(short.repo_id)
    second_source_column = second_label.index(longer.repo_id)
    first_description_column = first_label.index(short.description)
    second_description_column = second_label.index(longer.description)

    assert first_source_column == second_source_column
    assert first_description_column == second_description_column
    assert f"{short.repo_id}:a" not in first_label


def test_duplicate_resolution_prompt_uses_checkbox_table_without_numbered_rows(
    tmp_path: Path, capsys
):
    first = _source_skill(tmp_path, "RepoA", "https://github.com/Org/RepoA.git")
    second = _source_skill(tmp_path, "RepoB", "https://github.com/Org/RepoB.git")

    def choose_first(matches):
        assert matches == [first, second]
        return first

    resolved = cli_module._resolve_selected_duplicate_source_skills(
        [first, second], skill_chooser=choose_first
    )

    assert resolved == [first]
    output = capsys.readouterr().out
    assert "Multiple selected sources provide 'alpha':" in output
    assert "#" not in output.splitlines()[1]
    assert "Choose a skill number" not in output


def test_project_list_remove_and_manifest_target_helper_edges(tmp_path: Path, monkeypatch):
    project_skills = tmp_path / "project" / ".pi" / "skills"
    (project_skills / ".hidden").mkdir(parents=True)
    (project_skills / "alpha").mkdir()
    (project_skills / "alpha" / "SKILL.md").write_text("---\nname: alpha\ndescription: Alpha\n---\n")
    assert project_module.list_project_skills(project_skills) == ["alpha"]
    assert project_module.list_vault_skills(tmp_path / "missing") == []

    with pytest.raises(SvError, match="was not found"):
        project_module.remove_project_skill("missing", project_skills)
    with pytest.raises(SvError, match="was not found"):
        project_module.remove_vault_skill("missing", tmp_path / "vault" / "skills")

    original_iterdir = Path.iterdir

    def fail_iterdir(self):
        if self == project_skills:
            raise OSError("cannot list")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)
    with pytest.raises(SvError, match="Failed to list"):
        project_module.list_project_skills(project_skills)
    monkeypatch.setattr(Path, "iterdir", original_iterdir)

    vault_skills = tmp_path / "vault" / "skills"
    vault_skills.mkdir(parents=True)
    entry = ManifestEntry(
        name="alpha",
        repo_id="Repo",
        repo_url="url",
        source_path="skills/alpha",
        description="Alpha",
        target_kind="skill-vault",
        target_agent=None,
        target_path="skills/alpha",
    )
    other = ManifestEntry(
        name="alpha",
        repo_id="Other",
        repo_url="url",
        source_path="skills/alpha",
        description="Other",
        target_kind="project-agent",
        target_agent="pi",
        target_path=".pi/skills/alpha",
    )
    save_manifest(vault_skills, {"other": other})
    project_module._upsert_manifest_entry_for_target(vault_skills, entry, project_module._VAULT_TARGET)
    entries = load_manifest(vault_skills)
    assert any(item.target_kind == "skill-vault" for item in entries.values())
    project_module._remove_manifest_entry_for_target(vault_skills, "alpha", project_module._VAULT_TARGET)
    assert all(item.target_kind != "skill-vault" for item in load_manifest(vault_skills).values())

    assert project_module._source_reference_for_manifest(entry) == "Repo"
    non_default = ManifestEntry(
        name="alpha",
        repo_id="Repo",
        repo_url="url",
        source_path="other/alpha",
        description="Alpha",
    )
    assert project_module._source_reference_for_manifest(non_default) == "Repo:other/alpha"


def test_project_safety_hash_and_materialization_helper_edges(tmp_path: Path, monkeypatch):
    source = tmp_path / "source" / "alpha"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: alpha\ndescription: Alpha\n---\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_source = tmp_path / "linked-source"
    linked_source.symlink_to(source, target_is_directory=True)
    with pytest.raises(SvError, match="contains a symlink"):
        project_module._ensure_safe_source_skill_tree(linked_source, "alpha")
    (source / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SvError, match="contains a symlink"):
        project_module._ensure_safe_source_skill_tree(source, "alpha")
    (source / "link").unlink()

    original_rglob = Path.rglob

    def fail_rglob(self, pattern):
        if self == source:
            raise OSError("walk failed")
        return original_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", fail_rglob)
    with pytest.raises(SvError, match="Failed to inspect source skill"):
        project_module._ensure_safe_source_skill_tree(source, "alpha")
    monkeypatch.setattr(Path, "rglob", original_rglob)

    entry = _source_skill(tmp_path, "Repo", "https://github.com/Org/Repo.git")
    assert project_module._source_reference_for(entry) == "Repo"
    non_default = SourceSkill(
        name="alpha",
        description="Alpha",
        repo_id="Repo",
        repo_url="url",
        repo_path=entry.repo_path,
        source_path=entry.repo_path / "other" / "alpha",
        source_relative_path="other/alpha",
    )
    assert project_module._source_reference_for(non_default) == "Repo:other/alpha"

    temp_target = tmp_path / "temp" / "alpha"

    class BrokenMaterializer:
        name = "alpha"
        description = "Alpha"
        repo_id = "Repo"
        repo_url = "url"
        source_path = source
        source_relative_path = "skills/alpha"
        repo_aliases = ()
        source_backend = "fake"

        def materialize_to(self, destination):
            destination.mkdir(parents=True)
            raise ValueError("boom")

    with pytest.raises(SvError, match="Failed add: boom"):
        project_module._materialize_entry_to_temp(
            cast(project_module.ProjectSourceSkill, BrokenMaterializer()),
            temp_target,
            skill_name="alpha",
            error_message="Failed add",
        )
    assert not temp_target.exists()


def test_project_refresh_helpers_ignore_empty_and_unchanged_manifests(tmp_path: Path):
    project_skills = tmp_path / "project" / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    project_module.refresh_project_skill_states([], project_skills)
    project_module.refresh_project_skill_local_states(project_skills)

    (project_skills / "alpha").mkdir()
    (project_skills / "alpha" / "SKILL.md").write_text("---\nname: alpha\ndescription: Alpha\n---\n")
    save_manifest(
        project_skills,
        {
            "other": ManifestEntry(
                name="other",
                repo_id="Repo",
                repo_url="url",
                source_path="skills/other",
                description="Other",
                target_kind="project-agent",
                target_agent="other",
                target_path=".pi/skills/other",
            )
        },
    )
    before = (tmp_path / "project" / ".sv" / "manifest.toml").read_text()
    project_module.refresh_project_skill_states([], project_skills)
    project_module.refresh_project_skill_local_states(project_skills)
    assert (tmp_path / "project" / ".sv" / "manifest.toml").read_text() == before


def test_cli_lightweight_refresh_records_and_raises_index_failures(tmp_path: Path, monkeypatch):
    paths = SvPaths.from_home(tmp_path / "home")
    repo = RepoConfig(id="Repo", url="https://github.com/Org/Repo.git")
    failure = source_module.SourceBackendFailure(
        repo_id="Repo",
        repo_url=repo.url,
        backend="fake",
        operation="parsing .sv/index.toml",
        detail="bad toml",
    )

    monkeypatch.setattr(cli_module, "source_backends_for_repo", lambda *args, **kwargs: (object(),))
    monkeypatch.setattr(
        cli_module,
        "build_source_catalog_from_backends",
        lambda *args, **kwargs: SourceCatalogResult(entries=(), failures=(failure,), refreshed_repo_ids=()),
    )

    with pytest.raises(SvError, match="bad toml"):
        cli_module._update_sources_and_catalog_from_repos(
            [repo],
            paths,
            cli_module.default_runner,
            update=True,
            record_global_source_state=True,
            lightweight_discovery=True,
        )

    assert load_global_manifest(paths)["Repo"].last_refresh_status == "error"


def test_cli_lightweight_refresh_partial_failure_branches(tmp_path: Path, monkeypatch):
    paths = SvPaths.from_home(tmp_path / "home")
    good = RepoConfig(id="Good", url="https://github.com/Org/Good.git")
    bad = RepoConfig(id="Bad", url="https://github.com/Org/Bad.git")
    entry = _source_skill(tmp_path, "Good", good.url)
    failure = source_module.SourceBackendFailure(
        repo_id="Bad",
        repo_url=bad.url,
        backend="fake",
        operation="listing candidate SKILL.md files",
        detail="network failed",
    )
    monkeypatch.setattr(cli_module, "source_backends_for_repo", lambda *args, **kwargs: (object(),))
    monkeypatch.setattr(
        cli_module,
        "build_source_catalog_from_backends",
        lambda *args, **kwargs: SourceCatalogResult(
            entries=(entry,), failures=(failure,), refreshed_repo_ids=("Good",)
        ),
    )

    with pytest.raises(SvError, match="network failed"):
        cli_module._update_sources_and_catalog_from_repos(
            [good, bad],
            paths,
            cli_module.default_runner,
            update=True,
            record_global_source_state=True,
            lightweight_discovery=True,
            allow_partial_failures=False,
        )

    catalog = cli_module._update_sources_and_catalog_from_repos(
        [good, bad],
        paths,
        cli_module.default_runner,
        update=True,
        record_global_source_state=True,
        lightweight_discovery=True,
        allow_partial_failures=True,
    )
    assert catalog == [entry]
    states = load_global_manifest(paths)
    assert states["Good"].last_refresh_status == "ok"
    assert states["Bad"].last_refresh_status == "error"


def test_cli_lightweight_refresh_raises_metadata_failure_when_no_entries(tmp_path: Path, monkeypatch):
    paths = SvPaths.from_home(tmp_path / "home")
    repo = RepoConfig(id="Repo", url="https://github.com/Org/Repo.git")
    failure = source_module.SourceBackendFailure(
        repo_id="Repo",
        repo_url=repo.url,
        backend="fake",
        operation="reading candidate SKILL.md file",
        detail="Failed to read SKILL.md: bad frontmatter",
    )
    monkeypatch.setattr(cli_module, "source_backends_for_repo", lambda *args, **kwargs: (object(),))
    monkeypatch.setattr(
        cli_module,
        "build_source_catalog_from_backends",
        lambda *args, **kwargs: SourceCatalogResult(entries=(), failures=(failure,), refreshed_repo_ids=()),
    )

    with pytest.raises(SvError, match="bad frontmatter"):
        cli_module._update_sources_and_catalog_from_repos(
            [repo],
            paths,
            cli_module.default_runner,
            update=True,
            record_global_source_state=True,
            lightweight_discovery=True,
            allow_partial_failures=True,
        )


def test_cli_non_lightweight_refresh_records_failure_and_catalog_success(tmp_path: Path, monkeypatch):
    paths = SvPaths.from_home(tmp_path / "home")
    repo = RepoConfig(id="Repo", url="https://github.com/Org/Repo.git")
    entry = _source_skill(tmp_path, "Repo", repo.url)

    def fail_ensure(*args, **kwargs):
        raise SvError("git failed")

    monkeypatch.setattr(cli_module, "ensure_source_repo", fail_ensure)
    with pytest.raises(SvError, match="git failed"):
        cli_module._update_sources_and_catalog_from_repos(
            [repo],
            paths,
            cli_module.default_runner,
            update=True,
            record_global_source_state=True,
            lightweight_discovery=False,
        )
    assert load_global_manifest(paths)["Repo"].last_refresh_status == "error"

    monkeypatch.setattr(cli_module, "ensure_source_repo", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli_module, "build_source_catalog", lambda repos, paths_arg: [entry])
    catalog = cli_module._update_sources_and_catalog_from_repos(
        [repo],
        paths,
        cli_module.default_runner,
        update=True,
        record_global_source_state=True,
        lightweight_discovery=False,
    )
    assert catalog == [entry]
    assert load_global_manifest(paths)["Repo"].last_refresh_status == "ok"


def test_index_readme_and_scan_error_edges(tmp_path: Path, monkeypatch):
    document = index_module.IndexDocument(kind="project-index", generated_by="sv", generated_at="now", skills=())
    readme = tmp_path / "README.md"
    index_module.update_readme_skill_table(readme, document)
    assert not readme.exists()
    assert index_module.readme_skill_table_is_fresh(readme, document)

    vault_doc = index_module.IndexDocument(
        kind="skill-vault",
        generated_by="sv",
        generated_at="now",
        skills=(
            index_module.IndexSkillEntry(
                name="a|b",
                description="A & <B>",
                source_path="skills/a",
                content_hash="sha256:ed7002b439e9ac845f22357d822bac1444730fbdb6016d3ec9432297b9ec9f73",
                skill_file_hash="sha256:9c53c074d7ac6a2728b638ac1f376c5fa9eb8f71603017c3ea638c2fd40548df",
            ),
        ),
    )
    assert "a\\|b" in index_module._render_readme_skill_table(vault_doc.skills)
    assert "A &amp; &lt;B&gt;" in index_module._render_readme_skill_table(vault_doc.skills)
    assert index_module._replace_or_append_readme_skill_block("", []) == (
        "<!-- sv:skills:start -->\n| Skill | Description |\n| --- | --- |\n<!-- sv:skills:end -->\n"
    )
    assert index_module._replace_or_append_readme_skill_block("Intro\n", []).startswith("Intro\n\n")
    with pytest.raises(SvError, match="exactly one sv skill table"):
        index_module._replace_or_append_readme_skill_block(
            "<!-- sv:skills:start -->\n<!-- sv:skills:start -->\n<!-- sv:skills:end -->",
            [],
        )

    readme.write_text("old")
    original_open = Path.open

    def fail_read(self, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if self == readme and mode == "rb":
            raise OSError("read failed")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_read)
    with pytest.raises(SvError, match="Failed to read README"):
        index_module.readme_skill_table_is_fresh(readme, vault_doc)
    with pytest.raises(SvError, match="Failed to read README"):
        index_module.update_readme_skill_table(readme, vault_doc)
    monkeypatch.setattr(Path, "open", original_open)

    root = tmp_path / "repo"
    (root / "skills" / "alpha").mkdir(parents=True)
    skill_file = root / "skills" / "alpha" / "SKILL.md"
    skill_file.write_text("---\nname: alpha\ndescription: Alpha\n---\n")
    assert index_module._iter_candidate_skill_files(root, ("skills/alpha/SKILL.md",), ()) == [skill_file]
    assert index_module._iter_candidate_skill_files(root, ("missing",), ()) == []
    with pytest.raises(SvError, match="outside repository root"):
        index_module._repo_relative_path(tmp_path / "outside", root)


def test_index_scan_config_validation_and_warn_branches(tmp_path: Path, capsys):
    repo = tmp_path / "repo"
    config_path = repo / ".sv" / "index-config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("include_paths = [\"skills\"]\n")
    with pytest.raises(SvError, match="missing 'schema_version'"):
        index_module.load_index_scan_config(repo)

    config_path.write_text("schema_version = 1\ninclude_paths = [1]\n")
    with pytest.raises(SvError, match="must be a string"):
        index_module.load_index_scan_config(repo)

    config_path.write_text("schema_version = 1\ninclude_paths = [\"bad:name\"]\n")
    with pytest.raises(SvError, match=r"include_paths\[1\] is invalid"):
        index_module.load_index_scan_config(repo)

    warnings: list[str] = []
    index_module._warn_invalid_skill(warnings.append, tmp_path / "bad\npath", SvError("bad\x01skill"))
    assert "bad\\x0apath" in warnings[0]
    assert "bad\\x01skill" in warnings[0]
    index_module._warn_invalid_skill(None, tmp_path / "ignored", SvError("ignored"))
    assert index_module._is_filesystem_error(SvError("Failed to inspect path"))
    assert not index_module._is_filesystem_error(SvError("invalid frontmatter"))


def test_hashing_private_safety_and_error_edges(tmp_path: Path, monkeypatch):
    skill_dir = tmp_path / "alpha"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: alpha\ndescription: Alpha\n---\n")
    with pytest.raises(SvError, match="was not found"):
        hashing_module.sha256_skill_directory(tmp_path / "missing")
    with pytest.raises(SvError, match="Invalid skill directory"):
        hashing_module.sha256_skill_directory(skill_dir, expected_name="bad/name")
    with pytest.raises(SvError, match="regular file"):
        hashing_module.sha256_file(skill_dir)

    outside = tmp_path / "outside"
    outside.mkdir()
    link = skill_dir / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SvError, match="contains a symlink"):
        hashing_module.sha256_skill_directory(skill_dir)
    link.unlink()

    original_rglob = Path.rglob

    def fail_rglob(self, pattern):
        if self == skill_dir:
            raise OSError("walk failed")
        return original_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", fail_rglob)
    with pytest.raises(SvError, match="Failed to list skill directory"):
        hashing_module.sha256_skill_directory(skill_dir)
    monkeypatch.setattr(Path, "rglob", original_rglob)

    with pytest.raises(SvError, match="outside skill directory"):
        hashing_module._safe_relative_path(tmp_path / "outside-file", skill_dir)

    original_is_symlink = Path.is_symlink

    def fail_is_symlink(self):
        if self == skill_dir / "SKILL.md":
            raise OSError("inspect failed")
        return original_is_symlink(self)

    monkeypatch.setattr(Path, "is_symlink", fail_is_symlink)
    with pytest.raises(SvError, match="Failed to inspect file"):
        hashing_module.sha256_file(skill_dir / "SKILL.md")


def test_selector_private_input_helpers_and_table_has_input(monkeypatch):
    values = iter([b"a", b"b", b"\b", b"\xff", b"\n"])
    monkeypatch.setattr(selector_module.os, "read", lambda _fd, _count: next(values))
    assert selector_module._read_filter_query(0) == "a�"

    empty_backspace_values = iter([b"\x7f", b"\n"])
    monkeypatch.setattr(selector_module.os, "read", lambda _fd, _count: next(empty_backspace_values))
    assert selector_module._read_filter_query(0) == ""

    escape_values = iter([b"a", b"\x1b"])
    monkeypatch.setattr(selector_module.os, "read", lambda _fd, _count: next(escape_values))
    assert selector_module._read_filter_query(0) == ""

    monkeypatch.setattr(selector_module, "_has_input", lambda _fd: False)
    assert selector_module._read_escape_sequence(0) == "escape"

    sequence = iter([b"[", b"A", b"[", b"B", b"[", b"C", b"[", b"D", b"[", b"Z", b"X"])
    monkeypatch.setattr(selector_module, "_has_input", lambda _fd: True)
    monkeypatch.setattr(selector_module.os, "read", lambda _fd, _count: next(sequence))
    assert selector_module._read_escape_sequence(0) == "up"
    assert selector_module._read_escape_sequence(0) == "down"
    assert selector_module._read_escape_sequence(0) == "right"
    assert selector_module._read_escape_sequence(0) == "left"
    assert selector_module._read_escape_sequence(0) == "unknown"
    assert selector_module._read_escape_sequence(0) == "escape"

    monkeypatch.setattr(table_module.select, "select", lambda readable, _w, _x, _timeout: (readable, [], []))
    assert table_module._has_input(0)
    monkeypatch.setattr(table_module.select, "select", lambda _r, _w, _x, _timeout: ([], [], []))
    assert not table_module._has_input(0)

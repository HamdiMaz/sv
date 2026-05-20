from pathlib import Path
import sys

import pytest

from sv import cli as cli_module
from sv.config import derive_repo_id
from sv.errors import SvError
from tests.helpers import configure_source, make_source_repo, write_source_skill, run_git


def _write_index(repo: Path, kind: str) -> None:
    (repo / ".sv").mkdir(parents=True, exist_ok=True)
    (repo / ".sv" / "index.toml").write_text(
        "schema_version = 1\n"
        f'kind = "{kind}"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n',
        encoding="utf-8",
    )


class _TtyProxy:
    def __init__(self, wrapped):
        self._wrapped = wrapped

    def isatty(self):
        return True

    def write(self, value):
        return self._wrapped.write(value)

    def flush(self):
        return self._wrapped.flush()

    def fileno(self):
        return 0


@pytest.mark.integration
def test_tty_list_browses_source_skills_with_details_by_default(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    browse_calls = []

    def fake_browse(headers, rows, **kwargs):
        browse_calls.append((headers, rows, kwargs))
        assert headers == ["Skill", "Source", "Description"]
        assert callable(kwargs.get("detail_renderer"))
        assert callable(kwargs.get("detail_actions", {}).get("a"))
        assert kwargs.get("on_detail") is None
        detail_text = kwargs["detail_renderer"](rows[0], None)
        assert "Skill: alpha" in detail_text
        assert "Source: " in detail_text
        assert "Path: " in detail_text
        assert "Description: Alpha skill." in detail_text
        status = kwargs["detail_actions"]["a"](rows[0])
        assert "Added Pi skill 'alpha'" in status
        return None

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["list"], cwd=project, home=home)

    assert result.exit_code == 0
    assert browse_calls
    headers, rows, kwargs = browse_calls[0]
    assert headers == ["Skill", "Source", "Description"]
    assert ["alpha", derive_repo_id(str(source)), "Alpha skill."] in rows
    assert callable(kwargs["detail_renderer"])
    assert callable(kwargs["detail_actions"]["a"])
    assert (project / ".pi" / "skills" / "alpha" / "SKILL.md").is_file()
    assert "Skill: alpha" not in result.stdout
    assert "Added Pi skill" not in result.stdout
    assert "Add as:" not in result.stdout


@pytest.mark.integration
def test_tty_list_falls_back_to_plain_output_when_browser_is_unavailable(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))

    def unavailable_browser(*_args, **_kwargs):
        from sv.errors import SvError

        raise SvError("Interactive table browsing could not read terminal settings.")

    monkeypatch.setattr(cli_module, "_browse_tty_table", unavailable_browser)

    result = run_sv(["list"], cwd=project, home=home)

    assert result.exit_code == 0
    assert "Skill" in result.stdout
    assert "alpha" in result.stdout
    assert "Interactive table browsing" not in result.stderr


@pytest.mark.integration
def test_tty_list_browser_unavailable_fallback_ignores_symlinked_project_sv_metadata(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    target = tmp_path / "external-sv-metadata"
    target.mkdir()
    (project / ".sv").symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))

    def unavailable_browser(*_args, **_kwargs):
        from sv.errors import SvError

        raise SvError("Interactive table browsing could not read terminal settings.")

    monkeypatch.setattr(cli_module, "_browse_tty_table", unavailable_browser)

    result = run_sv(["list"], cwd=project, home=home)

    assert result.exit_code == 0
    assert "Skill" in result.stdout
    assert "alpha" in result.stdout
    assert "Refusing to use symlinked sv metadata" not in result.stderr
    assert "Interactive table browsing" not in result.stderr


@pytest.mark.integration
def test_tty_search_browser_unavailable_fallback_ignores_symlinked_project_sv_metadata(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    target = tmp_path / "external-sv-metadata"
    target.mkdir()
    (project / ".sv").symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))

    def unavailable_browser(*_args, **_kwargs):
        from sv.errors import SvError

        raise SvError("Interactive table browsing could not read terminal settings.")

    monkeypatch.setattr(cli_module, "_browse_tty_table", unavailable_browser)

    result = run_sv(["search", "alpha"], cwd=project, home=home)

    assert result.exit_code == 0
    assert "Rank" in result.stdout
    assert "alpha" in result.stdout
    assert "Refusing to use symlinked sv metadata" not in result.stderr
    assert "Interactive table browsing" not in result.stderr


@pytest.mark.integration
def test_non_tty_list_ignores_symlinked_project_sv_metadata(
    tmp_path: Path, run_sv
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    target = tmp_path / "external-sv-metadata"
    target.mkdir()
    (project / ".sv").symlink_to(target, target_is_directory=True)

    result = run_sv(["list"], cwd=project, home=home)

    assert result.exit_code == 0
    assert "alpha" in result.stdout
    assert "Refusing to use symlinked sv metadata" not in result.stderr


@pytest.mark.integration
def test_non_tty_search_ignores_symlinked_project_sv_metadata(
    tmp_path: Path, run_sv
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    target = tmp_path / "external-sv-metadata"
    target.mkdir()
    (project / ".sv").symlink_to(target, target_is_directory=True)

    result = run_sv(["search", "alpha"], cwd=project, home=home)

    assert result.exit_code == 0
    assert "alpha" in result.stdout
    assert "Refusing to use symlinked sv metadata" not in result.stderr


@pytest.mark.integration
def test_tty_search_browses_ranked_matches_with_details_by_default(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    browse_calls = []

    def fake_browse(headers, rows, **kwargs):
        browse_calls.append((headers, rows, kwargs))
        assert headers == ["Skill", "Source", "Description"]
        assert callable(kwargs.get("detail_renderer"))
        assert callable(kwargs.get("detail_actions", {}).get("a"))
        assert kwargs.get("on_detail") is None
        detail_text = kwargs["detail_renderer"](rows[0], None)
        assert "Skill: alpha" in detail_text
        assert "Source: " in detail_text
        assert "Path: " in detail_text
        assert "Description: Alpha skill." in detail_text
        status = kwargs["detail_actions"]["a"](rows[0])
        assert "Added Pi skill 'alpha'" in status
        return None

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["search", "alpha"], cwd=project, home=home)

    assert result.exit_code == 0
    assert browse_calls
    headers, rows, kwargs = browse_calls[0]
    assert headers == ["Skill", "Source", "Description"]
    assert rows == [["alpha", derive_repo_id(str(source)), "Alpha skill."]]
    assert callable(kwargs["detail_renderer"])
    assert callable(kwargs["detail_actions"]["a"])
    assert (project / ".pi" / "skills" / "alpha" / "SKILL.md").is_file()
    assert "Skill: alpha" not in result.stdout
    assert "Added Pi skill" not in result.stdout


@pytest.mark.integration
def test_tty_detail_add_reports_vault_replacement_as_status_without_prompting(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    (vault / ".git").mkdir(parents=True)
    _write_index(vault, "skill-vault")
    configure_source(source, vault, home)
    first = run_sv(["add", "alpha"], cwd=vault, home=home)
    assert first.exit_code == 0
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    statuses = []

    def fake_input(_prompt):
        raise AssertionError("detail add must not prompt for replacement")

    def fake_browse(_headers, rows, **kwargs):
        status = kwargs["detail_actions"]["a"](rows[0])
        statuses.append(status)
        return None

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["list"], cwd=vault, home=home)

    assert result.exit_code == 0
    assert len(statuses) == 1
    assert "Vault skill 'alpha' already exists" in statuses[0]
    assert "Use --replace to replace it." in statuses[0]
    assert "Replace existing vault skill" not in result.stdout
    assert statuses[0] not in result.stdout


@pytest.mark.integration
def test_tty_detail_add_reports_escaped_refresh_errors(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    statuses = []

    def fail_refresh(_context):
        raise SvError("refresh\x1b failed")

    def fake_browse(_headers, rows, **kwargs):
        statuses.append(kwargs["detail_actions"]["a"](rows[0]))
        return None

    monkeypatch.setattr(cli_module, "_refresh_local_index_if_needed", fail_refresh)
    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["list"], cwd=project, home=home)

    assert result.exit_code == 0
    assert statuses == ["refresh\\x1b failed"]
    assert "refresh\\x1b failed" in statuses[0]
    assert "refresh\x1b failed" not in result.stderr


@pytest.mark.integration
def test_tty_detail_add_with_stale_row_reports_unavailable_without_installing(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    statuses = []

    def fake_browse(_headers, _rows, **kwargs):
        statuses.append(kwargs["detail_actions"]["a"](["missing", "repo", "Gone."]))
        return None

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["list"], cwd=project, home=home)

    assert result.exit_code == 0
    assert statuses == ["Selected skill is no longer available."]
    assert not (project / ".pi" / "skills" / "alpha").exists()


@pytest.mark.integration
def test_tty_detail_renderer_with_stale_row_preserves_status_line(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    detail_texts = []

    def fake_browse(_headers, _rows, **kwargs):
        detail_texts.append(
            kwargs["detail_renderer"](["missing", "repo", "Gone."], "Still open")
        )
        return None

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["list"], cwd=project, home=home)

    assert result.exit_code == 0
    assert detail_texts == ["Status: Still open"]


@pytest.mark.integration
def test_tty_detail_add_reports_escaped_context_detection_errors(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    statuses = []

    def fail_context(_cwd):
        raise SvError("context\x1b failed")

    def fake_browse(_headers, rows, **kwargs):
        statuses.append(kwargs["detail_actions"]["a"](rows[0]))
        return None

    monkeypatch.setattr(cli_module, "_detect_local_context", fail_context)
    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["list"], cwd=project, home=home)

    assert result.exit_code == 0
    assert statuses == ["context\\x1b failed"]
    assert "context\x1b failed" not in result.stderr


@pytest.mark.integration
def test_tty_repo_list_browses_repo_skills_and_can_install_one_or_all(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    write_source_skill(source, "gamma", "Gamma skill.", "gamma v1\n")
    run_git(["add", "skills/gamma"], source)
    run_git(["commit", "-m", "add gamma"], source)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    repo_id = derive_repo_id(str(source))
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    browse_calls = []

    def fake_browse(headers, rows, **kwargs):
        browse_calls.append((headers, rows, kwargs))
        if headers == ["Repo", "URL", "Cache"]:
            kwargs["on_detail"](rows[0])
        elif headers == ["Skill", "Source", "Description"]:
            assert callable(kwargs.get("detail_renderer"))
            assert callable(kwargs.get("detail_actions", {}).get("a"))
            assert callable(kwargs.get("key_actions", {}).get("a"))
            assert kwargs.get("key_help") == "a add all"
            assert kwargs.get("clear_on_exit") is True
            assert kwargs.get("on_detail") is None
            kwargs["key_actions"]["a"](rows[0])
        return None

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["repo", "list"], cwd=project, home=home)

    assert result.exit_code == 0
    assert [call[0] for call in browse_calls] == [
        ["Repo", "URL", "Cache"],
        ["Skill", "Source", "Description"],
    ]
    repo_rows = browse_calls[0][1]
    skill_rows = browse_calls[1][1]
    assert repo_rows[0][0] == repo_id
    assert ["gamma", repo_id, "Gamma skill."] in skill_rows
    assert (project / ".pi" / "skills" / "alpha" / "SKILL.md").is_file()
    assert (project / ".pi" / "skills" / "beta" / "SKILL.md").is_file()
    assert (project / ".pi" / "skills" / "gamma" / "SKILL.md").is_file()
    assert "Added Pi skill 'alpha'" in result.stdout
    assert "Added Pi skill 'gamma'" in result.stdout


@pytest.mark.integration
def test_tty_repo_skill_browser_detail_action_installs_one_skill(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))

    def fake_browse(headers, rows, **kwargs):
        if headers == ["Repo", "URL", "Cache"]:
            kwargs["on_detail"](rows[0])
        elif headers == ["Skill", "Source", "Description"]:
            detail_text = kwargs["detail_renderer"](rows[0], None)
            assert "Skill: alpha" in detail_text
            assert "Description: Alpha skill." in detail_text
            assert not (project / ".pi" / "skills" / "alpha").exists()
            status = kwargs["detail_actions"]["a"](rows[0])
            assert "Added Pi skill 'alpha'" in status
        return None

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["repo", "list"], cwd=project, home=home)

    assert result.exit_code == 0
    assert (project / ".pi" / "skills" / "alpha" / "SKILL.md").is_file()
    assert not (project / ".pi" / "skills" / "beta").exists()
    assert "Added Pi skill 'alpha'" not in result.stdout


@pytest.mark.integration
def test_tty_repo_skill_browser_detail_render_does_not_install_skill(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))

    def fake_browse(headers, rows, **kwargs):
        if headers == ["Repo", "URL", "Cache"]:
            kwargs["on_detail"](rows[0])
        elif headers == ["Skill", "Source", "Description"]:
            detail_text = kwargs["detail_renderer"](rows[0], None)
            assert "Skill: alpha" in detail_text
        return None

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["repo", "list"], cwd=project, home=home)

    assert result.exit_code == 0
    assert not (project / ".pi" / "skills" / "alpha").exists()
    assert not (project / ".pi" / "skills" / "beta").exists()


@pytest.mark.integration
def test_tty_repo_l_alias_opens_repo_browser(tmp_path: Path, run_sv, monkeypatch):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    browse_calls = []

    def fake_browse(headers, rows, **kwargs):
        browse_calls.append((headers, rows, kwargs))
        return None

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["repo", "-l"], cwd=project, home=home)

    assert result.exit_code == 0
    assert browse_calls[0][0] == ["Repo", "URL", "Cache"]

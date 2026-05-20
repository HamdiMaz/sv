from pathlib import Path
import sys

import pytest

from sv import cli as cli_module
from sv.config import derive_repo_id
from tests.helpers import configure_source, make_source_repo, write_source_skill, run_git


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
        kwargs["on_detail"](rows[0])
        return None

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["list"], cwd=project, home=home)

    assert result.exit_code == 0
    assert browse_calls
    headers, rows, kwargs = browse_calls[0]
    assert headers == ["Skill", "Source", "Description"]
    assert ["alpha", derive_repo_id(str(source)), "Alpha skill."] in rows
    assert callable(kwargs["on_detail"])
    assert "Skill: alpha" in result.stdout
    assert "Source: " in result.stdout
    assert "Description: Alpha skill." in result.stdout
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
        kwargs["on_detail"](rows[0])
        return None

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["search", "alpha"], cwd=project, home=home)

    assert result.exit_code == 0
    assert browse_calls
    headers, rows, kwargs = browse_calls[0]
    assert headers == ["Skill", "Source", "Description"]
    assert rows == [["alpha", derive_repo_id(str(source)), "Alpha skill."]]
    assert callable(kwargs["on_detail"])
    assert "Skill: alpha" in result.stdout


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
def test_tty_repo_skill_browser_enter_installs_one_skill(
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
            kwargs["on_detail"](rows[0])
        return None

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["repo", "list"], cwd=project, home=home)

    assert result.exit_code == 0
    assert (project / ".pi" / "skills" / "alpha" / "SKILL.md").is_file()
    assert not (project / ".pi" / "skills" / "beta").exists()
    assert "Added Pi skill 'alpha'" in result.stdout


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


def _open_repo_list_and_capture_skill_rows(
    run_sv,
    *,
    args: list[str],
    project: Path,
    home: Path,
    monkeypatch,
):
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    skill_rows: list[list[str]] = []
    browse_calls: list[tuple[list[str], list[list[str]]]] = []

    def fake_browse(headers, rows, **kwargs):
        normalized_headers = list(headers)
        normalized_rows = [list(row) for row in rows]
        browse_calls.append((normalized_headers, normalized_rows))
        if normalized_headers == ["Repo", "URL", "Cache"]:
            kwargs["on_detail"](rows[0])
        elif normalized_headers == ["Skill", "Source", "Description"]:
            skill_rows.extend(normalized_rows)
        return None

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(args, cwd=project, home=home)
    return result, skill_rows, browse_calls


@pytest.mark.integration
def test_tty_repo_list_uses_fresh_cached_metadata_when_opening_repo(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    assert run_sv(["list"], cwd=project, home=home).exit_code == 0
    write_source_skill(source, "gamma", "Gamma skill.", "gamma v1\n")
    run_git(["add", "skills/gamma"], source)
    run_git(["commit", "-m", "add gamma"], source)

    repo_id = derive_repo_id(str(source))
    result, skill_rows, browse_calls = _open_repo_list_and_capture_skill_rows(
        run_sv,
        args=["repo", "list"],
        project=project,
        home=home,
        monkeypatch=monkeypatch,
    )

    assert result.exit_code == 0
    assert [call[0] for call in browse_calls] == [
        ["Repo", "URL", "Cache"],
        ["Skill", "Source", "Description"],
    ]
    assert ["alpha", repo_id, "Alpha skill."] in skill_rows
    assert ["beta", repo_id, "Beta skill."] in skill_rows
    assert ["gamma", repo_id, "Gamma skill."] not in skill_rows


@pytest.mark.integration
def test_tty_repo_list_refresh_flag_refreshes_metadata_when_opening_repo(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    assert run_sv(["list"], cwd=project, home=home).exit_code == 0
    write_source_skill(source, "gamma", "Gamma skill.", "gamma v1\n")
    run_git(["add", "skills/gamma"], source)
    run_git(["commit", "-m", "add gamma"], source)

    repo_id = derive_repo_id(str(source))
    result, skill_rows, _browse_calls = _open_repo_list_and_capture_skill_rows(
        run_sv,
        args=["repo", "list", "--refresh"],
        project=project,
        home=home,
        monkeypatch=monkeypatch,
    )

    assert result.exit_code == 0
    assert ["gamma", repo_id, "Gamma skill."] in skill_rows


@pytest.mark.integration
def test_tty_repo_list_cached_flag_fails_without_cached_metadata_when_opening_repo(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    result, skill_rows, browse_calls = _open_repo_list_and_capture_skill_rows(
        run_sv,
        args=["repo", "list", "--cached"],
        project=project,
        home=home,
        monkeypatch=monkeypatch,
    )

    assert result.exit_code == 1
    assert skill_rows == []
    assert browse_calls == [
        (
            ["Repo", "URL", "Cache"],
            [
                [
                    derive_repo_id(str(source)),
                    str(source),
                    str(home / ".sv" / "sources" / derive_repo_id(str(source)) / "repo"),
                ]
            ],
        )
    ]
    assert "No cached metadata found" in result.stderr


@pytest.mark.integration
def test_tty_repo_l_alias_cached_flag_uses_fresh_cached_metadata_when_opening_repo(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    assert run_sv(["list"], cwd=project, home=home).exit_code == 0
    write_source_skill(source, "gamma", "Gamma skill.", "gamma v1\n")
    run_git(["add", "skills/gamma"], source)
    run_git(["commit", "-m", "add gamma"], source)

    repo_id = derive_repo_id(str(source))
    result, skill_rows, _browse_calls = _open_repo_list_and_capture_skill_rows(
        run_sv,
        args=["repo", "-l", "--cached"],
        project=project,
        home=home,
        monkeypatch=monkeypatch,
    )

    assert result.exit_code == 0
    assert ["alpha", repo_id, "Alpha skill."] in skill_rows
    assert ["beta", repo_id, "Beta skill."] in skill_rows
    assert ["gamma", repo_id, "Gamma skill."] not in skill_rows


def test_repo_cache_flags_are_rejected_for_non_list_subcommands(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    result = run_sv(["repo", "--refresh", "add", "owner/repo"], cwd=project, home=home)

    assert result.exit_code == 1
    assert "Use --refresh/--cached only with 'sv repo list' or 'sv repo -l'." in result.stderr


def test_repo_list_rejects_conflicting_parent_and_subcommand_cache_flags(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    result = run_sv(["repo", "--refresh", "list", "--cached"], cwd=project, home=home)

    assert result.exit_code == 1
    assert "Use either --refresh or --cached, not both." in result.stderr

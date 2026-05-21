from pathlib import Path
import io
import os
import sys

import pytest

from sv import cli as cli_module
from sv.config import derive_repo_id
from sv.errors import SvError
from sv.runtime import Runtime
from tests.helpers import (
    configure_source,
    display_width,
    make_source_repo,
    write_source_skill,
    run_git,
)


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


class _NonTty(io.StringIO):
    def isatty(self):
        return False


def _detail_visible_text(detail) -> str:
    if isinstance(detail, str):
        return detail
    return "\n".join(
        f"{' ' * getattr(line, 'indent', 0)}{getattr(line, 'text', str(line))}"
        for line in detail
    )


def _detail_visible_lines(detail) -> list[str]:
    return _detail_visible_text(detail).splitlines()


def _non_tty_runtime(*, cwd: Path, home: Path) -> Runtime:
    return Runtime(
        cwd=cwd,
        home=home,
        env=os.environ,
        stdin=_NonTty(),
        stdout=_NonTty(),
        stderr=_NonTty(),
    )


def test_detail_card_helpers_fit_narrow_widths_and_escape_status():
    assert cli_module._detail_card_rule("╭─ Skill", "╮", 4) == "╭..."
    assert cli_module._detail_card_row("alpha", 2) == ".."
    assert cli_module._fit_display_width("alpha", 0) == ""
    assert cli_module._fit_display_width("alpha", 2) == ".."
    assert cli_module._fit_display_width("alphabet", 5) == "al..."
    assert (
        cli_module._format_source_skill_unavailable_detail("bad\x1b")
        == "Status: bad\\x1b"
    )


@pytest.mark.integration
def test_tty_list_browses_source_skills_with_details_by_default(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    write_source_skill(source, "alpha-docs", "Alpha docs.", "alpha docs v1\n")
    write_source_skill(source, "docs", "Docs skill.", "docs v1\n")
    write_source_skill(source, "beta", "Docs in description.", "beta v2\n")
    run_git(["add", "skills"], source)
    run_git(["commit", "-m", "add docs search fixtures"], source)
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
        assert kwargs["search_title"] == "Search skills"
        assert kwargs.get("on_detail") is None
        detail = kwargs["detail_renderer"](rows[0], None)
        detail_text = _detail_visible_text(detail)
        assert "╭─ Skill" in detail_text
        assert "alpha ● available" in detail_text
        assert "Source  " in detail_text
        assert "Path    " in detail_text
        assert "├─ Description" in detail_text
        assert "Alpha skill." in detail_text
        assert kwargs["detail_key_help"] == "a add • q back"
        status = kwargs["detail_actions"]["a"](rows[0])
        assert "Added Pi skill 'alpha'" in status
        return None

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["list"], cwd=project, home=home)

    assert result.exit_code == 0
    assert browse_calls
    headers, rows, kwargs = browse_calls[0]
    assert headers == ["Skill", "Source", "Description"]
    assert ["alpha", derive_repo_id(str(source)), "Alpha skill."] in [
        row[:3] for row in rows
    ]
    assert callable(kwargs["detail_renderer"])
    assert callable(kwargs["detail_actions"]["a"])
    ranker = kwargs["row_ranker"]
    assert callable(ranker)
    rows_by_name = {row[0]: index for index, row in enumerate(rows)}
    assert ranker("docs") == [
        rows_by_name["docs"],
        rows_by_name["alpha-docs"],
        rows_by_name["beta"],
    ]
    assert (project / ".pi" / "skills" / "alpha" / "SKILL.md").is_file()
    assert "Skill: alpha" not in result.stdout
    assert "Added Pi skill" not in result.stdout
    assert "Add as:" not in result.stdout


@pytest.mark.integration
def test_tty_list_detail_card_renders_bordered_sections_footer_and_plain_title(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    detail_frames = []
    title_styles = []
    detail_key_help_values = []

    def fake_browse(_headers, rows, **kwargs):
        detail_key_help_values.append(kwargs["detail_key_help"])
        detail = kwargs["detail_renderer"](rows[0], None)
        detail_frames.append(_detail_visible_lines(detail))
        title_styles.extend(
            getattr(line, "style", "")
            for line in detail
            if getattr(line, "text", "").startswith("│ alpha ● available")
        )
        return None

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["list"], cwd=project, home=home)

    assert result.exit_code == 0
    assert detail_key_help_values == [""]
    assert title_styles == [""]
    frame = detail_frames[0]
    assert frame[0].startswith("╭─ Skill")
    assert frame[0].endswith("╮")
    assert frame[1].startswith("│ alpha ● available")
    assert frame[1].endswith("│")
    assert frame[2].startswith("│ Source  ")
    assert frame[2].endswith("│")
    assert frame[3].startswith("│ Path    ")
    assert frame[3].endswith("│")
    assert frame[4].startswith("├─ Description")
    assert frame[4].endswith("┤")
    assert frame[5].startswith("│ Alpha skill.")
    assert frame[5].endswith("│")
    assert frame[-3].startswith("├")
    assert frame[-3].endswith("┤")
    assert "Description" not in frame[-3]
    assert "Skill" not in frame[-3]
    assert frame[-2].startswith("│ a add • q back")
    assert frame[-2].endswith("│")
    assert frame[-1].startswith("╰")
    assert frame[-1].endswith("╯")
    assert {display_width(line) for line in frame} == {display_width(frame[0])}


@pytest.mark.integration
def test_tty_list_detail_card_renders_status_section_before_footer(
    tmp_path: Path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    detail_frames = []
    status_styles = []

    def fake_browse(_headers, rows, **kwargs):
        status = "Added Pi skill 'alpha' to .pi/skills/alpha."
        detail = kwargs["detail_renderer"](rows[0], status)
        detail_frames.append(_detail_visible_lines(detail))
        status_styles.extend(
            getattr(line, "style", "")
            for line in detail
            if getattr(line, "text", "").startswith("│ Added Pi skill")
        )
        return None

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["list"], cwd=project, home=home)

    assert result.exit_code == 0
    frame = detail_frames[0]
    status_index = next(
        index for index, line in enumerate(frame) if line.startswith("├─ Status")
    )
    assert frame[status_index].endswith("┤")
    assert frame[status_index + 1].startswith("│ Added Pi skill")
    assert frame[status_index + 1].endswith("│")
    assert frame[-2].startswith("│ a add • q back")
    assert frame[-1].startswith("╰")
    assert status_styles == ["success"]


@pytest.mark.integration
def test_list_uses_runtime_streams_for_browse_detection(
    tmp_path: Path, capsys, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    browse_calls = []

    def fake_browse(*_args, **_kwargs):
        browse_calls.append((_args, _kwargs))
        raise AssertionError("runtime non-TTY streams should disable browsing")

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)
    capsys.readouterr()

    result = cli_module.handle(
        cli_module.build_parser().parse_args(["list"]),
        cwd=project,
        home=home,
        runtime=_non_tty_runtime(cwd=project, home=home),
    )
    captured = capsys.readouterr()

    assert result == 0
    assert browse_calls == []
    assert "Skill" in captured.out
    assert "alpha" in captured.out


@pytest.mark.integration
def test_search_uses_runtime_streams_for_browse_detection(
    tmp_path: Path, capsys, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    browse_calls = []

    def fake_browse(*_args, **_kwargs):
        browse_calls.append((_args, _kwargs))
        raise AssertionError("runtime non-TTY streams should disable browsing")

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)
    capsys.readouterr()

    result = cli_module.handle(
        cli_module.build_parser().parse_args(["search", "alpha"]),
        cwd=project,
        home=home,
        runtime=_non_tty_runtime(cwd=project, home=home),
    )
    captured = capsys.readouterr()

    assert result == 0
    assert browse_calls == []
    assert "Rank" in captured.out
    assert "alpha" in captured.out


@pytest.mark.integration
def test_repo_list_uses_runtime_streams_for_browse_detection(
    tmp_path: Path, capsys, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    browse_calls = []

    def fake_browse(*_args, **_kwargs):
        browse_calls.append((_args, _kwargs))
        raise AssertionError("runtime non-TTY streams should disable browsing")

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)
    capsys.readouterr()

    result = cli_module.handle(
        cli_module.build_parser().parse_args(["repo", "list"]),
        cwd=project,
        home=home,
        runtime=_non_tty_runtime(cwd=project, home=home),
    )
    captured = capsys.readouterr()

    assert result == 0
    assert browse_calls == []
    assert "Repo" in captured.out
    assert derive_repo_id(str(source)) in captured.out


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
def test_non_tty_list_ignores_symlinked_project_sv_metadata(tmp_path: Path, run_sv):
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
def test_non_tty_search_ignores_symlinked_project_sv_metadata(tmp_path: Path, run_sv):
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
    write_source_skill(source, "alpha-docs", "Alpha docs.", "alpha docs v1\n")
    write_source_skill(source, "docs", "Docs skill.", "docs v1\n")
    write_source_skill(source, "beta", "Docs in description.", "beta v2\n")
    run_git(["add", "skills"], source)
    run_git(["commit", "-m", "add docs search fixtures"], source)
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
        assert kwargs["search_title"] == "Search skills"
        assert kwargs.get("on_detail") is None
        detail = kwargs["detail_renderer"](rows[0], None)
        detail_text = _detail_visible_text(detail)
        assert "╭─ Skill" in detail_text
        assert "docs ● available" in detail_text
        assert "Source  " in detail_text
        assert "Path    " in detail_text
        assert "├─ Description" in detail_text
        assert "Docs skill." in detail_text
        assert kwargs["detail_key_help"] == "a add • q back"
        status = kwargs["detail_actions"]["a"](rows[0])
        assert "Added Pi skill 'docs'" in status
        return None

    monkeypatch.setattr(cli_module, "_browse_tty_table", fake_browse, raising=False)

    result = run_sv(["search", "docs"], cwd=project, home=home)

    assert result.exit_code == 0
    assert browse_calls
    headers, rows, kwargs = browse_calls[0]
    assert headers == ["Skill", "Source", "Description"]
    repo_id = derive_repo_id(str(source))
    assert [row[:3] for row in rows] == [
        ["docs", repo_id, "Docs skill."],
        ["alpha-docs", repo_id, "Alpha docs."],
        ["beta", repo_id, "Docs in description."],
    ]
    assert callable(kwargs["detail_renderer"])
    assert callable(kwargs["detail_actions"]["a"])
    ranker = kwargs["row_ranker"]
    assert callable(ranker)
    assert ranker("docs") == [0, 1, 2]
    assert (project / ".pi" / "skills" / "docs" / "SKILL.md").is_file()
    assert "Skill: docs" not in result.stdout
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
        status = kwargs["detail_actions"]["a"](rows[0])
        statuses.append(status)
        detail = kwargs["detail_renderer"](rows[0], status)
        assert getattr(detail[-1], "text") == "refresh\\x1b failed"
        assert getattr(detail[-1], "style") == ""
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
    stale_detail = detail_texts[0]
    stale_frame = _detail_visible_lines(stale_detail)
    assert stale_frame[0].startswith("╭─ Status")
    assert stale_frame[0].endswith("╮")
    assert stale_frame[1].startswith("│ Still open")
    assert stale_frame[1].endswith("│")
    assert stale_frame[-2].startswith("│ a add • q back")
    assert stale_frame[-2].endswith("│")
    assert stale_frame[-1].startswith("╰")
    assert stale_frame[-1].endswith("╯")
    assert {display_width(line) for line in stale_frame} == {
        display_width(stale_frame[0])
    }


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
    write_source_skill(source, "alpha-docs", "Alpha docs.", "alpha docs v1\n")
    write_source_skill(source, "docs", "Docs skill.", "docs v1\n")
    write_source_skill(source, "beta", "Docs in description.", "beta v2\n")
    run_git(["add", "skills"], source)
    run_git(["commit", "-m", "add repo browser fixtures"], source)
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
            assert kwargs["search_title"] == "Search skills"
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
    assert browse_calls[0][2]["search_title"] == "Search repositories"
    assert browse_calls[1][2]["search_title"] == "Search skills"
    repo_rows = browse_calls[0][1]
    skill_rows = browse_calls[1][1]
    assert repo_rows[0][0] == repo_id
    assert ["gamma", repo_id, "Gamma skill."] in [row[:3] for row in skill_rows]
    skill_kwargs = browse_calls[1][2]
    ranker = skill_kwargs["row_ranker"]
    assert callable(ranker)
    skill_rows_by_name = {row[0]: index for index, row in enumerate(skill_rows)}
    assert ranker("docs") == [
        skill_rows_by_name["docs"],
        skill_rows_by_name["alpha-docs"],
        skill_rows_by_name["beta"],
    ]
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
            detail = kwargs["detail_renderer"](rows[0], None)
            detail_text = _detail_visible_text(detail)
            assert "╭─ Skill" in detail_text
            assert "alpha ● available" in detail_text
            assert "Source  " in detail_text
            assert "Path    " in detail_text
            assert "├─ Description" in detail_text
            assert "Alpha skill." in detail_text
            assert not (project / ".pi" / "skills" / "alpha").exists()
            status = kwargs["detail_actions"]["a"](rows[0])
            assert "Added Pi skill 'alpha'" in status
            status_detail = kwargs["detail_renderer"](rows[0], status)
            assert getattr(status_detail[-1], "text") == status
            assert getattr(status_detail[-1], "style") == "success"
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
            detail = kwargs["detail_renderer"](rows[0], None)
            detail_text = _detail_visible_text(detail)
            assert "╭─ Skill" in detail_text
            assert "alpha ● available" in detail_text
            assert "Source  " in detail_text
            assert "Path    " in detail_text
            assert "├─ Description" in detail_text
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
            skill_rows.extend(row[:3] for row in normalized_rows)
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
    add_result = run_sv(
        ["repo", "add", str(source), "--no-warm-cache"], cwd=project, home=home
    )
    assert add_result.exit_code == 0

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

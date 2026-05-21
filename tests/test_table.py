import unicodedata

from io import StringIO
import os
import re
import sys

import pytest

from sv.errors import SvError
from sv.search import SearchPromptResult
from sv.table import (
    DetailLine,
    TableState,
    _read_escape_sequence,
    _read_filter_query,
    _read_key,
    _read_search_query,
    _read_key_from_bytes,
    _render_interactive_detail,
    _render_interactive_table,
    browse_table,
    format_table,
)

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def visible_text(value: str) -> str:
    return ANSI_RE.sub("", value)


def display_width(value: str) -> int:
    width = 0
    for char in value:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


class TtyStream(StringIO):
    def isatty(self):
        return True

    def fileno(self):
        return 0


class NonTty(StringIO):
    def isatty(self):
        return False


class FakeTermios:
    TCSADRAIN = 1
    error = OSError

    @staticmethod
    def tcgetattr(fd):
        return ["settings"]

    @staticmethod
    def tcsetattr(fd, when, settings):
        return None


class FakeTty:
    @staticmethod
    def setcbreak(fd):
        return None


class FailingSetCbreakTty:
    @staticmethod
    def setcbreak(fd):
        raise OSError("no cbreak")


class FailingRestoreTermios:
    TCSADRAIN = 1
    error = OSError

    @staticmethod
    def tcgetattr(fd):
        return ["settings"]

    @staticmethod
    def tcsetattr(fd, when, settings):
        raise OSError("restore failed")


def test_interactive_table_renders_headers_highlighted_row_and_footer(monkeypatch):
    monkeypatch.setenv("COLUMNS", "90")
    state = TableState(
        ["Skill", "Description"],
        [
            ["alpha", "Short."],
            ["beta", "This description is intentionally very long."],
        ],
    )
    stdout = StringIO()

    line_count = _render_interactive_table(state, stdout)

    lines = stdout.getvalue().splitlines()
    visible_lines = [visible_text(line) for line in lines]
    assert line_count == 6
    assert visible_lines[0] == "-----  --------------------------------------------"
    assert visible_lines[1] == "Skill  Description"
    assert visible_lines[2] == "-----  --------------------------------------------"
    assert lines[0].startswith("\x1b[38;5;60m")
    assert lines[1].startswith("\x1b[38;5;183m\x1b[1m")
    assert lines[2].startswith("\x1b[38;5;60m")
    assert lines[3].startswith("\x1b[48;5;24m")
    assert visible_lines[3] == "alpha  Short."
    assert visible_lines[4] == "beta   This description is intentionally very long."
    assert (
        visible_lines[5]
        == "Showing 1-2 of 2 • ↑/↓ move • ←/→ page • / search • Enter details • q back"
    )


def test_interactive_table_default_result_page_shows_ten_rows(monkeypatch):
    monkeypatch.setenv("COLUMNS", "90")
    state = TableState(
        ["Skill"],
        [[f"skill-{index}"] for index in range(1, 12)],
    )
    stdout = StringIO()

    line_count = _render_interactive_table(state, stdout)

    visible_lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert line_count == 14
    assert visible_lines[3:13] == [f"skill-{index}" for index in range(1, 11)]
    assert (
        visible_lines[13]
        == "Showing 1-10 of 11 • ↑/↓ move • ←/→ page • / search • Enter details • q back"
    )


def test_interactive_table_truncates_every_line_when_columns_exceed_width(monkeypatch):
    monkeypatch.setenv("COLUMNS", "2")
    state = TableState(["A", "B", "C"], [["alpha", "beta", "gamma"]])
    stdout = StringIO()

    _render_interactive_table(state, stdout)

    visible_lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert all(display_width(line) <= 2 for line in visible_lines)


def test_interactive_table_truncates_rows_to_one_line(monkeypatch):
    monkeypatch.setenv("COLUMNS", "32")
    state = TableState(
        ["Skill", "Description"],
        [["alpha", "This description is much too long for one terminal line."]],
    )
    stdout = StringIO()

    _render_interactive_table(state, stdout)

    visible_lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert all(display_width(line) <= 32 for line in visible_lines)
    assert visible_lines[3].endswith("...")


def test_interactive_table_sanitizes_initial_search_query_in_footer():
    state = TableState(["Skill"], [["alpha"]], search_query="\x1b[2J")
    stdout = StringIO()

    _render_interactive_table(state, stdout, highlight_cursor=False)

    output = stdout.getvalue()
    assert "\x1b[2J" not in output
    assert "\\x1b[2J" in output


def test_interactive_detail_sanitizes_and_fits_lines(monkeypatch):
    monkeypatch.setenv("COLUMNS", "20")
    stdout = StringIO()

    line_count = _render_interactive_detail(
        ["alpha"],
        lambda row, status: ["Skill: " + row[0], "one\ntwo\x1b[2J", "x" * 80],
        None,
        stdout,
        previous_line_count=3,
        detail_key_help="a add skill • q back",
    )

    output = stdout.getvalue()
    visible_lines = [visible_text(line) for line in output.splitlines()]
    assert output.startswith("\x1b[3F\x1b[J")
    assert line_count == 4
    assert "\x1b[2J" not in output
    assert "one\\x0atwo\\x1b[2J" in output
    assert all(display_width(line) <= 20 for line in visible_lines)
    assert visible_lines[-1].startswith("a add skill")


def test_interactive_detail_splits_string_output_into_physical_lines(monkeypatch):
    monkeypatch.setenv("COLUMNS", "80")
    stdout = StringIO()

    line_count = _render_interactive_detail(
        ["alpha"],
        lambda row, status: "Skill: " + row[0] + "\none\ntwo\x1b[2J",
        None,
        stdout,
        detail_key_help="q back",
    )

    output = stdout.getvalue()
    visible_lines = [visible_text(line) for line in output.splitlines()]
    assert line_count == 4
    assert "\\x0a" not in output
    assert "\x1b[2J" not in output
    assert visible_lines == ["Skill: alpha", "one", "two\\x1b[2J", "q back"]


def test_interactive_detail_trims_trailing_empty_sequence_lines_before_footer(
    monkeypatch,
):
    monkeypatch.setenv("COLUMNS", "80")
    stdout = StringIO()

    line_count = _render_interactive_detail(
        ["alpha"],
        lambda row, status: ["Skill: " + row[0], status or ""],
        None,
        stdout,
        detail_key_help="q back",
    )

    visible_lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert line_count == 2
    assert visible_lines == ["Skill: alpha", "q back"]


def test_interactive_detail_default_footer_is_concise(monkeypatch):
    monkeypatch.setenv("COLUMNS", "80")
    stdout = StringIO()

    line_count = _render_interactive_detail(
        ["alpha"],
        lambda row, status: "Skill: " + row[0],
        None,
        stdout,
    )

    visible_lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert line_count == 2
    assert visible_lines == ["Skill: alpha", "a add • q back"]


def test_interactive_detail_omits_empty_footer(monkeypatch):
    monkeypatch.setenv("COLUMNS", "80")
    stdout = StringIO()

    line_count = _render_interactive_detail(
        ["alpha"],
        lambda row, status: "Skill: " + row[0],
        None,
        stdout,
        detail_key_help="",
    )

    visible_lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert line_count == 1
    assert visible_lines == ["Skill: alpha"]


def test_interactive_detail_renders_styled_wrapped_lines_safely(monkeypatch):
    monkeypatch.setenv("COLUMNS", "28")
    stdout = StringIO()

    line_count = _render_interactive_detail(
        ["alpha"],
        lambda row, status: [
            DetailLine("╭─ Skill" + "─" * 32, style="accent"),
            DetailLine("│ safe\x1b title", style="title"),
            DetailLine(
                "Long description with 日本語 characters and emoji ✨",
                wrap=True,
                indent=2,
            ),
        ],
        None,
        stdout,
        detail_key_help="a add • q back",
    )

    output = stdout.getvalue()
    visible_lines = [visible_text(line) for line in output.splitlines()]
    assert "\x1b[38;5;" in output
    assert "safe\x1b title" not in visible_text(output)
    assert "safe\\x1b title" in visible_text(output)
    assert visible_lines[-1] == "a add • q back"
    assert line_count == len(visible_lines)
    assert line_count > 4
    assert all(display_width(line) <= 28 for line in visible_lines)


def test_table_state_keeps_raw_rows_for_details_while_rendering_safely():
    state = TableState(["Skill", "Description"], [["alpha", "line one\nline two"]])
    stdout = StringIO()

    _render_interactive_table(state, stdout, highlight_cursor=False)

    assert state.current_row() == ["alpha", "line one\nline two"]
    output = stdout.getvalue()
    assert "line one\nline two" not in output
    assert "line one\\x0aline two" in output


def test_table_state_navigation_pages_clamps_and_handles_empty_results():
    with pytest.raises(ValueError, match="viewport_size"):
        TableState(["Skill"], [], viewport_size=0)

    state = TableState(
        ["Skill"],
        [["alpha"], ["beta"], ["gamma"], ["delta"]],
        viewport_size=2,
    )

    assert state.visible_end == 2
    assert state.current_row() == ["alpha"]
    state.move_up()
    assert state.current_row() == ["alpha"]
    state.move_down()
    state.move_down()
    assert state.current_row() == ["gamma"]
    assert state.viewport_start == 1
    state.page_previous()
    assert state.current_row() == ["beta"]
    state.page_next()
    assert state.current_row() == ["delta"]
    state.page_next()
    assert state.current_row() == ["delta"]

    state.cursor = 1
    state.viewport_start = 2
    state._scroll_to_cursor()
    assert state.viewport_start == 1

    state.set_filter("no-match")
    assert state.visible_rows() == []
    assert state.current_row() is None
    state.move_down()
    state.page_next()
    state.page_previous()
    assert state.current_row() is None


def test_table_state_slash_search_keeps_matching_rows_and_footer():
    state = TableState(
        ["Skill", "Repo"],
        [["alpha", "Org/A"], ["beta", "Org/B"], ["gamma", "Org/C"]],
    )

    state.set_search("org/c")
    stdout = StringIO()
    _render_interactive_table(state, stdout, highlight_cursor=False)

    visible_lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert visible_lines[3] == "gamma  Org/C"
    assert (
        visible_lines[-1]
        == "Showing 1-1 of 1 matching 3 • search: org/c • ↑/↓ move • ←/→ page • / search • Enter details • q back"
    )


def test_render_table_search_footer_styles_visible_search_text():
    state = TableState(["Skill"], [["alpha"], ["beta"], ["gamma"]])
    state.set_search("ga")
    stdout = StringIO()

    _render_interactive_table(state, stdout)

    visible = visible_text(stdout.getvalue())
    assert "Showing 1-1 of 1 matching 3" in visible
    assert "search: ga" in visible
    assert "↑/↓ move" in visible


def test_render_table_no_match_names_query_and_hint():
    state = TableState(["Skill"], [["alpha"], ["beta"], ["gamma"]])
    state.set_search("missing")
    stdout = StringIO()

    _render_interactive_table(state, stdout)

    visible = visible_text(stdout.getvalue())
    assert "No matches for \"missing\"" in visible
    assert "Try a different search term." in visible
    assert "Showing 0-0 of 0 matching 3" in visible
    assert "search: missing" in visible


def test_table_state_ranked_search_orders_visible_rows():
    state = TableState(["Skill"], [["alpha"], ["docs"], ["docs-helper"], ["find-docs"]])

    state.set_search("docs")

    assert [row[0] for _, row in state.visible_rows()] == [
        "docs",
        "docs-helper",
        "find-docs",
    ]


def test_table_state_accepts_custom_row_ranker_with_original_indices():
    state = TableState(
        ["Skill"],
        [["alpha"], ["docs"], ["docs-helper"], ["find-docs"]],
        row_ranker=lambda query: [3, 1] if query == "docs" else [],
    )

    state.set_search("docs")

    assert state.visible_rows() == [(3, ("find-docs",)), (1, ("docs",))]
    assert state.current_row() == ["find-docs"]


def test_table_state_filter_query_init_aliases_search_and_ranker_ignores_bad_indices():
    state = TableState(
        ["Skill"],
        [["alpha"], ["beta"], ["gamma"]],
        filter_query="ga",
        row_ranker=lambda query: [-1, 2, 2, 99, 1] if query == "ga" else [],
    )

    assert state.search_query == "ga"
    assert state.filter_query == "ga"
    assert state.visible_rows() == [(2, ("gamma",)), (1, ("beta",))]


def test_render_interactive_table_reuses_ranked_visible_indices_from_set_search(
    monkeypatch,
):
    monkeypatch.setenv("COLUMNS", "90")
    call_count = 0

    def ranker(query):
        nonlocal call_count
        call_count += 1
        assert query == "docs"
        return [1, 2]

    state = TableState(
        ["Skill"],
        [["alpha"], ["docs"], ["docs-helper"]],
        row_ranker=ranker,
    )

    state.set_search("docs")
    _render_interactive_table(state, StringIO())

    assert call_count == 1


def test_browse_table_enter_with_detail_renderer_opens_detail_q_returns_to_list_then_exits(
    monkeypatch,
):
    output = TtyStream()
    key_inputs = iter(["enter", "quit", "quit"])
    rendered_rows: list[list[str]] = []

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", _fake_read_key)

    selected = browse_table(
        ["Skill", "Description"],
        [["alpha", "Short."]],
        stdin=TtyStream(),
        stdout=output,
        detail_renderer=lambda row, status: (
            rendered_rows.append(list(row)) or "DETAIL alpha"
        ),
    )

    assert selected is None
    assert rendered_rows == [["alpha", "Short."]]
    rendered = visible_text(output.getvalue())
    assert "DETAIL alpha" in rendered
    assert rendered.count("Skill  Description") >= 3
    assert rendered.rstrip().endswith("q back")


def test_browse_table_detail_action_renders_status_and_stays_in_detail(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["enter", "action:a", "quit", "quit"])
    actions: list[list[str]] = []

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", _fake_read_key)

    def render_detail(row, status):
        lines = [f"DETAIL {row[0]}"]
        if status:
            lines.append(f"Status: {status}")
        return lines

    selected = browse_table(
        ["Skill", "Description"],
        [["alpha", "Short."]],
        stdin=TtyStream(),
        stdout=output,
        detail_renderer=render_detail,
        detail_actions={"a": lambda row: actions.append(list(row)) or "Added alpha"},
    )

    assert selected is None
    assert actions == [["alpha", "Short."]]
    rendered = visible_text(output.getvalue())
    assert "DETAIL alpha" in rendered
    assert "Status: Added alpha" in rendered
    assert rendered.count("DETAIL alpha") >= 2


def test_browse_table_detail_action_none_clears_previous_status(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["enter", "action:a", "action:b", "quit", "quit"])
    actions: list[str] = []
    rendered_statuses: list[str | None] = []

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", _fake_read_key)

    def render_detail(row, status):
        rendered_statuses.append(status)
        lines = [f"DETAIL {row[0]}"]
        if status is not None:
            lines.append(f"Status: {status}")
        return lines

    selected = browse_table(
        ["Skill", "Description"],
        [["alpha", "Short."]],
        stdin=TtyStream(),
        stdout=output,
        detail_renderer=render_detail,
        detail_actions={
            "a": lambda row: actions.append("a") or "Added alpha",
            "b": lambda row: actions.append("b") or None,
        },
    )

    assert selected is None
    assert actions == ["a", "b"]
    assert rendered_statuses == [None, "Added alpha", None]


def test_browse_table_detail_ignores_unmapped_action_until_quit(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["enter", "action:x", "enter", "quit", "quit"])
    rendered_statuses: list[str | None] = []

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", _fake_read_key)

    def render_detail(row, status):
        rendered_statuses.append(status)
        return f"DETAIL {row[0]}"

    selected = browse_table(
        ["Skill", "Description"],
        [["alpha", "Short."]],
        stdin=TtyStream(),
        stdout=output,
        detail_renderer=render_detail,
        detail_actions={"a": lambda _row: "Added alpha"},
    )

    assert selected is None
    assert rendered_statuses == [None]
    rendered = visible_text(output.getvalue())
    assert "DETAIL alpha" in rendered
    assert rendered.count("Skill  Description") >= 3


def test_browse_table_detail_eof_renders_final_table_without_clear(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["enter", "eof"])

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", _fake_read_key)

    selected = browse_table(
        ["Skill", "Description"],
        [["alpha", "Short."]],
        stdin=TtyStream(),
        stdout=output,
        detail_renderer=lambda row, status: f"DETAIL {row[0]}",
    )

    assert selected is None
    rendered = visible_text(output.getvalue())
    assert "DETAIL alpha" in rendered
    assert rendered.count("Skill  Description") >= 2
    assert rendered.rstrip().endswith("q back")
    assert output.getvalue().endswith("\x1b[?25h")


def test_browse_table_detail_eof_clears_without_final_table_when_clear_on_exit(
    monkeypatch,
):
    output = TtyStream()
    key_inputs = iter(["enter", "eof"])

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", _fake_read_key)

    selected = browse_table(
        ["Skill", "Description"],
        [["alpha", "Short."]],
        stdin=TtyStream(),
        stdout=output,
        clear_on_exit=True,
        detail_renderer=lambda row, status: f"DETAIL {row[0]}",
    )

    assert selected is None
    assert "DETAIL alpha" in visible_text(output.getvalue())
    assert output.getvalue().endswith("\x1b[2F\x1b[J\x1b[?25h")


def test_browse_table_escape_returns_from_detail_to_list_then_exits(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["enter", "escape", "quit"])

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", _fake_read_key)

    selected = browse_table(
        ["Skill", "Description"],
        [["alpha", "Short."]],
        stdin=TtyStream(),
        stdout=output,
        detail_renderer=lambda row, status: f"DETAIL {row[0]}",
    )

    assert selected is None
    rendered = visible_text(output.getvalue())
    assert "DETAIL alpha" in rendered
    assert rendered.count("Skill  Description") >= 3
    assert rendered.rstrip().endswith("q back")


def test_browse_table_enter_invokes_detail_hook_and_q_goes_back(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["down", "enter", "quit"])
    details: list[list[str]] = []

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", _fake_read_key)

    def show_detail(row):
        details.append(list(row))
        output.write("DETAIL\n")

    selected = browse_table(
        ["Skill", "Description"],
        [["alpha", "Short."], ["beta", "Details."]],
        stdin=TtyStream(),
        stdout=output,
        on_detail=show_detail,
    )

    assert selected is None
    assert details == [["beta", "Details."]]
    rendered = output.getvalue()
    assert "\x1b[6F\x1b[JDETAIL\n" in rendered


def test_browse_table_applies_slash_search_key(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["search:ga", "enter"])

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", _fake_read_key)

    selected = browse_table(
        ["Skill"],
        [["alpha"], ["beta"], ["gamma"]],
        stdin=TtyStream(),
        stdout=output,
    )

    assert selected == ["gamma"]


def test_browse_table_legacy_filter_key_reads_query(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["filter", "enter"])

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", lambda _fd: next(key_inputs))
    monkeypatch.setattr("sv.table._read_filter_query", lambda _fd: "ga")

    selected = browse_table(
        ["Skill"],
        [["alpha"], ["beta"], ["gamma"]],
        stdin=TtyStream(),
        stdout=output,
    )

    assert selected == ["gamma"]


def test_browse_table_visible_search_enter_applies_and_cancel_preserves(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["filter:alpha", "search", "search", "enter"])
    prompt_results = iter([None, "docs"])

    def _fake_read_key(_fd):
        return next(key_inputs)

    def _fake_read_search_query(_fd, stdout, previous_line_count=0, **_kwargs):
        result = next(prompt_results)
        assert previous_line_count == 5
        stdout.write(f"\x1b[{previous_line_count}F\x1b[J")
        stdout.write("╭search╮\n│prompt│\n│hint│\n╰end╯\n")
        if result is None:
            return SearchPromptResult(applied=False, query="", rendered_line_count=4)
        return SearchPromptResult(applied=True, query=result, rendered_line_count=4)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", _fake_read_key)
    monkeypatch.setattr("sv.table._read_search_query", _fake_read_search_query)

    selected = browse_table(
        ["Skill"],
        [["alpha"], ["docs"], ["docs-helper"], ["find-docs"]],
        stdin=TtyStream(),
        stdout=output,
    )

    assert selected == ["docs"]
    rendered = output.getvalue()
    assert "╰end╯\n\x1b[4F\x1b[J" in rendered
    visible_output = visible_text(rendered)
    assert "search: alpha" in visible_output


def test_browse_table_empty_enter_clears_previous_search(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["search:ga", "search", "enter"])

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", lambda _fd: next(key_inputs))
    monkeypatch.setattr(
        "sv.table._read_search_query",
        lambda *_args, **_kwargs: SearchPromptResult(
            applied=True,
            query="",
            rendered_line_count=4,
        ),
    )

    selected = browse_table(
        ["Skill"],
        [["alpha"], ["beta"], ["gamma"]],
        stdin=TtyStream(),
        stdout=output,
    )

    assert selected == ["alpha"]
    assert "search: ga" not in visible_text(output.getvalue()).splitlines()[-1]


def test_browse_table_search_cancel_leaves_previous_result_selected(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["filter:alpha", "search", "enter"])

    def _fake_read_key(_fd):
        return next(key_inputs)

    def _fake_read_search_query(_fd, stdout, previous_line_count=0, **_kwargs):
        assert previous_line_count == 5
        stdout.write(f"\x1b[{previous_line_count}F\x1b[JSearch: ignored\n")
        return SearchPromptResult(applied=False, query="", rendered_line_count=1)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", _fake_read_key)
    monkeypatch.setattr("sv.table._read_search_query", _fake_read_search_query)

    selected = browse_table(
        ["Skill"],
        [["alpha"], ["docs"], ["docs-helper"], ["find-docs"]],
        stdin=TtyStream(),
        stdout=output,
    )

    assert selected == ["alpha"]
    assert "Search: ignored\n\x1b[1F\x1b[J" in output.getvalue()


def test_browse_table_runs_custom_key_actions_and_stays_in_view(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["action:a", "quit"])
    actions: list[list[str]] = []

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", _fake_read_key)

    selected = browse_table(
        ["Skill", "Description"],
        [["alpha", "Short."]],
        stdin=TtyStream(),
        stdout=output,
        key_actions={"a": lambda row: actions.append(list(row))},
    )

    assert selected is None
    assert actions == [["alpha", "Short."]]


def test_browse_table_can_clear_on_back_without_leaving_final_table(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["quit"])

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", _fake_read_key)

    selected = browse_table(
        ["Skill"],
        [["alpha"]],
        stdin=TtyStream(),
        stdout=output,
        clear_on_exit=True,
    )

    assert selected is None
    assert output.getvalue().endswith("\x1b[5F\x1b[J\x1b[?25h")


def test_browse_table_requires_tty_streams():
    with pytest.raises(SvError, match="requires a TTY"):
        browse_table(["Skill"], [["alpha"]], stdin=NonTty(), stdout=NonTty())


def test_browse_table_reports_cbreak_setup_errors(monkeypatch):
    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FailingSetCbreakTty)

    with pytest.raises(SvError, match="could not configure terminal input"):
        browse_table(["Skill"], [["alpha"]], stdin=TtyStream(), stdout=TtyStream())


def test_browse_table_reports_terminal_restore_errors(monkeypatch):
    key_inputs = iter(["quit"])

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FailingRestoreTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", _fake_read_key)

    with pytest.raises(SvError, match="could not restore terminal settings"):
        browse_table(["Skill"], [["alpha"]], stdin=TtyStream(), stdout=TtyStream())


def test_read_key_from_bytes_decodes_controls_actions_and_invalid_utf8():
    assert _read_key_from_bytes(b"") == "eof"
    assert _read_key_from_bytes(b"\n") == "enter"
    assert _read_key_from_bytes(b"/") == "search"
    assert _read_key_from_bytes(b"Q") == "quit"
    assert _read_key_from_bytes(b"x") == "action:x"
    assert _read_key_from_bytes(b"\xff") == "unknown"
    assert _read_key_from_bytes(b"\x01") == "unknown"


def test_read_key_decodes_actions_quit_enter_eof_unknown_and_invalid_utf8(monkeypatch):
    values = iter([b"x", b"Q", b"\r", b"", b"\x01", b"\xff"])
    monkeypatch.setattr("os.read", lambda _fd, _count: next(values))

    assert _read_key(0) == "action:x"
    assert _read_key(0) == "quit"
    assert _read_key(0) == "enter"
    assert _read_key(0) == "eof"
    assert _read_key(0) == "unknown"
    assert _read_key(0) == "unknown"


def test_table_read_search_query_renders_framed_prompt_and_escapes_controls():
    stdout = StringIO()
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"docs\x01\n")
        os.close(write_fd)
        write_fd = -1

        result = _read_search_query(
            read_fd,
            stdout,
            search_title="Search rows",
            count_label="3 rows",
        )
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)

    assert result == SearchPromptResult(
        applied=True,
        query="docs\x01",
        rendered_line_count=5,
    )
    rendered = stdout.getvalue()
    visible = visible_text(rendered)
    assert "Search rows" in visible
    assert "3 rows" in visible
    assert "⌕ docs\\x01" in visible
    assert "Enter apply • empty Enter clear • Esc cancel" in visible
    assert "docs\x01" not in rendered


def test_read_search_query_returns_none_when_escape_cancels():
    stdout = StringIO()
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"\x1b")
        os.close(write_fd)
        write_fd = -1
        result = _read_search_query(read_fd, stdout)
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)

    assert result.applied is False
    assert result.query == ""
    assert result.rendered_line_count == 5


def test_read_filter_query_handles_backspace_escape_and_replacement(monkeypatch):
    values = iter([b"a", b"b", b"\x7f", b"\xff", b"\n", b"ignored"])
    monkeypatch.setattr("os.read", lambda _fd, _count: next(values))

    assert _read_filter_query(0) == "a�"

    escape_values = iter([b"a", b"\x1b", b"ignored"])
    monkeypatch.setattr("os.read", lambda _fd, _count: next(escape_values))

    assert _read_filter_query(0) == ""


def test_read_filter_query_backspace_removes_complete_multibyte_character(monkeypatch):
    values = iter([b"\xc3", b"\xa9", b"\x7f", b"\n"])
    monkeypatch.setattr("os.read", lambda _fd, _count: next(values))

    assert _read_filter_query(0) == ""


def test_read_filter_query_backspace_after_invalid_utf8_pops_one_byte(monkeypatch):
    values = iter([b"a", b"\xff", b"\x7f", b"\n"])
    monkeypatch.setattr("os.read", lambda _fd, _count: next(values))

    assert _read_filter_query(0) == "a"


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        ([b"[", b"A"], "up"),
        ([b"[", b"B"], "down"),
        ([b"[", b"C"], "right"),
        ([b"[", b"D"], "left"),
        ([b"[", b"Z"], "unknown"),
        ([b"X"], "escape"),
    ],
)
def test_read_escape_sequence_decodes_arrows_and_unknown_sequences(
    monkeypatch, sequence, expected
):
    values = iter(sequence)
    monkeypatch.setattr("sv.table._has_input", lambda _fd: True)
    monkeypatch.setattr("os.read", lambda _fd, _count: next(values))

    assert _read_escape_sequence(0) == expected


def test_read_escape_sequence_treats_lone_escape_as_escape(monkeypatch):
    monkeypatch.setattr("sv.table._has_input", lambda _fd: False)

    assert _read_escape_sequence(0) == "escape"


def test_format_table_aligns_columns():
    output = format_table(
        ["Skill", "Repo", "Description"],
        [
            ["alpha", "Org/A", "Short."],
            ["longer-skill", "Org/B", "Longer description."],
        ],
    )

    assert output.splitlines() == [
        "Skill         Repo   Description",
        "------------  -----  -------------------",
        "alpha         Org/A  Short.",
        "longer-skill  Org/B  Longer description.",
    ]


def test_format_table_handles_no_rows():
    output = format_table(["Repo", "URL"], [])

    assert output.splitlines() == [
        "Repo  URL",
        "----  ---",
    ]


def test_format_table_wraps_long_cells_without_repeating_leading_columns():
    output = format_table(
        ["Skill", "Repo", "Description"],
        [["alpha", "Org/A", "This description wraps cleanly for narrow terminals."]],
        max_widths={"Description": 24},
    )

    assert output.splitlines() == [
        "Skill  Repo   Description",
        "-----  -----  ------------------------",
        "alpha  Org/A  This description wraps",
        "              cleanly for narrow",
        "              terminals.",
    ]


def test_format_table_escapes_control_characters_before_measuring_columns():
    output = format_table(
        ["Skill", "Description"],
        [["alpha", "Line one\nline two\x1b[2J"]],
    )

    assert output.splitlines() == [
        "Skill  Description",
        "-----  ---------------------------",
        "alpha  Line one\\x0aline two\\x1b[2J",
    ]


def test_format_table_wraps_repo_like_values_at_slash_boundaries():
    output = format_table(
        ["Repo"],
        [["OrgName/SkillRepo"]],
        max_widths={"Repo": 10},
    )

    assert output.splitlines() == [
        "Repo",
        "----------",
        "OrgName/",
        "SkillRepo",
    ]


def test_format_table_wraps_qualified_skill_values_at_colon_boundaries():
    output = format_table(
        ["Add as"],
        [["Org/Repo:alpha"]],
        max_widths={"Add as": 9},
    )

    assert output.splitlines() == [
        "Add as",
        "---------",
        "Org/Repo:",
        "alpha",
    ]


def test_format_table_breaks_long_words_to_honor_configured_widths():
    output = format_table(
        ["URL"],
        [["abcdefghijklmnop"]],
        max_widths={"URL": 6},
    )

    assert output.splitlines() == [
        "URL",
        "------",
        "abcdef",
        "ghijkl",
        "mnop",
    ]


def test_format_table_shrinks_single_column_to_fit_total_width():
    output = format_table(
        ["URL"],
        [["abcdefghijklmnop"]],
        max_table_width=6,
    )

    assert output.splitlines() == [
        "URL",
        "------",
        "abcdef",
        "ghijkl",
        "mnop",
    ]


def test_format_table_shrinks_columns_to_fit_total_width():
    output = format_table(
        ["Skill", "Repo", "Description"],
        [["alpha", "Org/A", "This description wraps to fit a narrow terminal."]],
        max_table_width=40,
        min_widths={"Description": 12},
    )

    lines = output.splitlines()
    assert all(len(line) <= 40 for line in lines)
    assert lines == [
        "Skill  Repo   Description",
        "-----  -----  --------------------------",
        "alpha  Org/A  This description wraps to",
        "              fit a narrow terminal.",
    ]


def test_format_table_uses_compact_separators_before_wrapping_headers():
    output = format_table(
        ["Skill", "Repo", "Description"],
        [["alpha", "Org", "Short"]],
        max_table_width=22,
    )

    assert output.splitlines()[0] == "Skill Repo Description"


def test_format_table_uses_compact_separators_for_very_narrow_widths():
    output = format_table(
        ["A", "B", "C"],
        [["alpha", "beta", "gamma"]],
        max_table_width=5,
    )

    assert output.splitlines() == [
        "A B C",
        "- - -",
        "a b g",
        "l e a",
        "p t m",
        "h a m",
        "a   a",
    ]


def test_format_table_hard_wraps_when_width_is_narrower_than_column_count():
    output = format_table(
        ["A", "B", "C"],
        [["alpha", "beta", "gamma"]],
        max_table_width=2,
    )

    assert all(len(line) <= 2 for line in output.splitlines())


def test_format_table_fits_wide_unicode_to_display_width():
    output = format_table(
        ["Skill", "Desc"],
        [["日本語", "abcdefghi"]],
        max_table_width=10,
        min_widths={"Desc": 4},
    )

    assert all(display_width(line) <= 10 for line in output.splitlines())
    assert "日本" in output
    assert "語" in output


def test_format_table_drops_overwide_characters_when_width_is_one():
    output = format_table(["言"], [["語"]], max_table_width=1)

    assert all(display_width(line) <= 1 for line in output.splitlines())
    assert output.splitlines() == ["?", "-", "?"]


def test_format_table_measures_emoji_by_display_width():
    output = format_table(["Name"], [["😀😄😁"]], max_table_width=5)

    lines = output.splitlines()
    assert all(display_width(line) <= 5 for line in lines)
    assert lines == [
        "Name",
        "-----",
        "😀😄",
        "😁",
    ]


def test_format_table_wraps_multiple_wide_symbols_at_narrow_widths():
    output = format_table(["Sym", "Value"], [["😀😄😁", "abc"]], max_table_width=6)

    lines = output.splitlines()
    assert lines == [
        "Sy  Va",
        "m   lu",
        "    e",
        "--  --",
        "😀  ab",
        "😄  c",
        "😁",
    ]
    assert all(display_width(line) <= 6 for line in lines)


def test_format_table_handles_combining_characters_in_narrow_tables():
    output = format_table(["Base", "Desc"], [["Cafe\u0301", "ab"]], max_table_width=4)

    lines = output.splitlines()
    assert all(display_width(line) <= 4 for line in lines)
    assert lines == [
        "B  D",
        "a  e",
        "s  s",
        "e  c",
        "-  -",
        "C  a",
        "a  b",
        "f",
        "é",
    ]


def test_format_table_handles_fewer_and_extra_cells_per_row():
    sparse_row = format_table(
        ["Skill", "Repo", "Description"],
        [["alpha", "Org/A"]],
        max_table_width=30,
    )
    dense_row = format_table(
        ["A", "B"],
        [["alpha", "beta", "gamma", "delta"]],
        max_table_width=20,
    )

    assert sparse_row.splitlines() == [
        "Skill  Repo   Description",
        "-----  -----  -----------",
        "alpha  Org/A",
    ]
    assert dense_row.splitlines() == [
        "A      B",
        "-----  ----",
        "alpha  beta",
    ]


def test_format_table_wraps_sparse_rows_at_tight_widths():
    output = format_table(
        ["Skill", "Repo", "Description"],
        [["alpha"]],
        max_table_width=6,
    )

    assert output.splitlines() == [
        "S R De",
        "k e sc",
        "i p ri",
        "l o pt",
        "l   io",
        "    n",
        "- - --",
        "a",
        "l",
        "p",
        "h",
        "a",
    ]


def test_format_table_ignores_extra_cells_at_tight_widths():
    output = format_table(
        ["A", "B"],
        [["alpha", "beta", "gamma", "delta"]],
        max_table_width=6,
    )

    assert output.splitlines() == [
        "A   B",
        "--  --",
        "al  be",
        "ph  ta",
        "a",
    ]


def test_format_table_treats_zero_or_negative_max_table_width_as_unbounded_with_wide_and_unicode_cells():
    for max_table_width in (0, -1, -2):
        output = format_table(
            ["Emoji", "Wide"],
            [["😀", "日本語"]],
            max_table_width=max_table_width,
        )

        assert output.splitlines() == [
            "EmojiWide",
            "-----------",
            "😀   日本語",
        ]
        assert all("?" not in line for line in output.splitlines())


def test_format_table_handles_empty_string_cells_while_wrapping():
    output = format_table(["A", "B", "C"], [["a", "", "abcdef"]], max_table_width=6)

    assert output.splitlines() == [
        "A B C",
        "- - --",
        "a   ab",
        "    cd",
        "    ef",
    ]
    assert all(display_width(line) <= 6 for line in output.splitlines())


def test_format_table_accepts_column_index_width_overrides():
    output = format_table(
        ["A", "B"],
        [["abcdef", "xy"]],
        max_widths={0: 3},
    )

    assert output.splitlines() == [
        "A    B",
        "---  --",
        "abc  xy",
        "def",
    ]


def test_format_table_replaces_overwide_character_after_current_text():
    output = format_table(["V"], [["a語b"]], max_widths={"V": 1})

    assert output.splitlines() == [
        "V",
        "-",
        "a",
        "?",
        "b",
    ]


def test_format_table_preserves_combining_mark_when_breaking_long_text():
    output = format_table(["Value"], [["abce\u0301def"]], max_widths={"Value": 4})

    assert output.splitlines() == [
        "Value",
        "-----",
        "abcéd",
        "ef",
    ]


def test_format_table_treats_zero_or_negative_max_table_width_as_unbounded():
    for max_table_width in (0, -1, -2):
        output = format_table(
            ["A", "B", "C"],
            [["alpha", "beta", "gamma"]],
            max_table_width=max_table_width,
        )

        assert output.splitlines() == [
            "A    B   C",
            "--------------",
            "alphabetagamma",
        ]
        assert " " not in output.splitlines()[2]

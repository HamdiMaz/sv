from io import StringIO
import builtins
import os
import re
import sys
import unicodedata

import pytest

from sv.errors import SvError
from sv.selector import (
    SelectionState,
    _read_filter_query,
    _read_key,
    _read_search_query,
    _render,
    select_skills,
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


def test_selection_state_scrolls_after_cursor_moves_beyond_fifth_item():
    state = SelectionState([f"skill-{index}" for index in range(1, 7)])

    for _ in range(4):
        state.move_down()

    assert state.cursor == 4
    assert state.viewport_start == 0
    assert [skill for _, skill in state.visible_items()] == [
        "skill-1",
        "skill-2",
        "skill-3",
        "skill-4",
        "skill-5",
    ]

    state.move_down()

    assert state.cursor == 5
    assert state.viewport_start == 1
    assert [skill for _, skill in state.visible_items()] == [
        "skill-2",
        "skill-3",
        "skill-4",
        "skill-5",
        "skill-6",
    ]


def test_selection_state_scrolls_back_up_to_hidden_item():
    state = SelectionState([f"skill-{index}" for index in range(1, 7)])
    for _ in range(5):
        state.move_down()

    state.move_up()
    state.move_up()
    state.move_up()
    state.move_up()
    state.move_up()

    assert state.cursor == 0
    assert state.viewport_start == 0


def test_selection_state_pages_right_and_left_by_visible_window():
    state = SelectionState([f"skill-{index}" for index in range(1, 13)])

    state.page_next()

    assert state.cursor == 5
    assert state.viewport_start == 5
    assert [skill for _, skill in state.visible_items()] == [
        "skill-6",
        "skill-7",
        "skill-8",
        "skill-9",
        "skill-10",
    ]

    state.page_next()

    assert state.cursor == 10
    assert state.viewport_start == 10
    assert [skill for _, skill in state.visible_items()] == ["skill-11", "skill-12"]

    state.page_next()

    assert state.cursor == 10
    assert state.viewport_start == 10

    state.page_previous()

    assert state.cursor == 5
    assert state.viewport_start == 5


def test_selection_state_toggles_and_returns_selected_items_in_list_order():
    state = SelectionState(["alpha", "beta", "gamma"])

    state.move_down()
    state.toggle_current()
    state.move_down()
    state.toggle_current()

    assert state.selected_items() == ["beta", "gamma"]

    state.toggle_current()

    assert state.selected_items() == ["beta"]


def test_selection_state_rejects_empty_viewport():
    with pytest.raises(ValueError, match="viewport_size"):
        SelectionState(["alpha"], viewport_size=0)


def test_read_key_reads_simple_keypresses():
    assert _read_key_from_bytes(b"\x1b[A") == "up"
    assert _read_key_from_bytes(b"\x1b[B") == "down"
    assert _read_key_from_bytes(b"\x1b[D") == "left"
    assert _read_key_from_bytes(b"\x1b[C") == "right"
    assert _read_key_from_bytes(b" ") == "space"
    assert _read_key_from_bytes(b"\r") == "enter"
    assert _read_key_from_bytes(b"q") == "quit"
    assert _read_key_from_bytes(b"\x1b") == "escape"
    assert _read_key_from_bytes(b"") == "eof"


def test_read_key_treats_malformed_escape_sequences_as_escape_or_unknown():
    assert _read_key_from_bytes(b"\x1bX") in {"escape", "unknown"}
    assert _read_key_from_bytes(b"\x1b[") in {"escape", "unknown"}
    assert _read_key_from_bytes(b"\x1b[Z") in {"escape", "unknown"}


def test_render_can_remove_cursor_highlight_after_selection_finishes():
    state = SelectionState(["alpha", "beta"])
    state.selected.add(0)
    stdout = StringIO()

    _render(state, stdout, highlight_cursor=False)

    assert stdout.getvalue().splitlines() == [
        "\x1b[38;5;220m\x1b[1m[x] alpha\x1b[0m",
        "\x1b[38;5;252m[ ]\x1b[0m beta",
        "\x1b[38;5;245mShowing 1-2 of 2 • ↑/↓ move • ←/→ page • Space select • Enter confirm • / search • q cancel\x1b[0m",
    ]


def test_render_outputs_inline_colored_five_item_list_without_alternate_screen():
    state = SelectionState([f"skill {index}" for index in range(1, 7)])
    state.selected.add(1)
    stdout = StringIO()

    line_count = _render(state, stdout)

    assert line_count == 6
    output = stdout.getvalue()
    assert "\x1b[?1049h" not in output
    assert "\x1b[2J" not in output
    assert output.splitlines() == [
        "\x1b[48;5;24m\x1b[38;5;231m\x1b[1m[ ] skill 1\x1b[0m",
        "\x1b[38;5;220m\x1b[1m[x] skill 2\x1b[0m",
        "\x1b[38;5;252m[ ]\x1b[0m skill 3",
        "\x1b[38;5;252m[ ]\x1b[0m skill 4",
        "\x1b[38;5;252m[ ]\x1b[0m skill 5",
        "\x1b[38;5;245mShowing 1-5 of 6 • ↑/↓ move • ←/→ page • Space select • Enter confirm • / search • q cancel\x1b[0m",
    ]


def test_render_uses_checkboxes_without_numeric_row_prefixes():
    state = SelectionState(["alpha", "beta"])
    stdout = StringIO()

    _render(state, stdout, highlight_cursor=False)

    lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert lines[:2] == ["[ ] alpha", "[ ] beta"]
    assert "1-" not in lines[0]
    assert "2-" not in lines[1]


def test_render_uses_custom_item_labels():
    state = SelectionState([{"name": "alpha", "repo": "Org/A"}])
    stdout = StringIO()

    _render(
        state,
        stdout,
        item_label=lambda item: f"{item['name']}  {item['repo']}",
    )

    assert "alpha  Org/A" in stdout.getvalue()


def test_render_can_show_a_header_aligned_after_checkbox_prefix():
    state = SelectionState(["alpha  RepoA  First skill"])
    stdout = StringIO()

    line_count = _render(
        state,
        stdout,
        header_label="Skill  Source  Description",
        highlight_cursor=False,
    )

    lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert line_count == 3
    assert lines[0] == "    Skill  Source  Description"
    assert lines[1] == "[ ] alpha  RepoA  First skill"


def test_render_structured_picker_uses_framed_header_with_sel_column(monkeypatch):
    monkeypatch.setenv("COLUMNS", "100")
    items = [
        {"skill": "a", "source": "Org/A", "description": "Alpha."},
        {"skill": "longer", "source": "Org/Longer", "description": "Longer."},
    ]
    state = SelectionState(items)
    state.selected.add(1)
    stdout = StringIO()

    line_count = _render(
        state,
        stdout,
        highlight_cursor=False,
        item_columns=lambda item: [
            item["skill"],
            item["source"],
            item["description"],
        ],
        header_columns=["Skill", "Source", "Description"],
        enter_action_label="add",
    )

    lines = stdout.getvalue().splitlines()
    visible_lines = [visible_text(line) for line in lines]
    assert line_count == 6
    assert visible_lines[:3] == [
        "---  ------  ----------  -----------",
        "Sel  Skill   Source      Description",
        "---  ------  ----------  -----------",
    ]
    assert lines[0].startswith("\x1b[38;5;60m")
    assert lines[1].startswith("\x1b[38;5;183m\x1b[1m")
    assert visible_lines[3] == "[ ]  a       Org/A       Alpha."
    assert visible_lines[4] == "[x]  longer  Org/Longer  Longer."
    assert visible_lines[5] == "Showing 1-2 of 2 • ↑/↓ move • ←/→ page • Space select • Enter add • / search • q cancel"


def test_render_structured_footer_shows_filter_and_enter_action(monkeypatch):
    monkeypatch.setenv("COLUMNS", "140")
    items = [
        {"skill": "alpha", "source": "Org/A", "description": "Alpha."},
        {"skill": "gamma", "source": "Org/G", "description": "Gamma."},
    ]
    state = SelectionState(items, filter_text=lambda item: item["skill"])
    state.set_filter("ga")
    stdout = StringIO()

    _render(
        state,
        stdout,
        highlight_cursor=False,
        item_columns=lambda item: [
            item["skill"],
            item["source"],
            item["description"],
        ],
        header_columns=["Skill", "Source", "Description"],
        enter_action_label="remove",
    )

    lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert lines[-1] == "Showing 1-1 of 1 matching 2 • search: ga • ↑/↓ move • ←/→ page • Space select • Enter remove • / search • q cancel"


def test_render_escapes_control_characters_in_item_labels():
    state = SelectionState(["alpha\x1b[2J"])
    stdout = StringIO()

    _render(state, stdout)

    output = stdout.getvalue()
    assert "\x1b[2J" not in output
    assert "alpha\\x1b[2J" in output


def test_render_escapes_enter_action_label_in_footer():
    state = SelectionState(["alpha"])
    stdout = StringIO()

    _render(state, stdout, enter_action_label="\x1b[2J")

    output = stdout.getvalue()
    assert "\x1b[2J" not in output
    assert "Enter \\x1b[2J" in output


def test_render_truncates_long_labels_and_footer_to_terminal_width(monkeypatch):
    monkeypatch.setenv("COLUMNS", "32")
    state = SelectionState(["alpha " + "description " * 10])
    stdout = StringIO()

    _render(state, stdout)

    lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert all(display_width(line) <= 32 for line in lines)
    assert lines[0].endswith("...")
    assert lines[-1].startswith("Showing 1-1")
    assert lines[-1].endswith("...")


def test_render_compacts_rows_when_terminal_is_narrower_than_prefix(monkeypatch):
    monkeypatch.setenv("COLUMNS", "4")
    state = SelectionState(["alpha"])
    stdout = StringIO()

    _render(state, stdout)

    lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert all(display_width(line) <= 4 for line in lines)


def test_render_truncates_wide_unicode_labels_to_display_width(monkeypatch):
    monkeypatch.setenv("COLUMNS", "12")
    state = SelectionState(["日本語 description"])
    stdout = StringIO()

    _render(state, stdout)

    lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert all(display_width(line) <= 12 for line in lines)


def test_select_skills_reports_terminal_setup_errors_as_sv_errors():
    class BrokenTty(StringIO):
        def isatty(self):
            return True

        def fileno(self):
            raise OSError("no fd")

    with pytest.raises(SvError, match="terminal"):
        select_skills(["alpha"], stdin=BrokenTty(), stdout=BrokenTty())


class TtyStream(StringIO):
    def isatty(self):
        return True

    def fileno(self):
        return 0


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


def test_select_skills_reports_cbreak_setup_errors_as_sv_errors(monkeypatch):
    class BrokenTty(FakeTty):
        @staticmethod
        def setcbreak(fd):
            raise OSError("cbreak failed")

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", BrokenTty)

    with pytest.raises(SvError, match="configure terminal input"):
        select_skills(["alpha"], stdin=TtyStream(), stdout=TtyStream())


def test_select_skills_reports_terminal_restore_errors_as_sv_errors(monkeypatch):
    class BrokenRestoreTermios(FakeTermios):
        @staticmethod
        def tcsetattr(fd, when, settings):
            raise OSError("restore failed")

    monkeypatch.setitem(sys.modules, "termios", BrokenRestoreTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.selector._read_key", lambda fd: "enter")

    with pytest.raises(SvError, match="restore terminal settings"):
        select_skills(["alpha"], stdin=TtyStream(), stdout=TtyStream())


def test_select_skills_selects_items_in_list_order(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["space", "down", "down", "space", "enter"])
    stdin = TtyStream()

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.selector._read_key", _fake_read_key)

    selected = select_skills(["alpha", "beta", "gamma"], stdin=stdin, stdout=output)

    assert selected == ["alpha", "gamma"]
    rendered = output.getvalue()
    assert "\x1b[?25l" in rendered
    assert "\x1b[?25h" in rendered


def test_select_skills_renders_structured_columns_with_aligned_header(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["enter"])
    items = [
        {"skill": "a", "source": "Org/A", "description": "Alpha."},
        {"skill": "longer", "source": "Org/Longer", "description": "Longer."},
    ]

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.selector._read_key", _fake_read_key)

    select_skills(
        items,
        stdin=TtyStream(),
        stdout=output,
        item_columns=lambda item: [
            item["skill"],
            item["source"],
            item["description"],
        ],
        header_columns=["Skill", "Source", "Description"],
    )

    lines = [visible_text(line) for line in output.getvalue().splitlines()]
    assert "Sel  Skill   Source      Description" in lines
    assert "[ ]  a       Org/A       Alpha." in lines
    assert "[ ]  longer  Org/Longer  Longer." in lines


def test_select_skills_returns_empty_when_cancelled(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["down", "down", "quit"])
    stdin = TtyStream()

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.selector._read_key", _fake_read_key)

    selected = select_skills(["alpha", "beta", "gamma"], stdin=stdin, stdout=output)

    assert selected == []
    assert "\x1b[?25h" in output.getvalue()


def test_select_skills_returns_empty_when_enter_pressed_without_selection(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["enter"])

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.selector._read_key", _fake_read_key)

    selected = select_skills(["alpha", "beta", "gamma"], stdin=TtyStream(), stdout=output)

    assert selected == []
    assert "\x1b[?25h" in output.getvalue()


def test_select_skills_restores_cursor_and_terminal_settings_after_render_error(monkeypatch):
    output = TtyStream()
    restore_calls: list[tuple[int, int, list[str]]] = []

    class RecordingTermios:
        TCSADRAIN = 1
        error = OSError

        @staticmethod
        def tcgetattr(fd):
            return ["settings"]

        @staticmethod
        def tcsetattr(fd, when, settings):
            restore_calls.append((fd, when, settings))
            return None

    monkeypatch.setitem(sys.modules, "termios", RecordingTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.selector._render", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("render failure")))

    with pytest.raises(RuntimeError, match="render failure"):
        select_skills(["alpha"], stdin=TtyStream(), stdout=output)

    assert "\x1b[?25l" in output.getvalue()
    assert "\x1b[?25h" in output.getvalue()
    assert restore_calls == [(0, 1, ["settings"])
    ]


def test_selection_state_boundary_actions_are_noops():
    empty = SelectionState([])
    empty.move_up()
    empty.move_down()
    empty.page_previous()
    empty.page_next()
    empty.toggle_current()

    assert empty.cursor == 0
    assert empty.viewport_start == 0
    assert empty.selected_items() == []

    single = SelectionState(["alpha"])
    single.move_up()
    single.move_down()
    single.page_previous()
    single.page_next()

    assert single.cursor == 0
    assert single.viewport_start == 0


def test_select_skills_returns_empty_before_tty_checks():
    class NonTty(StringIO):
        def isatty(self):
            return False

    assert select_skills([], stdin=NonTty(), stdout=NonTty()) == []


def test_select_skills_requires_tty_streams():
    class NonTty(StringIO):
        def isatty(self):
            return False

    with pytest.raises(SvError, match="requires a TTY"):
        select_skills(["alpha"], stdin=NonTty(), stdout=NonTty())


def test_select_skills_reports_missing_terminal_modules(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "termios":
            raise ImportError("no termios")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(SvError, match="Unix-like terminal"):
        select_skills(["alpha"], stdin=TtyStream(), stdout=TtyStream())


def test_select_skills_handles_navigation_and_unknown_keys(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["down", "right", "left", "up", "unknown", "enter"])

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.selector._read_key", _fake_read_key)

    selected = select_skills(
        [f"skill-{index}" for index in range(1, 8)],
        stdin=TtyStream(),
        stdout=output,
    )

    assert selected == []
    rendered = visible_text(output.getvalue())
    assert "Showing 1-5 of 7" in rendered


def test_render_handles_empty_state_help_line():
    state = SelectionState([])
    stdout = StringIO()

    _render(state, stdout)

    assert visible_text(stdout.getvalue()).strip() == "No skills to show • q cancel"


def test_render_marks_selected_cursor_with_selected_highlight():
    state = SelectionState(["alpha"])
    state.selected.add(0)
    stdout = StringIO()

    _render(state, stdout)

    first_line = stdout.getvalue().splitlines()[0]
    assert first_line.startswith("\x1b[48;5;220m")
    assert "[x] alpha" in first_line


def test_render_handles_combining_marks_and_one_column_terminal(monkeypatch):
    monkeypatch.setenv("COLUMNS", "1")
    state = SelectionState(["e\u0301clair"])
    stdout = StringIO()

    _render(state, stdout)

    lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert lines[0] == "."
    assert all(display_width(line) <= 1 for line in lines)


def test_read_key_returns_unknown_for_regular_characters():
    assert _read_key_from_bytes(b"x") == "unknown"


def test_read_key_starts_slash_searching():
    assert _read_key_from_bytes(b"/") == "search"


def test_selection_state_ranks_visible_items_for_search():
    state = SelectionState(["alpha", "docs", "docs-helper", "find-docs"])

    state.set_search("docs")

    assert [item for _, item in state.visible_items()] == [
        "docs",
        "docs-helper",
        "find-docs",
    ]


def test_selection_state_searches_visible_items_without_losing_selection():
    state = SelectionState(["alpha", "beta", "gamma"])
    state.move_down()
    state.toggle_current()

    state.set_search("ga")

    assert state.search_query == "ga"
    assert state.filter_query == "ga"
    assert state.cursor == 0
    assert [skill for _, skill in state.visible_items()] == ["gamma"]
    assert state.selected_items() == ["beta"]


def test_selection_state_empty_search_restores_all_original_items():
    state = SelectionState(["alpha", "beta", "gamma"])

    state.set_search("ga")
    assert [skill for _, skill in state.visible_items()] == ["gamma"]

    state.set_search("")

    assert state.search_query == ""
    assert state.filter_query == ""
    assert state.cursor == 0
    assert state.viewport_start == 0
    assert [skill for _, skill in state.visible_items()] == ["alpha", "beta", "gamma"]


def test_selection_state_uses_item_ranker_when_provided():
    state = SelectionState(
        ["alpha", "docs", "docs-helper", "find-docs"],
        item_ranker=lambda query: [3, 1] if query == "docs" else [],
    )

    state.set_search("docs")

    assert [item for _, item in state.visible_items()] == ["find-docs", "docs"]


def test_selection_state_filter_query_init_aliases_search_and_ranker_ignores_bad_indices():
    state = SelectionState(
        ["alpha", "beta", "gamma"],
        filter_query="ga",
        item_ranker=lambda query: [-1, 2, 2, 99, 1] if query == "ga" else [],
    )

    assert state.search_query == "ga"
    assert state.filter_query == "ga"
    assert state.visible_items() == [(2, "gamma"), (1, "beta")]


def test_render_footer_shows_slash_search_query():
    state = SelectionState(["alpha", "beta", "gamma"])
    state.set_search("ga")
    stdout = StringIO()

    _render(state, stdout)

    lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert lines == [
        "[ ] gamma",
        "Showing 1-1 of 1 matching 3 • search: ga • ↑/↓ move • ←/→ page • Space select • Enter confirm • / search • q cancel",
    ]


def test_render_footer_shows_stable_no_match_search_query():
    state = SelectionState(["alpha", "beta", "gamma"])
    state.set_search("missing")
    stdout = StringIO()

    _render(state, stdout)

    lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert lines == [
        "Showing 0-0 of 0 matching 3 • search: missing • / search • q cancel",
    ]


def test_read_search_query_renders_visible_prompt_and_escapes_controls():
    stdout = StringIO()
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"docs\x01\n")
        os.close(write_fd)
        write_fd = -1

        query = _read_search_query(read_fd, stdout)
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)

    assert query == "docs\x01"
    rendered = stdout.getvalue()
    assert "Search: docs\\x01" in rendered
    assert "docs\x01" not in rendered


def test_read_search_query_applies_eof_input_and_clears_empty_eof():
    stdout = StringIO()
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"docs")
        os.close(write_fd)
        write_fd = -1
        query = _read_search_query(read_fd, stdout)
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)

    assert query == "docs"

    empty_stdout = StringIO()
    empty_read_fd, empty_write_fd = os.pipe()
    try:
        os.close(empty_write_fd)
        empty_query = _read_search_query(empty_read_fd, empty_stdout)
    finally:
        os.close(empty_read_fd)

    assert empty_query == ""


def test_read_search_query_returns_none_when_escape_cancels():
    stdout = StringIO()
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"\x1b")
        os.close(write_fd)
        write_fd = -1
        query = _read_search_query(read_fd, stdout)
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)

    assert query is None


def test_legacy_read_filter_query_escape_and_invalid_utf8_backspace(monkeypatch):
    escape_values = iter([b"g", b"\x1b", b"ignored"])
    monkeypatch.setattr("os.read", lambda _fd, _count: next(escape_values))
    assert _read_filter_query(0) == ""

    invalid_values = iter([b"g", b"\xff", b"\x7f", b"\n"])
    monkeypatch.setattr("os.read", lambda _fd, _count: next(invalid_values))
    assert _read_filter_query(0) == "g"


def test_set_filter_and_filter_synthetic_key_remain_compatible(monkeypatch):
    state = SelectionState(["alpha", "beta", "gamma"])
    state.set_filter("ga")

    assert state.search_query == "ga"
    assert [skill for _, skill in state.visible_items()] == ["gamma"]

    output = TtyStream()
    key_inputs = iter(["filter:ga", "space", "enter"])
    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.selector._read_key", lambda _fd: next(key_inputs))

    assert select_skills(["alpha", "beta", "gamma"], stdin=TtyStream(), stdout=output) == [
        "gamma"
    ]


def test_select_skills_legacy_filter_key_reads_query(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["filter", "space", "enter"])
    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.selector._read_key", lambda _fd: next(key_inputs))
    monkeypatch.setattr("sv.selector._read_filter_query", lambda _fd: "ga")

    assert select_skills(["alpha", "beta", "gamma"], stdin=TtyStream(), stdout=output) == [
        "gamma"
    ]


def test_select_skills_applies_slash_search_key(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["search:ga", "space", "enter"])

    def _fake_read_key(_fd):
        return next(key_inputs)

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.selector._read_key", _fake_read_key)

    selected = select_skills(
        ["alpha", "beta", "gamma"], stdin=TtyStream(), stdout=output
    )

    assert selected == ["gamma"]


def test_select_skills_real_search_prompt_filters_before_selection(monkeypatch):
    class PipeTty(TtyStream):
        def __init__(self, fd):
            super().__init__()
            self._fd = fd

        def fileno(self):
            return self._fd

    output = TtyStream()
    key_inputs = iter(["search", "space", "enter"])
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"ga\n")
        os.close(write_fd)
        write_fd = -1
        monkeypatch.setitem(sys.modules, "termios", FakeTermios)
        monkeypatch.setitem(sys.modules, "tty", FakeTty)
        monkeypatch.setattr("sv.selector._read_key", lambda _fd: next(key_inputs))

        selected = select_skills(
            ["alpha", "beta", "gamma"], stdin=PipeTty(read_fd), stdout=output
        )
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)

    assert selected == ["gamma"]
    rendered = visible_text(output.getvalue())
    assert "Search: ga" in rendered
    assert "Showing 1-1 of 1 matching 3 • search: ga" in rendered


def test_select_skills_escape_cancelled_search_preserves_previous_search(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["search:ga", "search", "space", "enter"])
    cancelled = iter([None])

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.selector._read_key", lambda _fd: next(key_inputs))
    monkeypatch.setattr("sv.selector._read_search_query", lambda *_args: next(cancelled))

    selected = select_skills(
        ["alpha", "beta", "gamma"], stdin=TtyStream(), stdout=output
    )

    assert selected == ["gamma"]


def _read_key_from_bytes(data: bytes) -> str:
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, data)
        os.close(write_fd)
        write_fd = -1
        return _read_key(read_fd)
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)

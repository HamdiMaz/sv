from io import StringIO
import os
import re
import sys
import unicodedata

import pytest

from sv.errors import SvError
from sv.selector import SelectionState, _read_key, _render, select_skills


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


def test_read_key_supports_left_and_right_arrows():
    assert _read_key_from_bytes(b"\x1b[D") == "left"
    assert _read_key_from_bytes(b"\x1b[C") == "right"


def test_render_can_remove_cursor_highlight_after_selection_finishes():
    state = SelectionState(["alpha", "beta"])
    state.selected.add(0)
    stdout = StringIO()

    _render(state, stdout, highlight_cursor=False)

    assert stdout.getvalue().splitlines() == [
        "\x1b[38;5;220m\x1b[1m[x] 1- alpha\x1b[0m",
        "\x1b[38;5;252m[ ]\x1b[0m \x1b[38;5;245m2-\x1b[0m beta",
        "\x1b[38;5;245mShowing 1-2 of 2 • ↑/↓ move • ←/→ page • Space select • Enter confirm • q cancel\x1b[0m",
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
        "\x1b[48;5;24m\x1b[38;5;231m\x1b[1m[ ] 1- skill 1\x1b[0m",
        "\x1b[38;5;220m\x1b[1m[x] 2- skill 2\x1b[0m",
        "\x1b[38;5;252m[ ]\x1b[0m \x1b[38;5;245m3-\x1b[0m skill 3",
        "\x1b[38;5;252m[ ]\x1b[0m \x1b[38;5;245m4-\x1b[0m skill 4",
        "\x1b[38;5;252m[ ]\x1b[0m \x1b[38;5;245m5-\x1b[0m skill 5",
        "\x1b[38;5;245mShowing 1-5 of 6 • ↑/↓ move • ←/→ page • Space select • Enter confirm • q cancel\x1b[0m",
    ]


def test_render_uses_custom_item_labels():
    state = SelectionState([{"name": "alpha", "repo": "Org/A"}])
    stdout = StringIO()

    _render(
        state,
        stdout,
        item_label=lambda item: f"{item['name']}  {item['repo']}",
    )

    assert "alpha  Org/A" in stdout.getvalue()


def test_render_escapes_control_characters_in_item_labels():
    state = SelectionState(["alpha\x1b[2J"])
    stdout = StringIO()

    _render(state, stdout)

    output = stdout.getvalue()
    assert "\x1b[2J" not in output
    assert "alpha\\x1b[2J" in output


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

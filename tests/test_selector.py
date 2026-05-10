from io import StringIO
import os

import pytest

from sv.selector import SelectionState, _read_key, _render


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
    ]


def test_render_outputs_inline_colored_five_item_list_without_alternate_screen():
    state = SelectionState([f"skill {index}" for index in range(1, 7)])
    state.selected.add(1)
    stdout = StringIO()

    line_count = _render(state, stdout)

    assert line_count == 5
    output = stdout.getvalue()
    assert "\x1b[?1049h" not in output
    assert "\x1b[2J" not in output
    assert output.splitlines() == [
        "\x1b[48;5;24m\x1b[38;5;231m\x1b[1m[ ] 1- skill 1\x1b[0m",
        "\x1b[38;5;220m\x1b[1m[x] 2- skill 2\x1b[0m",
        "\x1b[38;5;252m[ ]\x1b[0m \x1b[38;5;245m3-\x1b[0m skill 3",
        "\x1b[38;5;252m[ ]\x1b[0m \x1b[38;5;245m4-\x1b[0m skill 4",
        "\x1b[38;5;252m[ ]\x1b[0m \x1b[38;5;245m5-\x1b[0m skill 5",
    ]


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

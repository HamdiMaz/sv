from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import os
import select
import sys
from typing import Generic, TextIO, TypeVar

from sv.errors import SvError

T = TypeVar("T")

VIEWPORT_SIZE = 5

_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_FG_TEXT = "\x1b[38;5;252m"
_FG_MUTED = "\x1b[38;5;245m"
_FG_YELLOW = "\x1b[38;5;220m"
_FG_CURSOR = "\x1b[38;5;231m"
_FG_CURSOR_SELECTED = "\x1b[38;5;16m"
_BG_CURSOR = "\x1b[48;5;24m"
_BG_CURSOR_SELECTED = "\x1b[48;5;220m"
_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"


@dataclass
class SelectionState(Generic[T]):
    """State for a scrollable multi-select skill list."""

    items: Sequence[T]
    viewport_size: int = VIEWPORT_SIZE
    cursor: int = 0
    viewport_start: int = 0
    selected: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.viewport_size < 1:
            raise ValueError("viewport_size must be at least 1")

        last_index = max(len(self.items) - 1, 0)
        self.cursor = min(max(self.cursor, 0), last_index)
        self.viewport_start = min(
            max(self.viewport_start, 0), max(len(self.items) - self.viewport_size, 0)
        )
        self.selected = {
            index for index in self.selected if 0 <= index < len(self.items)
        }
        self._scroll_to_cursor()

    @property
    def visible_end(self) -> int:
        return min(self.viewport_start + self.viewport_size, len(self.items))

    def visible_items(self) -> list[tuple[int, T]]:
        return [
            (index, self.items[index])
            for index in range(self.viewport_start, self.visible_end)
        ]

    def move_up(self) -> None:
        if self.cursor == 0:
            return
        self.cursor -= 1
        self._scroll_to_cursor()

    def move_down(self) -> None:
        if self.cursor >= len(self.items) - 1:
            return
        self.cursor += 1
        self._scroll_to_cursor()

    def page_previous(self) -> None:
        if not self.items or self.viewport_start == 0:
            return

        page_size = min(self.viewport_size, self.viewport_start)
        self.cursor = max(self.cursor - page_size, 0)
        self.viewport_start -= page_size

    def page_next(self) -> None:
        if not self.items or self.visible_end >= len(self.items):
            return

        last_index = len(self.items) - 1
        self.cursor = min(self.cursor + self.viewport_size, last_index)
        self.viewport_start += self.viewport_size

    def toggle_current(self) -> None:
        if not self.items:
            return

        if self.cursor in self.selected:
            self.selected.remove(self.cursor)
        else:
            self.selected.add(self.cursor)

    def selected_items(self) -> list[T]:
        return [self.items[index] for index in sorted(self.selected)]

    def _scroll_to_cursor(self) -> None:
        if self.cursor < self.viewport_start:
            self.viewport_start = self.cursor
            return

        if self.cursor >= self.viewport_start + self.viewport_size:
            self.viewport_start = self.cursor - self.viewport_size + 1


def select_skills(
    skills: Sequence[T],
    *,
    viewport_size: int = VIEWPORT_SIZE,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    item_label: Callable[[T], str] = str,
) -> list[T]:
    """Prompt for skills with arrow-key navigation and spacebar selection."""
    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout

    if not skills:
        return []

    if not input_stream.isatty() or not output_stream.isatty():
        raise SvError("Interactive skill selection requires a TTY.")

    try:
        import termios
        import tty
    except ImportError as exc:
        raise SvError(
            "Interactive skill selection requires a Unix-like terminal."
        ) from exc

    state = SelectionState(skills, viewport_size=viewport_size)
    fd = input_stream.fileno()
    original_settings = termios.tcgetattr(fd)

    try:
        tty.setcbreak(fd)
        output_stream.write(_HIDE_CURSOR)
        output_stream.flush()
        rendered_lines = _render(state, output_stream, item_label=item_label)

        while True:
            key = _read_key(fd)
            if key == "up":
                state.move_up()
            elif key == "down":
                state.move_down()
            elif key == "left":
                state.page_previous()
            elif key == "right":
                state.page_next()
            elif key == "space":
                state.toggle_current()
            elif key == "enter":
                _render(
                    state,
                    output_stream,
                    previous_line_count=rendered_lines,
                    highlight_cursor=False,
                    item_label=item_label,
                )
                return state.selected_items()
            elif key in {"escape", "quit", "eof"}:
                _render(
                    state,
                    output_stream,
                    previous_line_count=rendered_lines,
                    highlight_cursor=False,
                    item_label=item_label,
                )
                return []
            else:
                continue

            rendered_lines = _render(
                state,
                output_stream,
                previous_line_count=rendered_lines,
                item_label=item_label,
            )
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, original_settings)
        finally:
            output_stream.write(_SHOW_CURSOR)
            output_stream.flush()


def _render(
    state: SelectionState[T],
    stdout: TextIO,
    *,
    previous_line_count: int = 0,
    highlight_cursor: bool = True,
    item_label: Callable[[T], str] = str,
) -> int:
    if previous_line_count:
        stdout.write(f"\x1b[{previous_line_count}F")
        stdout.write("\x1b[J")

    lines = [
        _format_skill_line(
            state, index, item_label(skill), highlight_cursor=highlight_cursor
        )
        for index, skill in state.visible_items()
    ]
    stdout.write("\n".join(lines))
    stdout.write("\n")
    stdout.flush()
    return len(lines)


def _format_skill_line(
    state: SelectionState[T], index: int, skill: str, *, highlight_cursor: bool = True
) -> str:
    is_selected = index in state.selected
    is_cursor = highlight_cursor and index == state.cursor
    checked = "[x]" if is_selected else "[ ]"
    line = f"{checked} {index + 1}- {skill}"

    if is_cursor and is_selected:
        return f"{_BG_CURSOR_SELECTED}{_FG_CURSOR_SELECTED}{_BOLD}{line}{_RESET}"
    if is_cursor:
        return f"{_BG_CURSOR}{_FG_CURSOR}{_BOLD}{line}{_RESET}"
    if is_selected:
        return f"{_FG_YELLOW}{_BOLD}{line}{_RESET}"

    return f"{_FG_TEXT}{checked}{_RESET} {_FG_MUTED}{index + 1}-{_RESET} {skill}"


def _read_key(fd: int) -> str:
    char = os.read(fd, 1)
    if char == b"":
        return "eof"
    if char in {b"\r", b"\n"}:
        return "enter"
    if char == b" ":
        return "space"
    if char.lower() == b"q":
        return "quit"
    if char == b"\x1b":
        return _read_escape_sequence(fd)
    return "unknown"


def _read_escape_sequence(fd: int) -> str:
    if not _has_input(fd):
        return "escape"

    second = os.read(fd, 1)
    if second not in {b"[", b"O"} or not _has_input(fd):
        return "escape"

    third = os.read(fd, 1)
    if third == b"A":
        return "up"
    if third == b"B":
        return "down"
    if third == b"C":
        return "right"
    if third == b"D":
        return "left"
    return "unknown"


def _has_input(fd: int) -> bool:
    readable, _, _ = select.select([fd], [], [], 0.2)
    return bool(readable)

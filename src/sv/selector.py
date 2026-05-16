from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import os
import select
import shutil
import sys
from typing import Generic, TextIO, TypeVar
import unicodedata

from sv.errors import SvError
from sv.terminal import escape_terminal_controls

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
    filter_query: str = ""
    filter_text: Callable[[T], str] = str

    def __post_init__(self) -> None:
        if self.viewport_size < 1:
            raise ValueError("viewport_size must be at least 1")

        self.filter_query = _sanitize_label(self.filter_query).strip()
        self.selected = {
            index for index in self.selected if 0 <= index < len(self.items)
        }
        self._clamp_view()

    @property
    def visible_end(self) -> int:
        return min(self.viewport_start + self.viewport_size, len(self._filtered_indices()))

    def visible_items(self) -> list[tuple[int, T]]:
        filtered_indices = self._filtered_indices()
        return [
            (index, self.items[index])
            for index in filtered_indices[self.viewport_start : self.visible_end]
        ]

    def move_up(self) -> None:
        if self.cursor == 0:
            return
        self.cursor -= 1
        self._scroll_to_cursor()

    def move_down(self) -> None:
        if self.cursor >= len(self._filtered_indices()) - 1:
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
        if not self._filtered_indices() or self.visible_end >= len(self._filtered_indices()):
            return

        last_index = len(self._filtered_indices()) - 1
        self.cursor = min(self.cursor + self.viewport_size, last_index)
        self.viewport_start += self.viewport_size

    def toggle_current(self) -> None:
        current_index = self._current_item_index()
        if current_index is None:
            return

        if current_index in self.selected:
            self.selected.remove(current_index)
        else:
            self.selected.add(current_index)

    def selected_items(self) -> list[T]:
        return [self.items[index] for index in sorted(self.selected)]

    def set_filter(self, query: str) -> None:
        self.filter_query = _sanitize_label(query).strip()
        self.cursor = 0
        self.viewport_start = 0
        self._clamp_view()

    def _current_item_index(self) -> int | None:
        filtered_indices = self._filtered_indices()
        if not filtered_indices:
            return None
        return filtered_indices[self.cursor]

    def _filtered_indices(self) -> list[int]:
        query = self.filter_query.casefold()
        if not query:
            return list(range(len(self.items)))
        return [
            index
            for index, item in enumerate(self.items)
            if query in _sanitize_label(self.filter_text(item)).casefold()
        ]

    def _clamp_view(self) -> None:
        item_count = len(self._filtered_indices())
        last_index = max(item_count - 1, 0)
        self.cursor = min(max(self.cursor, 0), last_index)
        self.viewport_start = min(
            max(self.viewport_start, 0), max(item_count - self.viewport_size, 0)
        )
        self._scroll_to_cursor()

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
    header_label: str | None = None,
    filter_text: Callable[[T], str] | None = None,
    item_columns: Callable[[T], Sequence[str]] | None = None,
    header_columns: Sequence[str] | None = None,
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

    column_labeler = _column_labeler(skills, item_columns, header_columns)
    effective_item_label = item_label if column_labeler is None else column_labeler
    effective_header_label = (
        header_label
        if column_labeler is None or header_columns is None
        else column_labeler.header_label()
    )
    effective_filter_text = effective_item_label if filter_text is None else filter_text
    state = SelectionState(
        skills,
        viewport_size=viewport_size,
        filter_text=effective_filter_text,
    )
    try:
        fd = input_stream.fileno()
        original_settings = termios.tcgetattr(fd)
    except (AttributeError, OSError, termios.error) as exc:
        raise SvError(
            "Interactive skill selection could not read terminal settings."
        ) from exc

    try:
        try:
            tty.setcbreak(fd)
        except (OSError, termios.error) as exc:
            raise SvError(
                "Interactive skill selection could not configure terminal input."
            ) from exc
        output_stream.write(_HIDE_CURSOR)
        output_stream.flush()
        rendered_lines = _render(
            state,
            output_stream,
            item_label=effective_item_label,
            header_label=effective_header_label,
        )

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
            elif key == "filter":
                state.set_filter(_read_filter_query(fd))
            elif key.startswith("filter:"):
                state.set_filter(key.partition(":")[2])
            elif key == "enter":
                _render(
                    state,
                    output_stream,
                    previous_line_count=rendered_lines,
                    highlight_cursor=False,
                    item_label=effective_item_label,
                    header_label=effective_header_label,
                )
                return state.selected_items()
            elif key in {"escape", "quit", "eof"}:
                _render(
                    state,
                    output_stream,
                    previous_line_count=rendered_lines,
                    highlight_cursor=False,
                    item_label=effective_item_label,
                    header_label=effective_header_label,
                )
                return []
            else:
                continue

            rendered_lines = _render(
                state,
                output_stream,
                previous_line_count=rendered_lines,
                item_label=effective_item_label,
                header_label=effective_header_label,
            )
    finally:
        restore_error: BaseException | None = None
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, original_settings)
        except (OSError, termios.error) as exc:
            restore_error = exc
        finally:
            output_stream.write(_SHOW_CURSOR)
            output_stream.flush()
        if restore_error is not None:
            raise SvError(
                "Interactive skill selection could not restore terminal settings."
            ) from restore_error


@dataclass(frozen=True)
class _ColumnLabeler(Generic[T]):
    item_columns: Callable[[T], Sequence[str]]
    widths: tuple[int, ...]
    header_columns: tuple[str, ...] | None = None

    def __call__(self, item: T) -> str:
        return self._format_row(self.item_columns(item))

    def header_label(self) -> str:
        if self.header_columns is None:
            return ""
        return self._format_row(self.header_columns)

    def _format_row(self, columns: Sequence[str]) -> str:
        sanitized = [_sanitize_label(str(value)) for value in columns]
        padded = [
            value if index == len(sanitized) - 1 else _fit_column(value, self.widths[index])
            for index, value in enumerate(sanitized[: len(self.widths)])
        ]
        if len(sanitized) > len(self.widths):
            padded.extend(sanitized[len(self.widths) :])
        return "  ".join(padded)


def _column_labeler(
    items: Sequence[T],
    item_columns: Callable[[T], Sequence[str]] | None,
    header_columns: Sequence[str] | None,
) -> _ColumnLabeler[T] | None:
    if item_columns is None:
        return None

    rows = [tuple(_sanitize_label(str(value)) for value in item_columns(item)) for item in items]
    headers = None if header_columns is None else tuple(str(value) for value in header_columns)
    column_count = max(
        [len(row) for row in rows] + ([len(headers)] if headers is not None else [0]),
        default=0,
    )
    widths = []
    for index in range(column_count):
        values = [row[index] for row in rows if index < len(row)]
        if headers is not None and index < len(headers):
            values.append(_sanitize_label(headers[index]))
        widths.append(max((_display_width(value) for value in values), default=0))
    return _ColumnLabeler(item_columns, tuple(widths), headers)


def _fit_column(value: str, width: int) -> str:
    return value + " " * max(width - _display_width(value), 0)


def _render(
    state: SelectionState[T],
    stdout: TextIO,
    *,
    previous_line_count: int = 0,
    highlight_cursor: bool = True,
    item_label: Callable[[T], str] = str,
    header_label: str | None = None,
) -> int:
    if previous_line_count:
        stdout.write(f"\x1b[{previous_line_count}F")
        stdout.write("\x1b[J")

    lines: list[str] = []
    if header_label is not None:
        lines.append(_format_header_line(header_label))
    lines.extend(
        _format_skill_line(
            state, index, item_label(skill), highlight_cursor=highlight_cursor
        )
        for index, skill in state.visible_items()
    )
    lines.append(_format_help_line(state))
    stdout.write("\n".join(lines))
    stdout.write("\n")
    stdout.flush()
    return len(lines)


def _format_header_line(header_label: str) -> str:
    prefix_width = _display_width("[ ] ")
    terminal_width = _terminal_width()
    label = _fit_label(header_label, max(terminal_width - prefix_width, 0))
    line = _fit_text(f"{' ' * prefix_width}{label}", terminal_width)
    return f"{_FG_MUTED}{_BOLD}{line}{_RESET}"


def _format_help_line(state: SelectionState[T]) -> str:
    filtered_count = len(state._filtered_indices())
    if state.items and filtered_count:
        text = f"Showing {state.viewport_start + 1}-{state.visible_end} of {filtered_count}"
        if state.filter_query:
            text += f" matching {len(state.items)} • filter: {state.filter_query}"
        text += " • ↑/↓ move • ←/→ page • Space select • Enter confirm • / filter • q cancel"
    elif state.items:
        text = f"Showing 0-0 of 0 matching {len(state.items)}"
        if state.filter_query:
            text += f" • filter: {state.filter_query}"
        text += " • / filter • q cancel"
    else:
        text = "No skills to show • q cancel"
    return f"{_FG_MUTED}{_fit_text(text, _terminal_width())}{_RESET}"


def _format_skill_line(
    state: SelectionState[T], index: int, skill: str, *, highlight_cursor: bool = True
) -> str:
    terminal_width = _terminal_width()
    is_selected = index in state.selected
    is_cursor = highlight_cursor and index == state._current_item_index()
    checked = "[x]" if is_selected else "[ ]"
    prefix = f"{checked} "
    prefix_width = _display_width(prefix)
    label = _fit_label(skill, max(terminal_width - prefix_width, 0))
    line = _fit_text(f"{prefix}{label}", terminal_width)

    if is_cursor and is_selected:
        return f"{_BG_CURSOR_SELECTED}{_FG_CURSOR_SELECTED}{_BOLD}{line}{_RESET}"
    if is_cursor:
        return f"{_BG_CURSOR}{_FG_CURSOR}{_BOLD}{line}{_RESET}"
    if is_selected:
        return f"{_FG_YELLOW}{_BOLD}{line}{_RESET}"
    if _display_width(line) < prefix_width:
        return f"{_FG_TEXT}{line}{_RESET}"

    return f"{_FG_TEXT}{checked}{_RESET} {label}"


def _terminal_width() -> int:
    return max(shutil.get_terminal_size(fallback=(120, 24)).columns, 1)


def _fit_label(value: str, max_width: int) -> str:
    return _fit_text(_sanitize_label(value), max_width)


def _fit_text(value: str, max_width: int) -> str:
    if max_width < 1:
        return ""
    if _display_width(value) <= max_width:
        return value

    ellipsis = "..."[:max_width]
    if max_width <= len(ellipsis):
        return ellipsis

    available_width = max_width - len(ellipsis)
    trimmed: list[str] = []
    current_width = 0
    for char in value:
        char_width = _character_width(char)
        if char_width == 0:
            trimmed.append(char)
            continue
        if current_width + char_width > available_width:
            break
        trimmed.append(char)
        current_width += char_width
    return "".join(trimmed).rstrip() + ellipsis


def _sanitize_label(value: str) -> str:
    return escape_terminal_controls(value)


def _display_width(value: str) -> int:
    return sum(_character_width(char) for char in value)


def _character_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    if unicodedata.east_asian_width(char) in {"F", "W"}:
        return 2
    return 1


def _read_key(fd: int) -> str:
    char = os.read(fd, 1)
    if char == b"":
        return "eof"
    if char in {b"\r", b"\n"}:
        return "enter"
    if char == b" ":
        return "space"
    if char == b"/":
        return "filter"
    if char.lower() == b"q":
        return "quit"
    if char == b"\x1b":
        return _read_escape_sequence(fd)
    return "unknown"


def _read_filter_query(fd: int) -> str:
    query = bytearray()
    while True:
        char = os.read(fd, 1)
        if char in {b"", b"\r", b"\n"}:
            break
        if char == b"\x1b":
            return ""
        if char in {b"\x7f", b"\b"}:
            if query:
                query.pop()
            continue
        query.extend(char)
    return query.decode(errors="replace")


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

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
import select
import shutil
import sys
import textwrap
from typing import TextIO
import unicodedata

from sv.errors import SvError
from sv.terminal import escape_terminal_controls


CellWidths = Mapping[str | int, int]

VIEWPORT_SIZE = 5

_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_FG_CURSOR = "\x1b[38;5;231m"
_FG_MUTED = "\x1b[38;5;245m"
_BG_CURSOR = "\x1b[48;5;24m"
_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"


@dataclass
class TableState:
    """State for a read-only, filterable interactive table."""

    headers: Sequence[str]
    rows: Sequence[Sequence[str]]
    viewport_size: int = VIEWPORT_SIZE
    cursor: int = 0
    viewport_start: int = 0
    filter_query: str = ""
    key_help: str = ""

    def __post_init__(self) -> None:
        if self.viewport_size < 1:
            raise ValueError("viewport_size must be at least 1")
        self.headers = tuple(str(header) for header in self.headers)
        self.rows = [tuple(str(cell) for cell in row) for row in self.rows]
        self.filter_query = _sanitize_cell(self.filter_query).strip()
        self._clamp_view()

    @property
    def visible_end(self) -> int:
        return min(self.viewport_start + self.viewport_size, len(self._filtered_indices()))

    def visible_rows(self) -> list[tuple[int, Sequence[str]]]:
        filtered_indices = self._filtered_indices()
        return [
            (index, self.rows[index])
            for index in filtered_indices[self.viewport_start : self.visible_end]
        ]

    def current_row(self) -> Sequence[str] | None:
        filtered_indices = self._filtered_indices()
        if not filtered_indices:
            return None
        return list(self.rows[filtered_indices[self.cursor]])

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
        if not self._filtered_indices() or self.viewport_start == 0:
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

    def set_filter(self, query: str) -> None:
        self.filter_query = _sanitize_cell(query).strip()
        self.cursor = 0
        self.viewport_start = 0
        self._clamp_view()

    def _filtered_indices(self) -> list[int]:
        query = self.filter_query.casefold()
        if not query:
            return list(range(len(self.rows)))
        return [
            index
            for index, row in enumerate(self.rows)
            if query in " ".join(row).casefold()
        ]

    def _clamp_view(self) -> None:
        row_count = len(self._filtered_indices())
        last_index = max(row_count - 1, 0)
        self.cursor = min(max(self.cursor, 0), last_index)
        self.viewport_start = min(
            max(self.viewport_start, 0), max(row_count - self.viewport_size, 0)
        )
        self._scroll_to_cursor()

    def _scroll_to_cursor(self) -> None:
        if self.cursor < self.viewport_start:
            self.viewport_start = self.cursor
            return
        if self.cursor >= self.viewport_start + self.viewport_size:
            self.viewport_start = self.cursor - self.viewport_size + 1


def browse_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    viewport_size: int = VIEWPORT_SIZE,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    on_detail: Callable[[Sequence[str]], object] | None = None,
    key_actions: Mapping[str, Callable[[Sequence[str]], object]] | None = None,
    key_help: str = "",
    clear_on_exit: bool = False,
) -> Sequence[str] | None:
    """Browse rows in a read-only TTY table.

    Arrow keys move the highlighted current row, ``/`` filters, Enter returns the
    current row or invokes ``on_detail``, and ``q``/Escape goes back.
    """
    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout

    if not input_stream.isatty() or not output_stream.isatty():
        raise SvError("Interactive table browsing requires a TTY.")

    try:
        import termios
        import tty
    except ImportError as exc:
        raise SvError("Interactive table browsing requires a Unix-like terminal.") from exc

    state = TableState(headers, rows, viewport_size=viewport_size, key_help=key_help)
    actions = {key.casefold(): action for key, action in (key_actions or {}).items()}
    try:
        fd = input_stream.fileno()
        original_settings = termios.tcgetattr(fd)
    except (AttributeError, OSError, termios.error) as exc:
        raise SvError("Interactive table browsing could not read terminal settings.") from exc

    try:
        try:
            tty.setcbreak(fd)
        except (OSError, termios.error) as exc:
            raise SvError(
                "Interactive table browsing could not configure terminal input."
            ) from exc
        output_stream.write(_HIDE_CURSOR)
        output_stream.flush()
        rendered_lines = _render_interactive_table(state, output_stream)

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
            elif key == "filter":
                state.set_filter(_read_filter_query(fd))
            elif key.startswith("filter:"):
                state.set_filter(key.partition(":")[2])
            elif key == "enter":
                row = state.current_row()
                if row is None:
                    continue
                if on_detail is None:
                    _render_interactive_table(
                        state,
                        output_stream,
                        previous_line_count=rendered_lines,
                        highlight_cursor=False,
                    )
                    return list(row)
                _clear_rendered_table(output_stream, rendered_lines)
                rendered_lines = 0
                on_detail(row)
            elif key.startswith("action:"):
                action_key = key.partition(":")[2].casefold()
                action = actions.get(action_key)
                row = state.current_row()
                if action is not None and row is not None:
                    _clear_rendered_table(output_stream, rendered_lines)
                    rendered_lines = 0
                    action(row)
            elif key in {"escape", "quit", "eof"}:
                if clear_on_exit:
                    _clear_rendered_table(output_stream, rendered_lines)
                else:
                    _render_interactive_table(
                        state,
                        output_stream,
                        previous_line_count=rendered_lines,
                        highlight_cursor=False,
                    )
                return None
            else:
                continue

            rendered_lines = _render_interactive_table(
                state,
                output_stream,
                previous_line_count=rendered_lines,
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
                "Interactive table browsing could not restore terminal settings."
            ) from restore_error


def _clear_rendered_table(stdout: TextIO, rendered_lines: int) -> None:
    if rendered_lines:
        stdout.write(f"\x1b[{rendered_lines}F")
        stdout.write("\x1b[J")
        stdout.flush()


def _render_interactive_table(
    state: TableState,
    stdout: TextIO,
    *,
    previous_line_count: int = 0,
    highlight_cursor: bool = True,
) -> int:
    if previous_line_count:
        stdout.write(f"\x1b[{previous_line_count}F")
        stdout.write("\x1b[J")

    terminal_width = _terminal_width()
    widths = _interactive_column_widths(state, terminal_width)
    separator = _column_separator(widths, terminal_width)
    lines = [
        _fit_text(_format_interactive_row(state.headers, widths, separator), terminal_width),
        _fit_text(
            _format_interactive_row(["-" * width for width in widths], widths, separator),
            terminal_width,
        ),
    ]
    if not state.visible_rows():
        lines.append(_fit_text("No rows to show", terminal_width))
    for row_index, row in state.visible_rows():
        line = _fit_text(_format_interactive_row(row, widths, separator), terminal_width)
        filtered_indices = state._filtered_indices()
        cursor_row_index = filtered_indices[state.cursor] if filtered_indices else -1
        if highlight_cursor and row_index == cursor_row_index:
            line = f"{_BG_CURSOR}{_FG_CURSOR}{_BOLD}{_fit_text(line, terminal_width)}{_RESET}"
        lines.append(line)
    lines.append(_format_table_help_line(state, terminal_width))

    stdout.write("\n".join(lines))
    stdout.write("\n")
    stdout.flush()
    return len(lines)


def _interactive_column_widths(state: TableState, terminal_width: int) -> list[int]:
    rows = [tuple(_sanitize_cell(cell) for cell in row) for _, row in state.visible_rows()]
    if not rows:
        rows = [()]
    headers = tuple(_sanitize_cell(header) for header in state.headers)
    return _column_widths(headers, rows, None, None, terminal_width)


def _format_interactive_row(
    row: Sequence[str], widths: Sequence[int], separator: str
) -> str:
    cells = []
    for index, width in enumerate(widths):
        value = _sanitize_cell(row[index]) if index < len(row) else ""
        cells.append(_pad_to_width(_fit_text(value, width), width))
    return separator.join(cells).rstrip()


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


def _format_table_help_line(state: TableState, terminal_width: int) -> str:
    filtered_count = len(state._filtered_indices())
    total_count = len(state.rows)
    if filtered_count:
        text = f"Showing {state.viewport_start + 1}-{state.visible_end} of {filtered_count}"
    else:
        text = "Showing 0-0 of 0"
    if state.filter_query:
        text += f" matching {total_count} • filter: {state.filter_query}"
    text += " • ↑/↓ move • ←/→ page • / filter • Enter details"
    if state.key_help:
        text += f" • {state.key_help}"
    text += " • q back"
    return f"{_FG_MUTED}{_fit_text(text, terminal_width)}{_RESET}"


def _terminal_width() -> int:
    return max(shutil.get_terminal_size(fallback=(120, 24)).columns, 1)


def _read_key(fd: int) -> str:
    char = os.read(fd, 1)
    if char == b"":
        return "eof"
    if char in {b"\r", b"\n"}:
        return "enter"
    if char == b"/":
        return "filter"
    if char.lower() == b"q":
        return "quit"
    if char == b"\x1b":
        return _read_escape_sequence(fd)
    try:
        decoded = char.decode("utf-8")
    except UnicodeDecodeError:
        return "unknown"
    if decoded.isprintable():
        return f"action:{decoded.casefold()}"
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


def format_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    max_widths: CellWidths | None = None,
    min_widths: CellWidths | None = None,
    max_table_width: int | None = None,
) -> str:
    """Format a readable plain-text table with optional cell wrapping."""
    normalized_headers = tuple(_sanitize_cell(header) for header in headers)
    normalized_rows = [tuple(_sanitize_cell(value) for value in row) for row in rows]
    widths = _column_widths(
        normalized_headers,
        normalized_rows,
        max_widths,
        min_widths,
        max_table_width,
    )
    separator = _column_separator(widths, max_table_width)

    def format_physical_row(row: Sequence[str]) -> list[str]:
        wrapped_cells = [
            _wrap_cell(row[index] if index < len(row) else "", widths[index])
            for index in range(len(widths))
        ]
        height = max(len(cell_lines) for cell_lines in wrapped_cells)
        output_lines: list[str] = []
        for line_index in range(height):
            parts = []
            for column_index, cell_lines in enumerate(wrapped_cells):
                value = cell_lines[line_index] if line_index < len(cell_lines) else ""
                parts.append(_pad_to_width(value, widths[column_index]))
            output_lines.extend(
                _hard_wrap_output_line(separator.join(parts).rstrip(), max_table_width)
            )
        return output_lines

    header_lines = format_physical_row(normalized_headers)
    underline = _hard_wrap_output_line(
        separator.join("-" * width for width in widths).rstrip(), max_table_width
    )
    body = [line for row in normalized_rows for line in format_physical_row(row)]
    return "\n".join([*header_lines, *underline, *body])


def _column_widths(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    max_widths: CellWidths | None,
    min_widths: CellWidths | None,
    max_table_width: int | None,
) -> list[int]:
    widths: list[int] = []
    preferred_minimums: list[int] = []
    hard_minimums: list[int] = []
    for index, header in enumerate(headers):
        configured_width = _configured_width(max_widths, header, index)
        configured_minimum = _configured_width(min_widths, header, index)
        header_width = _display_width(header)
        hard_minimums.append(max(header_width, 1))
        min_width = max(header_width, configured_minimum or 1)
        preferred_minimums.append(min_width)

        max_content_width = header_width
        for row in rows:
            value = row[index] if index < len(row) else ""
            max_content_width = max(max_content_width, _display_width(value))
        if configured_width is None:
            widths.append(max(max_content_width, min_width))
        else:
            widths.append(max(min_width, min(max_content_width, configured_width)))
    return _fit_table_width(
        widths, preferred_minimums, hard_minimums, max_table_width
    )


def _hard_wrap_output_line(line: str, max_table_width: int | None) -> list[str]:
    if (
        max_table_width is None
        or max_table_width < 1
        or _display_width(line) <= max_table_width
    ):
        return [line]
    return _wrap_display_width(line, max_table_width) or [line]


def _column_separator(widths: Sequence[int], max_table_width: int | None) -> str:
    if max_table_width is None or len(widths) < 2:
        return "  "
    if sum(widths) + 2 * (len(widths) - 1) <= max_table_width:
        return "  "
    if sum(widths) + len(widths) - 1 <= max_table_width:
        return " "
    return ""


def _fit_table_width(
    widths: list[int],
    preferred_minimums: Sequence[int],
    hard_minimums: Sequence[int],
    max_table_width: int | None,
) -> list[int]:
    if max_table_width is None or max_table_width < 1 or not widths:
        return widths

    for minimums in (preferred_minimums, hard_minimums, [1] * len(widths)):
        for separator_cell_width in (2, 1, 0):
            fitted = list(widths)
            separator_width = separator_cell_width * (len(fitted) - 1)
            _shrink_widths_to_fit(
                fitted,
                max_table_width=max_table_width,
                separator_width=separator_width,
                minimums=minimums,
            )
            if sum(fitted) + separator_width <= max_table_width:
                return fitted

    return [1] * len(widths)


def _shrink_widths_to_fit(
    widths: list[int], *, max_table_width: int, separator_width: int, minimums: Sequence[int]
) -> None:
    while sum(widths) + separator_width > max_table_width:
        candidates = [
            index for index, width in enumerate(widths) if width > minimums[index]
        ]
        if not candidates:
            break
        widest_index = max(candidates, key=lambda index: widths[index])
        widths[widest_index] -= 1


def _configured_width(
    max_widths: CellWidths | None, header: str, index: int
) -> int | None:
    if max_widths is None:
        return None
    by_index = max_widths.get(index)
    if by_index is not None:
        return max(1, by_index)
    by_header = max_widths.get(header)
    if by_header is not None:
        return max(1, by_header)
    return None


def _sanitize_cell(value: str) -> str:
    return escape_terminal_controls(value)


def _wrap_cell(value: str, width: int) -> list[str]:
    if not value:
        return [""]
    if _is_unspaced_value(value):
        return _wrap_unspaced_value(value, width)
    wrapped = textwrap.wrap(
        value,
        width=width,
        break_long_words=True,
        break_on_hyphens=False,
    ) or [value]
    return [
        line
        for wrapped_line in wrapped
        for line in (_wrap_display_width(wrapped_line, width) or [wrapped_line])
    ]


def _is_unspaced_value(value: str) -> bool:
    return not any(char.isspace() for char in value)


def _wrap_unspaced_value(value: str, width: int) -> list[str]:
    lines: list[str] = []
    remaining = value
    while remaining and _display_width(remaining) > width:
        cut = _preferred_unspaced_cut(remaining, width)
        if cut < 1:
            hard_wrapped = _wrap_display_width(remaining, width)
            return [*lines, *(hard_wrapped or [remaining])]
        lines.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        lines.append(remaining)
    return lines or [""]


def _preferred_unspaced_cut(value: str, width: int) -> int:
    current_width = 0
    hard_cut = 0
    preferred_cut = 0
    for index, char in enumerate(value):
        char_width = _character_width(char)
        if current_width + char_width > width:
            break
        current_width += char_width
        hard_cut = index + 1
        if char in {"/", ":"}:
            preferred_cut = index + 1
    if preferred_cut:
        return preferred_cut
    return hard_cut


def _wrap_display_width(value: str, width: int) -> list[str]:
    lines: list[str] = []
    current: list[str] = []
    current_width = 0
    for char in value:
        char_width = _character_width(char)
        if char_width == 0:
            current.append(char)
            continue
        if char_width > width:
            if current:
                lines.append("".join(current))
                current = []
                current_width = 0
            lines.append("?" * width)
            continue
        if current and current_width + char_width > width:
            lines.append("".join(current))
            current = []
            current_width = 0
        current.append(char)
        current_width += char_width
    if current:
        lines.append("".join(current))
    return lines


def _pad_to_width(value: str, width: int) -> str:
    return value + " " * max(width - _display_width(value), 0)


def _display_width(value: str) -> int:
    return sum(_character_width(char) for char in value)


def _character_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    if unicodedata.east_asian_width(char) in {"F", "W"}:
        return 2
    return 1

from __future__ import annotations

from collections.abc import Mapping, Sequence
import textwrap
import unicodedata


CellWidths = Mapping[str | int, int]


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
    escaped: list[str] = []
    for char in str(value):
        codepoint = ord(char)
        if codepoint < 0x20 or 0x7F <= codepoint < 0xA0:
            escaped.append(f"\\x{codepoint:02x}")
        else:
            escaped.append(char)
    return "".join(escaped)


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

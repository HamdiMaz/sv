from __future__ import annotations

from collections.abc import Mapping, Sequence
import textwrap


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
                parts.append(value.ljust(widths[column_index]))
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
        hard_minimums.append(max(len(header), 1))
        min_width = max(len(header), configured_minimum or 1)
        preferred_minimums.append(min_width)

        max_content_width = len(header)
        for row in rows:
            value = row[index] if index < len(row) else ""
            max_content_width = max(max_content_width, len(value))
        if configured_width is None:
            widths.append(max(max_content_width, min_width))
        else:
            widths.append(max(min_width, min(max_content_width, configured_width)))
    return _fit_table_width(
        widths, preferred_minimums, hard_minimums, max_table_width
    )


def _hard_wrap_output_line(line: str, max_table_width: int | None) -> list[str]:
    if max_table_width is None or max_table_width < 1 or len(line) <= max_table_width:
        return [line]
    return textwrap.wrap(
        line,
        width=max_table_width,
        break_long_words=True,
        break_on_hyphens=False,
        drop_whitespace=False,
    ) or [line]


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
    return textwrap.wrap(
        value,
        width=width,
        break_long_words=True,
        break_on_hyphens=False,
    ) or [value]

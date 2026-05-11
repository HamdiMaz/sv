from __future__ import annotations

from collections.abc import Sequence


def format_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    all_rows = [tuple(headers), *(tuple(row) for row in rows)]
    widths = [max(len(row[index]) for row in all_rows) for index in range(len(headers))]

    def format_row(row: Sequence[str]) -> str:
        return "  ".join(
            value.ljust(widths[index]) for index, value in enumerate(row)
        ).rstrip()

    header = format_row(headers)
    underline = "  ".join("-" * width for width in widths).rstrip()
    body = [format_row(row) for row in rows]
    return "\n".join([header, underline, *body])

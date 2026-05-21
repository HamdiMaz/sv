from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import os
import shutil
from typing import TextIO
import unicodedata

from sv.terminal import escape_terminal_controls


@dataclass(frozen=True)
class SearchPromptResult:
    applied: bool
    query: str
    rendered_line_count: int = 1


@dataclass(frozen=True)
class SearchPromptConfig:
    title: str = "Search rows"
    placeholder: str = "type to search…"
    count_label: str = ""
    help_text: str = "Enter apply • empty Enter clear • Esc cancel"


_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_FG_ACCENT = "\x1b[38;5;81m"
_FG_MUTED = "\x1b[38;5;245m"
_FG_RULE = "\x1b[38;5;60m"


def ranked_search_indices(query: str, fields_by_item: Sequence[Sequence[object]]) -> list[int]:
    terms = [term for term in str(query).casefold().split() if term]
    if not terms:
        return list(range(len(fields_by_item)))

    matches: list[tuple[int, int]] = []
    for item_index, fields in enumerate(fields_by_item):
        total_score = 0
        for term in terms:
            term_score = _best_term_score(term, fields)
            if term_score is None:
                break
            total_score += term_score
        else:
            matches.append((total_score, item_index))
    return [index for _, index in sorted(matches)]


def _best_term_score(term: str, fields: Sequence[object]) -> int | None:
    best: int | None = None
    for field_index, field in enumerate(fields):
        value = str(field).casefold()
        base_score = _score_term(term, value)
        if base_score is None:
            continue
        score = base_score * 1000 + field_index
        if best is None or score < best:
            best = score
    return best


def _score_term(term: str, value: str) -> int | None:
    if term == value:
        return 0
    if value.startswith(term):
        return 2
    position = value.find(term)
    if position >= 0:
        return 6 + min(position, 20)
    if len(term) >= 2:
        return _fuzzy_score(term, value)
    return None


def _fuzzy_score(term: str, value: str) -> int | None:
    positions: list[int] = []
    search_from = 0
    for char in term:
        position = value.find(char, search_from)
        if position < 0:
            return None
        positions.append(position)
        search_from = position + 1
    gaps = sum(
        max(next_position - current_position - 1, 0)
        for current_position, next_position in zip(positions, positions[1:], strict=False)
    )
    return 100 + positions[0] + gaps


def discard_last_utf8_character(query: bytearray) -> None:
    """Remove the last complete UTF-8 character, or one byte if invalid."""
    if not query:
        return
    try:
        decoded = query.decode("utf-8")
    except UnicodeDecodeError:
        query.pop()
        return
    query[:] = decoded[:-1].encode("utf-8")


def render_search_prompt(
    query: str,
    config: SearchPromptConfig | None = None,
    *,
    terminal_width: int | None = None,
) -> str:
    config = SearchPromptConfig() if config is None else config
    width = max(terminal_width or shutil.get_terminal_size(fallback=(120, 24)).columns, 1)
    inner_width = max(width - 4, 1)
    safe_query = escape_terminal_controls(query)
    safe_title = escape_terminal_controls(config.title)
    safe_placeholder = escape_terminal_controls(config.placeholder)
    safe_count_label = escape_terminal_controls(config.count_label)
    safe_help_text = escape_terminal_controls(config.help_text)
    title = _fit_text(
        safe_title, max(inner_width - _display_width(safe_count_label) - 1, 1)
    )
    count = _fit_text(safe_count_label, inner_width) if safe_count_label else ""
    field_value = safe_query if safe_query else safe_placeholder
    field = _fit_text(f"⌕ {field_value}", inner_width)
    help_text = _fit_text(safe_help_text, inner_width)
    title_line = _join_title_and_count(title, count, inner_width)
    horizontal = "─" * inner_width
    return "\n".join(
        [
            f"╭{horizontal}╮",
            _framed_line(f"{_BOLD}{title_line}{_RESET}", title_line, inner_width),
            _framed_line(f"{_FG_ACCENT}{field}{_RESET}", field, inner_width),
            _framed_line(f"{_FG_MUTED}{help_text}{_RESET}", help_text, inner_width),
            f"╰{horizontal}╯",
        ]
    )


def _framed_line(styled_text: str, visible_text: str, width: int) -> str:
    padding = " " * max(width - _display_width(visible_text), 0)
    return f"│{styled_text}{padding}│"


def _join_title_and_count(title: str, count: str, width: int) -> str:
    if not count:
        return _fit_text(title, width)
    gap = max(width - _display_width(title) - _display_width(count), 1)
    return _fit_text(f"{title}{' ' * gap}{count}", width)


def _fit_text(value: str, max_width: int) -> str:
    if max_width < 1:
        return ""
    if _display_width(value) <= max_width:
        return value
    ellipsis = "..."[:max_width]
    if max_width <= len(ellipsis):
        return ellipsis
    available = max_width - len(ellipsis)
    current = 0
    chars: list[str] = []
    for char in value:
        char_width = _character_width(char)
        if char_width == 0:
            chars.append(char)
            continue
        if current + char_width > available:
            break
        chars.append(char)
        current += char_width
    return "".join(chars).rstrip() + ellipsis


def _display_width(value: str) -> int:
    return sum(_character_width(char) for char in value)


def _character_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    if unicodedata.east_asian_width(char) in {"F", "W"}:
        return 2
    return 1


def read_search_prompt(
    fd: int,
    stdout: TextIO,
    render_prompt: Callable[[str], str],
    previous_line_count: int = 0,
) -> SearchPromptResult:
    query = bytearray()
    captured_input = False
    rendered_line_count = _render_prompt(stdout, render_prompt, query, previous_line_count)

    while True:
        char = os.read(fd, 1)
        if char in {b"\r", b"\n"}:
            return SearchPromptResult(
                applied=True,
                query=query.decode(errors="replace"),
                rendered_line_count=rendered_line_count,
            )
        if char == b"":
            return SearchPromptResult(
                applied=True,
                query=query.decode(errors="replace") if captured_input else "",
                rendered_line_count=rendered_line_count,
            )
        if char == b"\x1b":
            return SearchPromptResult(
                applied=False,
                query="",
                rendered_line_count=rendered_line_count,
            )
        captured_input = True
        if char in {b"\x7f", b"\b"}:
            discard_last_utf8_character(query)
        else:
            query.extend(char)
        rendered_line_count = _render_prompt(
            stdout, render_prompt, query, rendered_line_count
        )


def _render_prompt(
    stdout: TextIO,
    render_prompt: Callable[[str], str],
    query: bytearray,
    previous_line_count: int,
) -> int:
    if previous_line_count:
        stdout.write(f"\x1b[{previous_line_count}F")
        stdout.write("\x1b[J")
    safe_query = escape_terminal_controls(query.decode(errors="replace"))
    rendered = render_prompt(safe_query)
    stdout.write(rendered)
    stdout.write("\n")
    stdout.flush()
    return _rendered_line_count(rendered)


def _rendered_line_count(value: str) -> int:
    return max(value.count("\n") + 1, 1)

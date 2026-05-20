from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import os
from typing import TextIO

from sv.terminal import escape_terminal_controls


@dataclass(frozen=True)
class SearchPromptResult:
    applied: bool
    query: str


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
        return 6
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


def read_search_prompt(
    fd: int,
    stdout: TextIO,
    render_prompt: Callable[[str], str],
    previous_line_count: int = 0,
) -> SearchPromptResult:
    query = bytearray()
    captured_input = False
    _render_prompt(stdout, render_prompt, query, previous_line_count)

    while True:
        char = os.read(fd, 1)
        if char in {b"\r", b"\n"}:
            return SearchPromptResult(applied=True, query=query.decode(errors="replace"))
        if char == b"":
            return SearchPromptResult(
                applied=True,
                query=query.decode(errors="replace") if captured_input else "",
            )
        if char == b"\x1b":
            return SearchPromptResult(applied=False, query="")
        captured_input = True
        if char in {b"\x7f", b"\b"}:
            if query:
                query.pop()
        else:
            query.extend(char)
        _render_prompt(stdout, render_prompt, query, 1)


def _render_prompt(
    stdout: TextIO,
    render_prompt: Callable[[str], str],
    query: bytearray,
    previous_line_count: int,
) -> None:
    if previous_line_count:
        stdout.write(f"\x1b[{previous_line_count}F")
        stdout.write("\x1b[J")
    safe_query = escape_terminal_controls(query.decode(errors="replace"))
    stdout.write(render_prompt(safe_query))
    stdout.write("\n")
    stdout.flush()

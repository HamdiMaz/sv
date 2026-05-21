from io import StringIO
import re
import unicodedata

from sv.search import (
    SearchPromptConfig,
    SearchPromptResult,
    discard_last_utf8_character,
    ranked_search_indices,
    read_search_prompt,
    render_search_prompt,
)

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


def test_ranked_search_indices_orders_exact_prefix_substring_and_fuzzy_matches():
    rows = [
        ("alpha docs",),
        ("docs",),
        ("docs-helper",),
        ("find docs",),
        ("detailed overview",),
    ]

    assert ranked_search_indices("docs", rows) == [1, 2, 3, 0]
    assert ranked_search_indices("do", rows)[:2] == [1, 2]
    assert ranked_search_indices("dh", rows) == [2]


def test_ranked_search_indices_prefers_earlier_substring_positions():
    rows = [
        ("abcdefghijklmnopqrstuvw docs",),
        ("xxdocs",),
    ]

    assert ranked_search_indices("docs", rows) == [1, 0]


def test_ranked_search_indices_empty_query_one_character_miss_and_field_replacement():
    rows = [
        ("zzz", "needle"),
        ("alpha", "zzz"),
        ("zzz", "alphabet"),
    ]

    assert ranked_search_indices("", rows) == [0, 1, 2]
    assert ranked_search_indices("x", rows) == []
    assert ranked_search_indices("alpha", rows) == [1, 2]
    assert ranked_search_indices("needle", rows) == [0]


def test_discard_last_utf8_character_invalid_bytes_pop_one_byte():
    query = bytearray(b"a\xff")

    discard_last_utf8_character(query)

    assert query == bytearray(b"a")


def test_render_search_prompt_frames_title_query_hint_and_count():
    prompt = render_search_prompt(
        "docs",
        SearchPromptConfig(title="Search skills", count_label="3 items"),
        terminal_width=60,
    )

    visible_lines = [visible_text(line) for line in prompt.splitlines()]

    assert len(visible_lines) == 5
    assert visible_lines[0].startswith("╭")
    assert visible_lines[1].startswith("│Search skills")
    assert visible_lines[1].endswith("3 items│")
    assert visible_lines[2].startswith("│⌕ docs")
    assert visible_lines[2].endswith("│")
    assert visible_lines[3].startswith("│Enter apply • empty Enter clear • Esc cancel")
    assert visible_lines[3].endswith("│")
    assert visible_lines[4].startswith("╰")
    assert visible_lines[4].endswith("╯")
    assert "Enter apply" not in visible_lines[4]


def test_render_search_prompt_keeps_frame_aligned_in_tiny_terminals():
    for terminal_width in range(1, 8):
        prompt = render_search_prompt(
            "very-long-query-value",
            SearchPromptConfig(title="Search skills", count_label="123 items"),
            terminal_width=terminal_width,
        )

        visible_lines = [visible_text(line) for line in prompt.splitlines()]
        line_widths = [display_width(line) for line in visible_lines]

        assert len(visible_lines) == 5
        assert len(set(line_widths)) == 1


def test_render_search_prompt_uses_placeholder_and_escapes_controls():
    prompt = render_search_prompt(
        "\x01[2J",
        SearchPromptConfig(title="Search rows", placeholder="type to search…"),
        terminal_width=50,
    )

    assert "⌕ \\x01[2J" in prompt
    assert "\x01[2J" not in prompt

    empty_prompt = render_search_prompt(
        "",
        SearchPromptConfig(title="Search rows", placeholder="type to search…"),
        terminal_width=50,
    )
    assert "type to search…" in empty_prompt


def test_render_search_prompt_escapes_placeholder_controls():
    prompt = render_search_prompt(
        "",
        SearchPromptConfig(placeholder="type\x01[2Jhere"),
        terminal_width=50,
    )

    assert "⌕ type\\x01[2Jhere" in prompt
    assert "type\x01[2Jhere" not in prompt


def test_render_search_prompt_fits_narrow_terminal_width():
    prompt = render_search_prompt(
        "very-long-query-value",
        SearchPromptConfig(title="Search skills", count_label="123 items"),
        terminal_width=24,
    )

    lines = prompt.splitlines()
    assert len(lines) == 5
    assert all(len(line) <= 24 + len("\x1b[0m") * 4 for line in lines)
    assert "..." in prompt


def test_read_search_prompt_renders_escaped_query_and_applies_on_enter(monkeypatch):
    values = iter([b"d", b"\x01", b"[", b"2", b"J", b"\n"])
    monkeypatch.setattr("os.read", lambda _fd, _count: next(values))
    stdout = StringIO()

    result = read_search_prompt(0, stdout, lambda query: f"Search: {query}")

    assert result == SearchPromptResult(
        applied=True,
        query="d\x01[2J",
        rendered_line_count=1,
    )
    assert "Search: d\\x01[2J" in stdout.getvalue()
    assert "Search: d\x01[2J" not in stdout.getvalue()


def test_read_search_prompt_clears_multiline_prompt_on_each_redraw(monkeypatch):
    values = iter([b"d", b"o", b"\n"])
    monkeypatch.setattr("os.read", lambda _fd, _count: next(values))
    stdout = StringIO()

    result = read_search_prompt(
        0,
        stdout,
        lambda query: render_search_prompt(
            query,
            SearchPromptConfig(title="Search skills", count_label="3 items"),
            terminal_width=60,
        ),
        previous_line_count=5,
    )

    assert result == SearchPromptResult(
        applied=True,
        query="do",
        rendered_line_count=5,
    )
    rendered = stdout.getvalue()
    assert rendered.startswith("\x1b[5F\x1b[J")
    assert rendered.count("\x1b[5F\x1b[J") == 3
    assert "Search skills" in rendered
    assert "⌕ do" in rendered


def test_read_search_prompt_counts_trailing_blank_line_for_redraw(monkeypatch):
    values = iter([b"d", b"\n"])
    monkeypatch.setattr("os.read", lambda _fd, _count: next(values))
    stdout = StringIO()

    result = read_search_prompt(0, stdout, lambda query: f"Search: {query}\n")

    assert result == SearchPromptResult(
        applied=True,
        query="d",
        rendered_line_count=2,
    )
    rendered = stdout.getvalue()
    assert "\x1b[2F\x1b[J" in rendered
    assert "\x1b[1F\x1b[J" not in rendered


def test_read_search_prompt_escape_cancels_without_query(monkeypatch):
    values = iter([b"d", b"\x1b"])
    monkeypatch.setattr("os.read", lambda _fd, _count: next(values))

    assert read_search_prompt(0, StringIO(), lambda query: query) == SearchPromptResult(
        applied=False,
        query="",
    )


def test_read_search_prompt_backspace_removes_complete_multibyte_character(monkeypatch):
    values = iter([b"\xc3", b"\xa9", b"\x7f", b"\n"])
    monkeypatch.setattr("os.read", lambda _fd, _count: next(values))

    assert read_search_prompt(0, StringIO(), lambda query: query) == SearchPromptResult(
        applied=True,
        query="",
    )


def test_read_search_prompt_eof_applies_captured_or_empty_query(monkeypatch):
    values = iter([b"d", b""])
    monkeypatch.setattr("os.read", lambda _fd, _count: next(values))
    assert read_search_prompt(0, StringIO(), lambda query: query) == SearchPromptResult(
        applied=True,
        query="d",
    )

    empty_values = iter([b""])
    monkeypatch.setattr("os.read", lambda _fd, _count: next(empty_values))
    assert read_search_prompt(0, StringIO(), lambda query: query) == SearchPromptResult(
        applied=True,
        query="",
    )

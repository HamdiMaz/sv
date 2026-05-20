from io import StringIO

from sv.search import SearchPromptResult, ranked_search_indices, read_search_prompt


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


def test_read_search_prompt_renders_escaped_query_and_applies_on_enter(monkeypatch):
    values = iter([b"d", b"\x01", b"[", b"2", b"J", b"\n"])
    monkeypatch.setattr("os.read", lambda _fd, _count: next(values))
    stdout = StringIO()

    result = read_search_prompt(0, stdout, lambda query: f"Search: {query}")

    assert result == SearchPromptResult(applied=True, query="d\x01[2J")
    assert "Search: d\\x01[2J" in stdout.getvalue()
    assert "Search: d\x01[2J" not in stdout.getvalue()


def test_read_search_prompt_escape_cancels_without_query(monkeypatch):
    values = iter([b"d", b"\x1b"])
    monkeypatch.setattr("os.read", lambda _fd, _count: next(values))

    assert read_search_prompt(0, StringIO(), lambda query: query) == SearchPromptResult(
        applied=False,
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

# Search Prompt Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the raw one-line interactive `Search:` prompt with a shared framed inline prompt across TTY table browsers and multi-select pickers.

**Architecture:** Keep the existing applied-on-Enter search model and ranked search semantics. Add shared prompt presentation and multiline redraw accounting in `src/sv/search.py`, then have `src/sv/table.py` and `src/sv/selector.py` pass context-specific prompt titles while preserving their local state, footer, and no-match rendering responsibilities.

**Tech Stack:** Python 3.14 stdlib, existing ANSI inline TTY rendering, pytest/ruff/ty via `uv`; no runtime dependencies.

---

## Scout Summary

**Architecture:** `src/sv/search.py` owns ranked search and raw byte prompt input. `src/sv/table.py` owns read-only TTY table browsing, row filtering, footer rendering, and detail-mode transitions. `src/sv/selector.py` owns multi-select TTY pickers, item filtering, selection persistence, and structured column rendering. CLI flows in `src/sv/cli.py` call table/selector helpers through `_browse_tty_table()` and `_call_selector()`.

**Likely files:**
- `src/sv/search.py`: add shared framed prompt config/rendering and multiline redraw accounting.
- `src/sv/table.py`: use shared framed prompt, track returned prompt line count, improve table footer/no-match copy.
- `src/sv/selector.py`: use shared framed prompt, track returned prompt line count, improve selector footer/no-match copy.
- `src/sv/cli.py`: pass contextual prompt titles for source-skill, repo, add, remove, and repo-remove interactive surfaces.
- Docs: `README.md`, `docs/output.md`, `docs/usage.md`, `docs/getting-started.md`, `docs/examples.md`, `docs/commands.md`, and `CHANGELOG.md`.
- Tests: `tests/test_search.py`, `tests/test_table.py`, `tests/test_selector.py`, `tests/test_cli_tty_browsing_contracts.py`, `tests/test_cli_add_contracts.py`, `tests/test_cli_remove_contracts.py`, `tests/test_cli_source_commands.py`, `tests/test_docs.py`, and `tests/test_second_coverage_batch.py`.

**Existing patterns:** Search state is stored as sanitized `search_query`, `filter_query` remains an alias for legacy tests/keys, and filtering uses original item/row indices. TTY tests strip ANSI using `visible_text()`. Focused test loops use commands like `uv run pytest tests/test_search.py -q --no-cov` because project-wide coverage is enforced for full test runs.

**Risks:** The main risk is terminal line accounting. `read_search_prompt()` currently assumes a one-line prompt and table/selector loops set `rendered_lines = 1` after search. A framed prompt must clear the actual prompt line count while typing and before redrawing the browser. A second risk is exact string assertions in existing footer/search tests.

**Tests:** Build from the core outward: first shared prompt rendering and multiline redraw in `tests/test_search.py`, then table integration, selector integration, CLI contextual titles, docs, and release-gate checks.

**Unknowns:** None blocking. The approved UX keeps search applied-on-Enter, Esc cancel, and empty Enter clear.

## Recommended Design

**Recommendation:** Add a small shared prompt UI layer to `src/sv/search.py` and expose the prompt line count through `SearchPromptResult`. This keeps input behavior centralized and avoids duplicating the framed prompt in table and selector modules.

**Components:**
- `SearchPromptConfig`: immutable prompt title, placeholder, count label, and help text.
- `render_search_prompt()`: produces a framed ANSI-safe multiline prompt string that fits terminal width.
- `read_search_prompt()`: continues reading raw bytes and returning `SearchPromptResult`, now with `rendered_line_count`.
- Table/selector `_read_search_query()`: return `SearchPromptResult`, not only the query string, so loops can clear the framed prompt correctly.
- Table/selector renderers: keep local footer/no-match logic but align wording.

**Flow:** User presses `/`; the current browser render is cleared using existing `rendered_lines`; `read_search_prompt()` renders the framed prompt and tracks prompt line count as the user types; Enter returns an applied query, empty Enter returns an applied empty query, and Esc returns `applied=False`; table/selector redraw using the returned prompt line count and existing `set_search()` behavior.

**Trade-offs:** This preserves the current lightweight stdlib TTY approach and avoids live filtering. It adds a small amount of duplicated context wiring in CLI calls so prompt titles can be specific without making the low-level search helper know CLI concepts.

**Risks & mitigations:** Count rendered physical lines in `src/sv/search.py` and assert escape sequences such as `\x1b[4F\x1b[J` in tests. Keep `SearchPromptResult.rendered_line_count` defaulted to `1` so simple one-line renderers remain compatible. Preserve `_call_selector()` keyword compatibility by only adding optional keyword arguments to `select_skills()`.

**Testing strategy:** Use TDD per task. Run focused `--no-cov` tests after each task and run the documented release gate at the end.

**Rollout/migration:** User-facing behavior changes only in TTY interactive prompts. Non-TTY output and ranking semantics remain unchanged.

## Behavior Locked by This Plan

- `/` opens a framed inline prompt.
- Search results update only after Enter.
- Enter applies the typed query.
- Empty Enter clears the active search and removes the footer search indicator.
- Esc cancels prompt input and preserves the previous search state.
- Prompt help reads: `Enter apply • empty Enter clear • Esc cancel`.
- Control characters in typed queries render escaped, never as raw terminal controls.
- Long prompt content truncates to terminal width.
- `ranked_search_indices()` behavior does not change.

## File Structure

- Modify `src/sv/search.py`: prompt config, prompt renderer, line-count-aware prompt redraw, result line count.
- Modify `tests/test_search.py`: shared renderer and redraw coverage.
- Modify `src/sv/table.py`: table prompt integration, line-count handling, footer/no-match updates, optional prompt title.
- Modify `tests/test_table.py`: table prompt, empty clear, cancel preservation, footer/no-match updates.
- Modify `src/sv/selector.py`: selector prompt integration, line-count handling, footer/no-match updates, optional prompt title.
- Modify `tests/test_selector.py` and `tests/test_second_coverage_batch.py`: selector prompt/footer/no-match updates.
- Modify `src/sv/cli.py`: contextual prompt titles passed into browse/select calls.
- Modify CLI contract tests for contextual prompt title kwargs.
- Modify docs and changelog to describe framed search and empty Enter clear behavior.

## Constraints

- Keep all work in `/home/maz/Projects/sv/.worktrees/improve-search-prompt`.
- Do not add runtime dependencies.
- Do not change non-TTY output.
- Do not change search ranking semantics.
- Keep legacy `filter` and `filter:` synthetic key paths working.
- Preserve terminal-control escaping.
- Preserve source-skill rankers passed by CLI flows.
- Use focused test commands with `--no-cov`; run full release verification before claiming implementation readiness.

---

### Task 1: Shared framed prompt renderer and multiline redraw

**Files:**
- Modify: `src/sv/search.py`
- Modify: `tests/test_search.py`

- [ ] **Step 1: Write failing tests for the shared prompt renderer**

In `tests/test_search.py`, extend the import list:

```python
from sv.search import (
    SearchPromptConfig,
    SearchPromptResult,
    discard_last_utf8_character,
    ranked_search_indices,
    read_search_prompt,
    render_search_prompt,
)
```

Add these tests after `test_discard_last_utf8_character_invalid_bytes_pop_one_byte`:

```python
def test_render_search_prompt_frames_title_query_hint_and_count():
    prompt = render_search_prompt(
        "docs",
        SearchPromptConfig(title="Search skills", count_label="3 items"),
        terminal_width=60,
    )

    assert prompt.count("\n") == 3
    assert "Search skills" in prompt
    assert "3 items" in prompt
    assert "⌕ docs" in prompt
    assert "Enter apply • empty Enter clear • Esc cancel" in prompt
    assert "╭" in prompt
    assert "╰" in prompt


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


def test_render_search_prompt_fits_narrow_terminal_width():
    prompt = render_search_prompt(
        "very-long-query-value",
        SearchPromptConfig(title="Search skills", count_label="123 items"),
        terminal_width=24,
    )

    lines = prompt.splitlines()
    assert len(lines) == 4
    assert all(len(line) <= 24 + len("\x1b[0m") * 4 for line in lines)
    assert "..." in prompt
```

- [ ] **Step 2: Write failing tests for multiline redraw accounting**

Update `test_read_search_prompt_renders_escaped_query_and_applies_on_enter` to expect the default one-line renderer still reports one line:

```python
assert result == SearchPromptResult(
    applied=True,
    query="d\x01[2J",
    rendered_line_count=1,
)
```

Add this test below it:

```python
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
        rendered_line_count=4,
    )
    rendered = stdout.getvalue()
    assert rendered.startswith("\x1b[5F\x1b[J")
    assert rendered.count("\x1b[4F\x1b[J") == 2
    assert "Search skills" in rendered
    assert "⌕ do" in rendered
```

- [ ] **Step 3: Run the focused tests and verify they fail for missing prompt APIs**

Run:

```bash
uv run pytest tests/test_search.py -q --no-cov
```

Expected: failures mention missing `SearchPromptConfig`, missing `render_search_prompt`, missing `rendered_line_count`, or multiline redraw assumptions.

- [ ] **Step 4: Implement `SearchPromptResult.rendered_line_count` and prompt config**

In `src/sv/search.py`, update imports:

```python
from dataclasses import dataclass
import os
import shutil
import unicodedata
```

Replace `SearchPromptResult` with:

```python
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
```

Add prompt styling constants below the dataclasses:

```python
_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_FG_ACCENT = "\x1b[38;5;81m"
_FG_MUTED = "\x1b[38;5;245m"
_FG_RULE = "\x1b[38;5;60m"
```

- [ ] **Step 5: Implement `render_search_prompt()` and fitting helpers**

Add these helpers in `src/sv/search.py` before `read_search_prompt()`:

```python
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
    title = _fit_text(config.title, max(inner_width - _display_width(config.count_label) - 1, 1))
    count = _fit_text(config.count_label, inner_width) if config.count_label else ""
    field_value = safe_query if safe_query else config.placeholder
    field_prefix = "⌕ "
    field = field_prefix + _fit_text(field_value, max(inner_width - _display_width(field_prefix), 1))
    help_text = _fit_text(config.help_text, inner_width)
    title_line = _join_title_and_count(title, count, inner_width)
    horizontal = "─" * inner_width
    return "\n".join(
        [
            f"{_FG_RULE}╭{horizontal}╮{_RESET}",
            _framed_line(f"{_BOLD}{title_line}{_RESET}", title_line, inner_width),
            _framed_line(f"{_FG_ACCENT}{field}{_RESET}", field, inner_width),
            _framed_line(f"{_FG_MUTED}{help_text}{_RESET}", help_text, inner_width, bottom=True),
        ]
    )


def _framed_line(styled_text: str, visible_text: str, width: int, *, bottom: bool = False) -> str:
    padding = " " * max(width - _display_width(visible_text), 0)
    if bottom:
        return f"{_FG_RULE}╰{styled_text}{padding}╯{_RESET}"
    return f"{_FG_RULE}│{_RESET}{styled_text}{padding}{_FG_RULE}│{_RESET}"


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
```

- [ ] **Step 6: Make prompt redraw line-count-aware**

Update `read_search_prompt()` so it tracks the line count returned by `_render_prompt()`:

```python
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
        rendered_line_count = _render_prompt(stdout, render_prompt, query, rendered_line_count)
```

Update `_render_prompt()` to return the number of prompt lines it wrote:

```python
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
    return max(len(value.splitlines()), 1)
```

- [ ] **Step 7: Verify shared prompt tests pass**

Run:

```bash
uv run pytest tests/test_search.py -q --no-cov
```

Expected: all tests in `tests/test_search.py` pass.

- [ ] **Step 8: Commit**

```bash
git add src/sv/search.py tests/test_search.py
git commit -m "feat: add framed search prompt renderer"
```

---

### Task 2: Table browser integration, footer, and no-match state

**Files:**
- Modify: `src/sv/table.py`
- Modify: `tests/test_table.py`

- [ ] **Step 1: Write failing table prompt tests**

In `tests/test_table.py`, extend imports:

```python
from sv.search import SearchPromptResult
```

Replace `test_read_search_query_renders_visible_prompt_and_escapes_controls` or add this table-specific test near the current `_read_search_query` tests:

```python
def test_table_read_search_query_renders_framed_prompt_and_escapes_controls():
    stdout = StringIO()
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"docs\x01\n")
        os.close(write_fd)
        write_fd = -1

        result = _read_search_query(
            read_fd,
            stdout,
            search_title="Search rows",
            count_label="3 rows",
        )
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)

    assert result == SearchPromptResult(
        applied=True,
        query="docs\x01",
        rendered_line_count=4,
    )
    rendered = stdout.getvalue()
    visible = visible_text(rendered)
    assert "Search rows" in visible
    assert "3 rows" in visible
    assert "⌕ docs\\x01" in visible
    assert "Enter apply • empty Enter clear • Esc cancel" in visible
    assert "docs\x01" not in rendered
```

Update `test_read_search_query_returns_none_when_escape_cancels` because `_read_search_query()` now returns a `SearchPromptResult` object:

```python
assert result.applied is False
assert result.query == ""
assert result.rendered_line_count == 4
```

- [ ] **Step 2: Write failing tests for table loop line accounting and empty clear**

Update `test_browse_table_visible_search_enter_applies_and_cancel_preserves` so the fake `_read_search_query()` returns `SearchPromptResult` and writes four prompt lines:

```python
def _fake_read_search_query(_fd, stdout, previous_line_count=0, **_kwargs):
    result = next(prompt_results)
    assert previous_line_count == 5
    stdout.write(f"\x1b[{previous_line_count}F\x1b[J")
    stdout.write("╭search╮\n│prompt│\n│hint│\n╰end╯\n")
    if result is None:
        return SearchPromptResult(applied=False, query="", rendered_line_count=4)
    return SearchPromptResult(applied=True, query=result, rendered_line_count=4)
```

Update assertions in that test to prove the subsequent table redraw clears four prompt lines:

```python
assert "╰end╯\n\x1b[4F\x1b[J" in rendered
assert "search: alpha" in visible_output
```

Add this test below the cancel test:

```python
def test_browse_table_empty_enter_clears_previous_search(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["search:ga", "search", "enter"])

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.table._read_key", lambda _fd: next(key_inputs))
    monkeypatch.setattr(
        "sv.table._read_search_query",
        lambda *_args, **_kwargs: SearchPromptResult(
            applied=True,
            query="",
            rendered_line_count=4,
        ),
    )

    selected = browse_table(
        ["Skill"],
        [["alpha"], ["beta"], ["gamma"]],
        stdin=TtyStream(),
        stdout=output,
    )

    assert selected == ["alpha"]
    assert "search: ga" not in visible_text(output.getvalue()).splitlines()[-1]
```

- [ ] **Step 3: Write failing tests for no-match copy and active search indicator**

Add or update table render tests near `_render_interactive_table` tests:

```python
def test_render_table_search_footer_styles_visible_search_text():
    state = TableState(["Skill"], [["alpha"], ["beta"], ["gamma"]])
    state.set_search("ga")
    stdout = StringIO()

    _render_interactive_table(state, stdout)

    visible = visible_text(stdout.getvalue())
    assert "Showing 1-1 of 1 matching 3" in visible
    assert "search: ga" in visible
    assert "↑/↓ move" in visible


def test_render_table_no_match_names_query_and_hint():
    state = TableState(["Skill"], [["alpha"], ["beta"], ["gamma"]])
    state.set_search("missing")
    stdout = StringIO()

    _render_interactive_table(state, stdout)

    visible = visible_text(stdout.getvalue())
    assert "No matches for \"missing\"" in visible
    assert "Try a different search term." in visible
    assert "Showing 0-0 of 0 matching 3" in visible
    assert "search: missing" in visible
```

- [ ] **Step 4: Run table tests and verify intended failures**

Run:

```bash
uv run pytest tests/test_table.py -q --no-cov
```

Expected: failures reference old string return from `_read_search_query`, one-line rendered prompt accounting, or generic `No rows to show` copy.

- [ ] **Step 5: Implement table prompt integration**

Update `src/sv/table.py` imports:

```python
from sv.search import (
    SearchPromptConfig,
    SearchPromptResult,
    discard_last_utf8_character,
    read_search_prompt,
    ranked_search_indices,
    render_search_prompt,
)
```

Add optional search prompt parameters to `browse_table()`:

```python
    search_title: str = "Search rows",
    search_placeholder: str = "type to search…",
```

Update the `/` branch in `browse_table()`:

```python
            elif key == "search":
                result = _read_search_query(
                    fd,
                    output_stream,
                    rendered_lines,
                    search_title=search_title,
                    search_placeholder=search_placeholder,
                    count_label=f"{len(state.rows)} rows",
                )
                rendered_lines = result.rendered_line_count
                if result.applied:
                    state.set_search(result.query)
```

Change `_read_search_query()` to return `SearchPromptResult`:

```python
def _read_search_query(
    fd: int,
    stdout: TextIO,
    previous_line_count: int = 0,
    *,
    search_title: str = "Search rows",
    search_placeholder: str = "type to search…",
    count_label: str = "",
) -> SearchPromptResult:
    return read_search_prompt(
        fd,
        stdout,
        lambda query: render_search_prompt(
            query,
            SearchPromptConfig(
                title=search_title,
                placeholder=search_placeholder,
                count_label=count_label,
            ),
        ),
        previous_line_count=previous_line_count,
    )
```

- [ ] **Step 6: Implement table no-match and footer updates**

In `_render_interactive_table()`, replace the current no-row append:

```python
    if not state.visible_rows():
        lines.extend(_format_no_match_lines(state, terminal_width))
```

Add this helper near `_format_table_help_line()`:

```python
def _format_no_match_lines(state: TableState, terminal_width: int) -> list[str]:
    if not state.search_query:
        return [_fit_text("No rows to show", terminal_width)]
    safe_query = _sanitize_cell(state.search_query)
    return [
        f"{_FG_MUTED}{_fit_text(f'No matches for \"{safe_query}\"', terminal_width)}{_RESET}",
        f"{_FG_MUTED}{_fit_text('Try a different search term.', terminal_width)}{_RESET}",
    ]
```

In `_format_table_help_line()`, style the active search segment while preserving visible text:

```python
    if state.search_query:
        safe_query = _sanitize_cell(state.search_query)
        text += f" matching {total_count} • search: {safe_query}"
```

Keep the existing final line styling through `_FG_MUTED` and `_fit_text()`.

- [ ] **Step 7: Verify table tests pass**

Run:

```bash
uv run pytest tests/test_table.py -q --no-cov
```

Expected: all tests in `tests/test_table.py` pass.

- [ ] **Step 8: Commit**

```bash
git add src/sv/table.py tests/test_table.py
git commit -m "feat: use framed search in table browser"
```

---

### Task 3: Selector integration, footer, and no-match state

**Files:**
- Modify: `src/sv/selector.py`
- Modify: `tests/test_selector.py`
- Modify: `tests/test_second_coverage_batch.py`

- [ ] **Step 1: Write failing selector prompt tests**

In `tests/test_selector.py`, extend imports:

```python
from sv.search import SearchPromptResult
```

Replace `test_read_search_query_renders_visible_prompt_and_escapes_controls` or add this test near it:

```python
def test_selector_read_search_query_renders_framed_prompt_and_escapes_controls():
    stdout = StringIO()
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"docs\x01\n")
        os.close(write_fd)
        write_fd = -1

        result = _read_search_query(
            read_fd,
            stdout,
            search_title="Search skills",
            count_label="3 items",
        )
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)

    assert result == SearchPromptResult(
        applied=True,
        query="docs\x01",
        rendered_line_count=4,
    )
    rendered = stdout.getvalue()
    visible = visible_text(rendered)
    assert "Search skills" in visible
    assert "3 items" in visible
    assert "⌕ docs\\x01" in visible
    assert "Enter apply • empty Enter clear • Esc cancel" in visible
    assert "docs\x01" not in rendered
```

Update `test_read_search_query_applies_eof_input_and_clears_empty_eof` because `_read_search_query()` now returns `SearchPromptResult`:

```python
assert result.query == "docs"
assert result.applied is True
assert result.rendered_line_count == 4
assert empty_result.query == ""
assert empty_result.applied is True
```

Update `test_read_search_query_returns_none_when_escape_cancels`:

```python
assert result.applied is False
assert result.query == ""
assert result.rendered_line_count == 4
```

- [ ] **Step 2: Write failing selector loop tests for line accounting and empty clear**

Update `test_select_skills_escape_cancelled_search_preserves_previous_search` so the monkeypatch returns `SearchPromptResult(applied=False, query="", rendered_line_count=4)` instead of `None`.

Add this test below it:

```python
def test_select_skills_empty_enter_clears_previous_search(monkeypatch):
    output = TtyStream()
    key_inputs = iter(["search:ga", "search", "space", "enter"])

    monkeypatch.setitem(sys.modules, "termios", FakeTermios)
    monkeypatch.setitem(sys.modules, "tty", FakeTty)
    monkeypatch.setattr("sv.selector._read_key", lambda _fd: next(key_inputs))
    monkeypatch.setattr(
        "sv.selector._read_search_query",
        lambda *_args, **_kwargs: SearchPromptResult(
            applied=True,
            query="",
            rendered_line_count=4,
        ),
    )

    selected = select_skills(
        ["alpha", "beta", "gamma"],
        stdin=TtyStream(),
        stdout=output,
    )

    assert selected == ["alpha"]
    assert "search: ga" not in visible_text(output.getvalue()).splitlines()[-1]
```

- [ ] **Step 3: Write failing selector footer/no-match tests**

Update `test_render_footer_shows_slash_search_query` expected visible text only if wording changes. Add this no-match body assertion for plain mode:

```python
def test_render_plain_selector_no_match_names_query_and_hint():
    state = SelectionState(["alpha", "beta", "gamma"])
    state.set_search("missing")
    stdout = StringIO()

    _render(state, stdout)

    visible = visible_text(stdout.getvalue())
    assert "No matches for \"missing\"" in visible
    assert "Try a different search term." in visible
    assert "Showing 0-0 of 0 matching 3" in visible
    assert "search: missing" in visible
```

Update structured no-match expectations in `tests/test_second_coverage_batch.py::test_selector_empty_and_filtered_interactive_paths` from `No rows to show` to the same query-aware no-match text when an active search exists. In the same test, change the `_read_search_query` monkeypatch from returning the string `"alpha"` to returning:

```python
SearchPromptResult(applied=True, query="alpha", rendered_line_count=4)
```

- [ ] **Step 4: Run selector tests and verify intended failures**

Run:

```bash
uv run pytest tests/test_selector.py tests/test_second_coverage_batch.py::test_selector_empty_and_filtered_interactive_paths -q --no-cov
```

Expected: failures reference old `str | None` `_read_search_query`, one-line prompt assumptions, or missing no-match body copy.

- [ ] **Step 5: Implement selector prompt integration**

Update `src/sv/selector.py` imports:

```python
from sv.search import (
    SearchPromptConfig,
    SearchPromptResult,
    discard_last_utf8_character,
    read_search_prompt,
    ranked_search_indices,
    render_search_prompt,
)
```

Add optional search prompt parameters to `select_skills()`:

```python
    search_title: str = "Search skills",
    search_placeholder: str = "type to search…",
```

Update the `/` branch in `select_skills()`:

```python
            elif key == "search":
                result = _read_search_query(
                    fd,
                    output_stream,
                    rendered_lines,
                    search_title=search_title,
                    search_placeholder=search_placeholder,
                    count_label=f"{len(state.items)} items",
                )
                rendered_lines = result.rendered_line_count
                if result.applied:
                    state.set_search(result.query)
```

Change `_read_search_query()` to return `SearchPromptResult`:

```python
def _read_search_query(
    fd: int,
    stdout: TextIO,
    previous_line_count: int = 0,
    *,
    search_title: str = "Search skills",
    search_placeholder: str = "type to search…",
    count_label: str = "",
) -> SearchPromptResult:
    return read_search_prompt(
        fd,
        stdout,
        lambda query: render_search_prompt(
            query,
            SearchPromptConfig(
                title=search_title,
                placeholder=search_placeholder,
                count_label=count_label,
            ),
        ),
        previous_line_count=previous_line_count,
    )
```

- [ ] **Step 6: Implement selector no-match body copy**

In `_render()`, before appending the footer, add query-aware no-match lines when `state.items` exists but `visible_items()` is empty:

```python
    visible_items = state.visible_items()
    if not visible_items and state.items:
        lines.extend(_format_selector_no_match_lines(state))
    for index, skill in visible_items:
        lines.append(
            _format_skill_line(
                state,
                index,
                item_label(skill),
                highlight_cursor=highlight_cursor,
            )
        )
```

In `_render_structured()`, replace `lines.append(_fit_text("No rows to show", terminal_width))` with:

```python
        lines.extend(_format_selector_no_match_lines(state, terminal_width=terminal_width))
```

Add helper near `_format_help_line()`:

```python
def _format_selector_no_match_lines(
    state: SelectionState[T], *, terminal_width: int | None = None
) -> list[str]:
    width = _terminal_width() if terminal_width is None else terminal_width
    if not state.search_query:
        return [_fit_text("No rows to show", width)]
    safe_query = _sanitize_label(state.search_query)
    return [
        f"{_FG_MUTED}{_fit_text(f'No matches for \"{safe_query}\"', width)}{_RESET}",
        f"{_FG_MUTED}{_fit_text('Try a different search term.', width)}{_RESET}",
    ]
```

Keep `_format_help_line()` visible wording compatible with table, for example: `Showing 1-1 of 1 matching 3 • search: docs • / search • q cancel`.

- [ ] **Step 7: Verify selector tests pass**

Run:

```bash
uv run pytest tests/test_selector.py tests/test_second_coverage_batch.py::test_selector_empty_and_filtered_interactive_paths -q --no-cov
```

Expected: all selected selector tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/sv/selector.py tests/test_selector.py tests/test_second_coverage_batch.py
git commit -m "feat: use framed search in interactive picker"
```

---

### Task 4: Contextual prompt titles in CLI interactive flows

**Files:**
- Modify: `src/sv/cli.py`
- Modify: `tests/test_cli_tty_browsing_contracts.py`
- Modify: `tests/test_cli_add_contracts.py`
- Modify: `tests/test_cli_remove_contracts.py`
- Modify: `tests/test_cli_source_commands.py`

- [ ] **Step 1: Write failing CLI tests for table prompt titles**

Update existing fake browser assertions in `tests/test_cli_tty_browsing_contracts.py`:

```python
assert kwargs["search_title"] == "Search skills"
```

Add that assertion to:
- `test_tty_list_browses_source_skills_with_details_by_default`
- `test_tty_search_browses_ranked_matches_with_details_by_default`
- repo skill browser assertions inside `test_tty_repo_list_browses_repo_skills_and_can_install_one_or_all`

For the first repo table in `test_tty_repo_list_browses_repo_skills_and_can_install_one_or_all`, assert:

```python
assert browse_calls[0][2]["search_title"] == "Search repositories"
assert browse_calls[1][2]["search_title"] == "Search skills"
```

- [ ] **Step 2: Write failing CLI tests for selector prompt titles**

In `tests/test_cli_add_contracts.py`, update fake selector assertions for interactive add:

```python
def fake_select_skills(skills, **kwargs):
    assert kwargs["search_title"] == "Search skills"
    return [skills[0]]
```

In `tests/test_cli_remove_contracts.py`, update the fake selector used by interactive remove:

```python
def fake_select_skills(skills, **kwargs):
    assert kwargs["search_title"] == "Search skills"
    return [skills[0]]
```

In `tests/test_cli_source_commands.py`, update the repo remove interactive selector test to assert:

```python
def fake_select_repos(repo_ids, **kwargs):
    assert kwargs["search_title"] == "Search repositories"
    return [repo_ids[0]]
```

In `tests/test_cli.py` or the existing duplicate-choice test file, update the fake picker around duplicate source selection to assert the one-source chooser receives a skills prompt title:

```python
def fake_choose_skill(matches, **kwargs):
    assert kwargs["search_title"] == "Search skills"
    return matches[0]
```

- [ ] **Step 3: Run CLI-focused tests and verify intended failures**

Run:

```bash
uv run pytest tests/test_cli_tty_browsing_contracts.py tests/test_cli_add_contracts.py tests/test_cli_remove_contracts.py tests/test_cli_source_commands.py -q --no-cov
```

Expected: failures mention missing `search_title` kwargs or unexpected keyword handling until CLI calls are updated.

- [ ] **Step 4: Pass contextual titles from CLI table flows**

In `src/sv/cli.py`, update `_handle_repo_browser()`:

```python
    _browse_tty_table(
        ["Repo", "URL", "Cache"],
        rows,
        on_detail=open_repo_skills,
        search_title="Search repositories",
    )
```

Update `_browse_repo_source_skills()`:

```python
    _browse_tty_table(
        _source_skill_headers(),
        rows,
        detail_renderer=render_detail,
        detail_actions={"a": add_detail},
        key_actions={"a": install_all},
        key_help="a add all",
        clear_on_exit=True,
        row_ranker=_source_skill_ranker(entries),
        search_title="Search skills",
    )
```

Update `_browse_source_skills()`:

```python
    _browse_tty_table(
        _source_skill_headers(),
        rows,
        row_ranker=_source_skill_ranker(entries),
        detail_renderer=render_detail,
        detail_actions={"a": add_detail},
        detail_key_help="a add skill • q back",
        search_title="Search skills",
    )
```

- [ ] **Step 5: Pass contextual titles from CLI selector flows**

Update `_handle_add_interactive()` selector call:

```python
    selected_skills = _call_selector(
        skill_selector,
        catalog,
        item_columns=_source_skill_picker_columns,
        header_columns=["Skill", "Source", "Description"],
        item_ranker=_source_skill_ranker(catalog),
        enter_action_label="add",
        search_title="Search skills",
    )
```

Update `_handle_remove_interactive()` selector call:

```python
    selected_skills = _call_selector(
        skill_selector,
        skills,
        item_label=lambda skill: _skill_removal_label(entries_by_name[skill]),
        item_columns=lambda skill: _skill_removal_columns(entries_by_name[skill]),
        header_columns=["Skill", "Target", "Source/Status"],
        enter_action_label="remove",
        search_title="Search skills",
    )
```

Update `_handle_repo_remove_interactive()` selector call:

```python
    selected_repo_ids = _call_selector(
        skill_selector,
        repo_ids,
        item_label=lambda repo_id: _repo_removal_label(repos_by_id[repo_id]),
        search_title="Search repositories",
    )
```

Update `_choose_skill()` so duplicate and ambiguous source selection uses the skills prompt title:

```python
        selected = select_skills(
            matches,
            item_columns=_source_skill_picker_columns,
            header_columns=["Skill", "Source", "Description"],
            item_ranker=_source_skill_ranker(matches),
            enter_action_label="choose",
            search_title="Search skills",
        )
```

- [ ] **Step 6: Verify CLI-focused tests pass**

Run:

```bash
uv run pytest tests/test_cli_tty_browsing_contracts.py tests/test_cli_add_contracts.py tests/test_cli_remove_contracts.py tests/test_cli_source_commands.py -q --no-cov
```

Expected: all selected CLI contract tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/sv/cli.py tests/test_cli_tty_browsing_contracts.py tests/test_cli_add_contracts.py tests/test_cli_remove_contracts.py tests/test_cli_source_commands.py
git commit -m "feat: label interactive search prompts by context"
```

---

### Task 5: Documentation and changelog updates

**Files:**
- Modify: `README.md`
- Modify: `docs/output.md`
- Modify: `docs/usage.md`
- Modify: `docs/getting-started.md`
- Modify: `docs/examples.md`
- Modify: `docs/commands.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_docs.py` only if existing docs assertions need new required snippets

- [ ] **Step 1: Write or update docs assertions**

In `tests/test_docs.py`, add a test near `test_command_reference_documents_search()`:

```python
def test_docs_describe_framed_interactive_search_prompt():
    documented = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            README_PATH,
            DOCS_DIR / "output.md",
            DOCS_DIR / "usage.md",
            DOCS_DIR / "getting-started.md",
            DOCS_DIR / "commands.md",
        ]
    )

    assert "framed search prompt" in documented
    assert "empty Enter" in documented
    assert "Esc cancels" in documented
```

Use existing path constants in `tests/test_docs.py`; if `README_PATH` is not present, use the project root constant already defined in the file to read `README.md`.

- [ ] **Step 2: Run docs tests and verify intended failure**

Run:

```bash
uv run pytest tests/test_docs.py -q --no-cov
```

Expected: the new docs assertion fails until docs mention the framed prompt and empty Enter behavior.

- [ ] **Step 3: Update user-facing docs**

Replace raw or vague search prompt wording with this behavior in each docs file:

```text
Press `/` to open the framed search prompt. Enter applies the typed search, empty Enter clears the active search, and Esc cancels without changing the current results.
```

Apply the wording to:
- `README.md` sections for `sv list` and `sv add -l`.
- `docs/output.md` TTY browser section.
- `docs/usage.md` interactive list and add picker sections.
- `docs/getting-started.md` interactive list and picker notes.
- `docs/examples.md` interactive list paragraph.
- `docs/commands.md` rows for `sv list`, the search command, and `sv add -l`, plus the search command details section.

Do not rewrite historical specs under `docs/superpowers/specs/`; they record earlier design decisions.

- [ ] **Step 4: Update changelog**

In `CHANGELOG.md`, add an Unreleased bullet under the top unreleased section:

```markdown
- Polished TTY `/` search with a framed inline prompt, contextual titles, and visible `empty Enter` clear guidance.
```

- [ ] **Step 5: Verify docs tests pass**

Run:

```bash
uv run pytest tests/test_docs.py -q --no-cov
```

Expected: all docs tests pass.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/output.md docs/usage.md docs/getting-started.md docs/examples.md docs/commands.md CHANGELOG.md tests/test_docs.py
git commit -m "docs: describe framed interactive search"
```

---

### Task 6: Final regression and release-gate verification

**Files:**
- Modify only if verification exposes issues in files changed by Tasks 1-5.

- [ ] **Step 1: Run focused interactive UI regression tests**

Run:

```bash
uv run pytest tests/test_search.py tests/test_table.py tests/test_selector.py tests/test_second_coverage_batch.py -q --no-cov
```

Expected: all selected tests pass.

- [ ] **Step 2: Run CLI contract regression tests touched by this plan**

Run:

```bash
uv run pytest tests/test_cli_tty_browsing_contracts.py tests/test_cli_add_contracts.py tests/test_cli_remove_contracts.py tests/test_cli_source_commands.py tests/test_docs.py -q --no-cov
```

Expected: all selected tests pass.

- [ ] **Step 3: Run fast local test loop**

Run:

```bash
uv run pytest -m "not integration and not security" -q --no-cov
```

Expected: all non-integration, non-security tests pass.

- [ ] **Step 4: Run full release verification**

Run the documented release gate from `docs/testing.md`:

```bash
uv run ruff check .
uv run ty check src tests
uv run pytest --cov=sv --cov-report=term-missing
rm -rf dist
uv build
tmp_venv="$(mktemp -d)"
trap 'rm -rf "$tmp_venv"' EXIT
python -m venv "$tmp_venv"
wheel_path="$(python -c 'from pathlib import Path; wheels = sorted(Path("dist").glob("sv-*.whl")); assert len(wheels) == 1, wheels; print(wheels[0])')"
expected_version="$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
uv pip install --python "$tmp_venv/bin/python" --link-mode=copy --no-index "$wheel_path"
"$tmp_venv/bin/sv" --help
EXPECTED_SV_VERSION="$expected_version" "$tmp_venv/bin/python" -c 'import os, sv; assert sv.__version__ == os.environ["EXPECTED_SV_VERSION"], sv.__version__'
```

Expected: every command exits with status 0; pytest coverage remains at or above the configured 95% threshold.

- [ ] **Step 5: Inspect the final diff**

Run:

```bash
git status --short
git log --oneline -6
git diff --stat HEAD~5..HEAD
```

Expected: only intentional code, test, docs, and changelog changes are present. Generated files such as `.coverage`, `dist/`, `.pytest_cache/`, and `htmlcov/` are not staged.

- [ ] **Step 6: Commit verification fixes if any were required**

If Step 4 exposed fixes, commit only those fixes from the files owned by this plan:

```bash
git add src/sv/search.py src/sv/table.py src/sv/selector.py src/sv/cli.py \
  tests/test_search.py tests/test_table.py tests/test_selector.py \
  tests/test_second_coverage_batch.py tests/test_cli_tty_browsing_contracts.py \
  tests/test_cli_add_contracts.py tests/test_cli_remove_contracts.py \
  tests/test_cli_source_commands.py README.md docs/output.md docs/usage.md \
  docs/getting-started.md docs/examples.md docs/commands.md CHANGELOG.md \
  tests/test_docs.py
git commit -m "fix: polish framed search verification issues"
```

Expected: no commit is created when Step 4 passes without fixes.

## Self-Review Checklist

- Every approved UX requirement maps to a task: framed prompt in Tasks 1-3, empty Enter hint in Tasks 1 and 5, Esc preservation in Tasks 2-3, footer/no-match in Tasks 2-3, contextual titles in Task 4, docs in Task 5.
- No runtime dependencies are added.
- Ranking semantics remain untouched.
- Table and selector both use shared `render_search_prompt()`.
- Line-count handling is tested at the core and integration levels.
- All commands use exact repo-local paths and `--no-cov` for focused loops.
- The final release gate matches `docs/testing.md`.

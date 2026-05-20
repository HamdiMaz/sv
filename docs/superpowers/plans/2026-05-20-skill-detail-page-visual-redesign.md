# Skill Detail Page Visual Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the interactive skill detail page into a polished, color-accented terminal card with the concise footer `a add • q back`.

**Architecture:** Keep the existing stdlib inline browser and detail-mode control flow. Add a small trusted styled-line primitive to `sv.table` so sv-owned ANSI styling can be applied after untrusted source metadata is escaped and fitted/wrapped to terminal width. Wire `sv.cli` skill detail renderers to return structured detail lines for `sv list`, `sv search`, and repo skill browsing while leaving non-TTY/plain output unchanged.

**Tech Stack:** Python 3.14, stdlib terminal output, pytest, ruff, ty, uv.

---

## Scout Summary

**Architecture:** `src/sv/cli.py` maps source skill rows back to `SourceSkill` entries and provides `detail_renderer` / `detail_actions` callbacks to `src/sv/table.py`. `src/sv/table.py` owns the TTY browser, list/detail mode, terminal redraw, sanitization, display-width fitting, and footer rendering.

**Likely files:**

- `src/sv/table.py`: add styled/wrapped detail-line support and change the default detail footer.
- `src/sv/ui.py`: re-export the detail-line helper through the existing UI adapter boundary.
- `src/sv/cli.py`: add a polished skill-detail card formatter and wire list/search/repo skill browsers to it.
- `tests/test_table.py`: cover styled detail rendering, wrapping, sanitization, width fitting, and concise footer.
- `tests/test_cli_tty_browsing_contracts.py`: update list/search/repo detail contracts for card output and footer help.
- `docs/output.md`, `docs/usage.md`: update user-facing detail-page wording for the new card shape and concise footer.

**Existing patterns:**

- Runtime UI stays dependency-free; `sv.ui` is a thin adapter over `sv.table` and `sv.selector`.
- Untrusted terminal content is escaped with `escape_terminal_controls()` before display.
- TTY tests strip sv-owned ANSI using `visible_text()` and assert visible display widths.
- Existing detail actions return status strings; the browser keeps detail mode open and rerenders with that status.

**Risks:**

- Raw ANSI from skill metadata must not be rendered. Only sv-owned style codes may reach stdout.
- Display-width fitting must ignore sv-owned ANSI by applying styles after fitting/wrapping visible text.
- Detail line counts must remain exact because redraw clearing uses the previous line count.
- Existing non-TTY tables and `_print_source_skill_detail()` should remain plain/scriptable.

**Tests:** Focus on `tests/test_table.py` for rendering mechanics and `tests/test_cli_tty_browsing_contracts.py` for list/search/repo browser wiring.

---

## Recommended Design

**Recommendation:** Add a `DetailLine` dataclass plus `detail_line()` helper in `src/sv/table.py`. A `DetailLine` stores visible text, a sv-owned style name, optional wrapping, and indentation. `_render_interactive_detail()` converts renderer output into visible lines by escaping untrusted text, wrapping/fitting by display width, then applying sv-owned ANSI styles.

**Components:**

- `DetailLine`: trusted render instruction for sv-owned detail content.
- `_format_detail_content_lines()`: converts string or sequence detail output into rendered lines.
- `_format_source_skill_detail_card()`: builds the approved card-style skill detail page from `SourceSkill` metadata.
- `_format_source_skill_unavailable_detail_card()`: preserves stale-row behavior with styled status-only output.

**Flow:** The CLI still passes the selected row and optional status into a detail renderer. The renderer returns `list[DetailLine]`. The table renderer escapes every `DetailLine.text`, wraps description/status lines when requested, applies styles from a closed mapping, appends `a add • q back`, and writes the final lines.

**Trade-offs:** This avoids a full TUI rewrite and avoids adding dependencies. Whole-line styling is simpler than fragment-level styling, but it still gives the page a polished card, color accents, readable metadata sections, wrapped descriptions, and a concise footer.

**Risks & mitigations:** ANSI safety is protected because style values are not taken from source metadata and user text is escaped before styling. Width safety is protected by running `_fit_text()` / `_wrap_cell()` before applying ANSI. Non-TTY compatibility is protected by keeping the plain `_format_source_skill_detail()` string helper for print paths.

**Testing strategy:** Add failing tests for the styled render primitive first, then CLI card wiring, then docs. Run focused tests after each task and the documented release gate at the end.

---

## Locked behavior

- Detail footer text is exactly `a add • q back`.
- Detail mode keys stay unchanged: `a` adds, `q`/Escape returns to the list.
- List-mode keys stay unchanged, including repo skill list `a add all`.
- Non-TTY list/search output and duplicate `Add as` sections remain unchanged.
- `_print_source_skill_detail()` remains plain text for script/debug paths.
- Source-controlled metadata and status strings are escaped before display.
- No runtime dependency is added.

## File structure

- Modify `src/sv/table.py`: styled detail primitive, detail-line formatting helpers, default footer.
- Modify `src/sv/ui.py`: expose `DetailLine` and `detail_line` from the UI boundary.
- Modify `src/sv/cli.py`: card formatter and detail renderer wiring.
- Modify `tests/test_table.py`: rendering primitive and footer tests.
- Modify `tests/test_cli_tty_browsing_contracts.py`: browser contract expectations.
- Modify `docs/output.md` and `docs/usage.md` for user-facing wording.

---

## Task 1: Add styled and wrapped detail-line rendering

**Files:**

- Modify: `src/sv/table.py`
- Modify: `src/sv/ui.py`
- Test: `tests/test_table.py`

- [ ] **Step 1: Write the failing tests**

Add `DetailLine` to the `sv.table` import list in `tests/test_table.py`:

```python
from sv.table import (
    DetailLine,
    TableState,
    _read_escape_sequence,
    _read_filter_query,
    _read_key,
    _read_search_query,
    _read_key_from_bytes,
    _render_interactive_detail,
    _render_interactive_table,
    browse_table,
    format_table,
)
```

Add these tests after `test_interactive_detail_trims_trailing_empty_sequence_lines_before_footer`:

```python
def test_interactive_detail_default_footer_is_concise(monkeypatch):
    monkeypatch.setenv("COLUMNS", "80")
    stdout = StringIO()

    line_count = _render_interactive_detail(
        ["alpha"],
        lambda row, status: "Skill: " + row[0],
        None,
        stdout,
    )

    visible_lines = [visible_text(line) for line in stdout.getvalue().splitlines()]
    assert line_count == 2
    assert visible_lines == ["Skill: alpha", "a add • q back"]


def test_interactive_detail_renders_styled_wrapped_lines_safely(monkeypatch):
    monkeypatch.setenv("COLUMNS", "28")
    stdout = StringIO()

    line_count = _render_interactive_detail(
        ["alpha"],
        lambda row, status: [
            DetailLine("╭─ Skill" + "─" * 32, style="accent"),
            DetailLine("│ safe\x1b title", style="title"),
            DetailLine(
                "Long description with 日本語 characters and emoji ✨",
                wrap=True,
                indent=2,
            ),
        ],
        None,
        stdout,
        detail_key_help="a add • q back",
    )

    output = stdout.getvalue()
    visible_lines = [visible_text(line) for line in output.splitlines()]
    assert "\x1b[38;5;" in output
    assert "safe\x1b title" not in visible_text(output)
    assert "safe\\x1b title" in visible_text(output)
    assert visible_lines[-1] == "a add • q back"
    assert line_count == len(visible_lines)
    assert line_count > 4
    assert all(display_width(line) <= 28 for line in visible_lines)
```

- [ ] **Step 2: Run tests to verify they fail for the intended missing behavior**

Run:

```bash
uv run pytest tests/test_table.py::test_interactive_detail_default_footer_is_concise tests/test_table.py::test_interactive_detail_renders_styled_wrapped_lines_safely -q --no-cov
```

Expected: FAIL. The first test sees `a add skill • q back`; the second test cannot import/use `DetailLine`.

- [ ] **Step 3: Implement the styled detail primitive**

In `src/sv/table.py`, replace the current detail-renderer type annotation in `browse_table()` and `_render_interactive_detail()` with the new aliases. Add the dataclasses and helpers near the existing ANSI constants:

```python
_DETAIL_FG_ACCENT = "\x1b[38;5;80m"
_DETAIL_FG_TITLE = "\x1b[38;5;183m"
_DETAIL_FG_SUCCESS = "\x1b[38;5;114m"
_DETAIL_STYLES = {
    "accent": _DETAIL_FG_ACCENT,
    "muted": _FG_MUTED,
    "rule": _FG_RULE,
    "success": _DETAIL_FG_SUCCESS,
    "title": _DETAIL_FG_TITLE,
}


@dataclass(frozen=True)
class DetailLine:
    """A safely rendered detail line styled only by sv-owned ANSI codes."""

    text: str
    style: str = ""
    wrap: bool = False
    indent: int = 0


def detail_line(
    text: object,
    *,
    style: str = "",
    wrap: bool = False,
    indent: int = 0,
) -> DetailLine:
    return DetailLine(str(text), style=style, wrap=wrap, indent=max(indent, 0))


DetailRenderLine = str | DetailLine
DetailRenderResult = str | Sequence[DetailRenderLine]


@dataclass(frozen=True)
class _RenderedDetailLine:
    visible: str
    styled: str
```

Update the `detail_renderer` parameters in `browse_table()` and `_render_interactive_detail()` to:

```python
detail_renderer: Callable[[Sequence[str], str | None], DetailRenderResult] | None = None,
```

Change both default footer values in `src/sv/table.py` from:

```python
detail_key_help: str = "a add skill • q back",
```

to:

```python
detail_key_help: str = "a add • q back",
```

Replace the content-line block in `_render_interactive_detail()` with:

```python
    terminal_width = _terminal_width()
    rendered = detail_renderer(row, status) if detail_renderer is not None else ""
    rendered_lines = _format_detail_content_lines(rendered, terminal_width)
    while rendered_lines and rendered_lines[-1].visible == "":
        rendered_lines.pop()
    lines = [line.styled for line in rendered_lines]
    footer = _fit_text(_sanitize_cell(detail_key_help), terminal_width)
    lines.append(f"{_FG_MUTED}{footer}{_RESET}")
```

Add these helpers below `_render_interactive_detail()` and above `_render_interactive_table()`:

```python
def _format_detail_content_lines(
    rendered: DetailRenderResult, terminal_width: int
) -> list[_RenderedDetailLine]:
    if isinstance(rendered, str):
        content_lines: Sequence[DetailRenderLine] = rendered.splitlines() or [""]
    else:
        content_lines = rendered
    formatted: list[_RenderedDetailLine] = []
    for line in content_lines:
        formatted.extend(_format_detail_line(line, terminal_width))
    return formatted


def _format_detail_line(
    line: DetailRenderLine, terminal_width: int
) -> list[_RenderedDetailLine]:
    if isinstance(line, DetailLine):
        safe_text = _sanitize_cell(line.text)
        indent = " " * min(line.indent, max(terminal_width - 1, 0))
        if line.wrap:
            body_width = max(terminal_width - _display_width(indent), 1)
            parts = _wrap_cell(safe_text, body_width) if safe_text else [""]
            return [
                _styled_detail_line(_fit_text(indent + part, terminal_width), line.style)
                for part in parts
            ]
        return [_styled_detail_line(_fit_text(indent + safe_text, terminal_width), line.style)]
    visible = _fit_text(_sanitize_cell(str(line)), terminal_width)
    return [_RenderedDetailLine(visible=visible, styled=visible)]


def _styled_detail_line(visible: str, style: str) -> _RenderedDetailLine:
    prefix = _DETAIL_STYLES.get(style)
    if not prefix:
        return _RenderedDetailLine(visible=visible, styled=visible)
    return _RenderedDetailLine(visible=visible, styled=f"{prefix}{visible}{_RESET}")
```

In `src/sv/ui.py`, replace the table import line:

```python
from sv.table import CellWidths, format_table
```

with:

```python
from sv.table import CellWidths, DetailLine, detail_line, format_table
```

- [ ] **Step 4: Run the focused table tests**

Run:

```bash
uv run pytest tests/test_table.py::test_interactive_detail_default_footer_is_concise tests/test_table.py::test_interactive_detail_renders_styled_wrapped_lines_safely tests/test_table.py::test_interactive_detail_sanitizes_and_fits_lines tests/test_table.py::test_interactive_detail_splits_string_output_into_physical_lines tests/test_table.py::test_interactive_detail_trims_trailing_empty_sequence_lines_before_footer -q --no-cov
```

Expected: PASS. Existing string and sequence behavior remains safe, and the new styled/wrapped path passes.

- [ ] **Step 5: Commit**

```bash
git add src/sv/table.py src/sv/ui.py tests/test_table.py
git commit -m "feat: add styled skill detail rendering"
```

---

## Task 2: Add the polished source-skill detail card and wire browsers

**Files:**

- Modify: `src/sv/cli.py`
- Modify: `tests/test_cli_tty_browsing_contracts.py`

- [ ] **Step 1: Write the failing CLI contract updates**

In `tests/test_cli_tty_browsing_contracts.py`, add this helper near the existing test helpers:

```python
def _detail_visible_text(detail) -> str:
    if isinstance(detail, str):
        return detail
    return "\n".join(getattr(line, "text", str(line)) for line in detail)
```

Update the detail assertions in `test_tty_list_browses_source_skills_with_details_by_default`:

```python
        detail = kwargs["detail_renderer"](rows[0], None)
        detail_text = _detail_visible_text(detail)
        assert "╭─ Skill" in detail_text
        assert "alpha ● available" in detail_text
        assert "Source  " in detail_text
        assert "Path    " in detail_text
        assert "├─ Description" in detail_text
        assert "Alpha skill." in detail_text
        assert kwargs["detail_key_help"] == "a add • q back"
```

Update the detail assertions in `test_tty_search_browses_ranked_matches_with_details_by_default`:

```python
        detail = kwargs["detail_renderer"](rows[0], None)
        detail_text = _detail_visible_text(detail)
        assert "╭─ Skill" in detail_text
        assert "docs ● available" in detail_text
        assert "Source  " in detail_text
        assert "Path    " in detail_text
        assert "├─ Description" in detail_text
        assert "Docs skill." in detail_text
        assert kwargs["detail_key_help"] == "a add • q back"
```

Update `test_tty_detail_renderer_with_stale_row_preserves_status_line`:

```python
    assert [_detail_visible_text(detail) for detail in detail_texts] == ["Status\n  Still open"]
```

Update `test_tty_repo_skill_browser_detail_action_installs_one_skill`:

```python
            detail = kwargs["detail_renderer"](rows[0], None)
            detail_text = _detail_visible_text(detail)
            assert "╭─ Skill" in detail_text
            assert "alpha ● available" in detail_text
            assert "Description" in detail_text
            assert "Alpha skill." in detail_text
```

Update `test_tty_repo_skill_browser_detail_render_does_not_install_skill`:

```python
            detail = kwargs["detail_renderer"](rows[0], None)
            detail_text = _detail_visible_text(detail)
            assert "╭─ Skill" in detail_text
            assert "alpha ● available" in detail_text
```

- [ ] **Step 2: Run tests to verify they fail for missing card output**

Run:

```bash
uv run pytest tests/test_cli_tty_browsing_contracts.py::test_tty_list_browses_source_skills_with_details_by_default tests/test_cli_tty_browsing_contracts.py::test_tty_search_browses_ranked_matches_with_details_by_default tests/test_cli_tty_browsing_contracts.py::test_tty_detail_renderer_with_stale_row_preserves_status_line tests/test_cli_tty_browsing_contracts.py::test_tty_repo_skill_browser_detail_action_installs_one_skill tests/test_cli_tty_browsing_contracts.py::test_tty_repo_skill_browser_detail_render_does_not_install_skill -q --no-cov
```

Expected: FAIL because the renderer still returns the raw `Skill: ...` multiline string and `_browse_source_skills()` still passes `a add skill • q back`.

- [ ] **Step 3: Implement the card formatter**

In `src/sv/cli.py`, update the `sv.ui` import block to include `DetailLine` and `detail_line`:

```python
from sv.ui import (
    CellWidths,
    DetailLine,
    browse_tty_table,
    detail_line,
    format_plain_table as format_table,
    select_tty_items as select_skills,
)
```

In `_browse_repo_source_skills()`, replace the detail renderer body with:

```python
    def render_detail(row: Sequence[str], status: str | None) -> list[DetailLine]:
        entry = _entry_for_interactive_row(row, rows, entries)
        if entry is None:
            return _format_source_skill_unavailable_detail_card(status)
        return _format_source_skill_detail_card(entry, status=status)
```

In `_browse_source_skills()`, replace the detail renderer body with the same card renderer:

```python
    def render_detail(row: Sequence[str], status: str | None) -> list[DetailLine]:
        entry = _entry_for_interactive_row(row, rows, entries)
        if entry is None:
            return _format_source_skill_unavailable_detail_card(status)
        return _format_source_skill_detail_card(entry, status=status)
```

In `_browse_source_skills()`, replace the explicit footer help:

```python
        detail_key_help="a add skill • q back",
```

with:

```python
        detail_key_help="a add • q back",
```

Add these helpers directly below `_format_source_skill_detail()` so the plain formatter remains available for `_print_source_skill_detail()`:

```python
def _format_source_skill_detail_card(
    entry: SourceSkill, status: str | None = None
) -> list[DetailLine]:
    lines = [
        detail_line("╭─ Skill" + "─" * 48, style="accent"),
        detail_line(f"│ {entry.name} ● available", style="title"),
        detail_line(f"│ Source  {entry.repo_id}"),
        detail_line(f"│ Path    {entry.source_relative_path}"),
        detail_line("├─ Description" + "─" * 42, style="accent"),
        detail_line(entry.description, wrap=True, indent=2),
    ]
    if status is not None:
        lines.extend(
            [
                detail_line(""),
                detail_line("Status", style="accent"),
                detail_line(status, wrap=True, indent=2),
            ]
        )
    return lines


def _format_source_skill_unavailable_detail_card(
    status: str | None = None,
) -> list[DetailLine]:
    safe_status = status or "Selected skill is no longer available."
    return [
        detail_line("Status", style="accent"),
        detail_line(safe_status, wrap=True, indent=2),
    ]
```

Keep the existing `_format_source_skill_unavailable_detail()` string helper unchanged if any plain tests still use it.

- [ ] **Step 4: Run the focused CLI contract tests**

Run:

```bash
uv run pytest tests/test_cli_tty_browsing_contracts.py::test_tty_list_browses_source_skills_with_details_by_default tests/test_cli_tty_browsing_contracts.py::test_tty_search_browses_ranked_matches_with_details_by_default tests/test_cli_tty_browsing_contracts.py::test_tty_detail_renderer_with_stale_row_preserves_status_line tests/test_cli_tty_browsing_contracts.py::test_tty_repo_skill_browser_detail_action_installs_one_skill tests/test_cli_tty_browsing_contracts.py::test_tty_repo_skill_browser_detail_render_does_not_install_skill -q --no-cov
```

Expected: PASS. The renderer returns card lines, detail add still installs one skill, stale rows show a status-only card, and list/search pass `a add • q back`.

- [ ] **Step 5: Run broader TTY browsing tests**

Run:

```bash
uv run pytest tests/test_cli_tty_browsing_contracts.py tests/test_table.py -q --no-cov
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/sv/cli.py tests/test_cli_tty_browsing_contracts.py
git commit -m "feat: polish skill detail cards"
```

---

## Task 3: Update user-facing docs for the polished detail page

**Files:**

- Modify: `docs/usage.md`
- Modify: `docs/output.md`
- Test: `tests/test_docs.py`

- [ ] **Step 1: Update docs wording**

In `docs/output.md`, replace the sentence:

```markdown
Detail pages show focused skill metadata such as source, path, and description; copy-paste `Add as` references stay in non-interactive duplicate sections where they are easier to use from scripts. Long values are truncated to the current terminal width, and control characters from source names or descriptions are escaped before display.
```

with:

```markdown
Detail pages show a framed, color-accented card with the skill name, source, path, wrapped description, optional add status, and the concise footer `a add • q back`; copy-paste `Add as` references stay in non-interactive duplicate sections where they are easier to use from scripts. Long values fit the current terminal width, and control characters from source names, paths, descriptions, or statuses are escaped before display.
```

In `docs/usage.md`, replace:

```markdown
In an interactive terminal, `sv list` opens an interactive browser. Use ↑/↓ to move and `/` to open a visible ranked search prompt. Enter opens an inline detail page with focused skill metadata such as source, path, and description. On the detail page, `a` adds the shown skill, shows the add result/status, and `q` or Esc returns to the list. In the list, `q` or Esc exits the browser.
```

with:

```markdown
In an interactive terminal, `sv list` opens an interactive browser. Use ↑/↓ to move and `/` to open a visible ranked search prompt. Enter opens an inline detail card with the skill name, source, path, wrapped description, and concise footer `a add • q back`. On the detail page, `a` adds the shown skill, shows the add result/status, and `q` or Esc returns to the list. In the list, `q` or Esc exits the browser.
```

- [ ] **Step 2: Run docs tests**

Run:

```bash
uv run pytest tests/test_docs.py -q --no-cov
```

Expected: PASS. Markdown links and documented command references remain valid.

- [ ] **Step 3: Commit**

```bash
git add docs/usage.md docs/output.md tests/test_docs.py
git commit -m "docs: describe polished skill detail cards"
```

---

## Task 4: Final verification and cleanup

**Files:**

- Verify: full repository

- [ ] **Step 1: Run formatter/linter check**

Run:

```bash
uv run ruff check .
```

Expected: PASS with no lint errors.

- [ ] **Step 2: Run type checks**

Run:

```bash
uv run ty check src tests
```

Expected: PASS with no type errors.

- [ ] **Step 3: Run full coverage test suite**

Run:

```bash
uv run pytest --cov=sv --cov-report=term-missing
```

Expected: PASS with coverage at or above 95%.

- [ ] **Step 4: Build package**

Run:

```bash
rm -rf dist
uv build
```

Expected: PASS and exactly one `sv-*.whl` appears under `dist/`.

- [ ] **Step 5: Smoke-test the built wheel**

Run:

```bash
tmp_venv="$(mktemp -d)"
trap 'rm -rf "$tmp_venv"' EXIT
python -m venv "$tmp_venv"
wheel_path="$(python -c 'from pathlib import Path; wheels = sorted(Path("dist").glob("sv-*.whl")); assert len(wheels) == 1, wheels; print(wheels[0])')"
expected_version="$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
uv pip install --python "$tmp_venv/bin/python" --link-mode=copy --no-index "$wheel_path"
"$tmp_venv/bin/sv" --help
EXPECTED_SV_VERSION="$expected_version" "$tmp_venv/bin/python" -c 'import os, sv; assert sv.__version__ == os.environ["EXPECTED_SV_VERSION"], sv.__version__'
```

Expected: PASS. `sv --help` exits 0 and the installed package version matches `pyproject.toml`.

- [ ] **Step 6: Inspect final state**

Run:

```bash
git status --short --branch
git log --oneline -5
```

Expected: branch is `improve-detail-page`; worktree is clean except for deliberate uncommitted release artifacts if the build leaves any ignored files.

---

## Self-review notes

- Every approved design requirement maps to a task: card styling and colors in Tasks 1-2, concise footer in Tasks 1-2, source/path/description/status in Task 2, docs in Task 3, verification in Task 4.
- No source discovery, cache, install, or non-TTY output behavior changes are planned.
- The plan keeps all changes inside `/home/maz/Projects/sv/.worktrees/improve-detail-page`.
- The existing base detail-mode spec remains at `docs/superpowers/specs/2026-05-20-skill-detail-page-design.md`; the visual redesign spec is `docs/superpowers/specs/2026-05-20-skill-detail-page-visual-redesign.md`.

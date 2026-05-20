# Interactive Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hidden interactive `/` filtering with visible, applied-on-Enter ranked search across table browsers and multi-select pickers.

**Architecture:** Add one shared lightweight interactive-search helper for generic ranked index search and prompt input. Keep table and picker state indexed by original row/item positions, but derive visible indices from ranked search results or an optional domain-aware ranker. CLI source-skill flows pass rankers backed by `search_source_catalog()` while generic repo/local rows use the helper fallback.

**Tech Stack:** Python 3.14 stdlib, existing inline TTY rendering, pytest/ruff/ty via `uv`.

---

## File Structure

- Create `src/sv/search.py`: generic ranked index search, prompt input loop, and prompt result type shared by table/picker.
- Modify `src/sv/table.py`: `TableState` ranked search state, visible `Search:` prompt rendering, `/ search` footer, optional `row_ranker` callback, compatibility with old synthetic `filter:` test keys.
- Modify `src/sv/selector.py`: `SelectionState` ranked search state, visible `Search:` prompt rendering, `/ search` footer, optional `item_ranker` callback, selection persistence.
- Modify `src/sv/cli.py`: source-skill ranker callbacks for `sv list`, `sv search`, repo skill browser, `sv add -l`, and duplicate source chooser.
- Modify docs (`README.md`, `docs/*.md`) that describe `/ filter` so they describe `/ search`.
- Modify `CHANGELOG.md`: add one Unreleased bullet for interactive search.
- Modify tests: `tests/test_table.py`, `tests/test_selector.py`, `tests/test_cli_tty_browsing_contracts.py`, `tests/test_cli_add_contracts.py`, `tests/test_second_coverage_batch.py`, and add `tests/test_search.py` if helper coverage is clearer there.

## Constraints

- No runtime dependencies.
- Non-TTY output for `sv list`, `sv search`, and repo commands must not change.
- Search updates results only after Enter; no live filtering.
- Esc cancels prompt input without mutating the previous search.
- EOF applies captured input, or clears search if no input was captured.
- Displayed prompt/query text must be terminal-control escaped.
- Keep row/item identity as original indices so details/actions/selections keep working.
- Make small commits after each tested vertical slice.

---

### Task 1: Shared ranked search and table browser vertical slice

**Files:**
- Create: `src/sv/search.py`
- Create or modify: `tests/test_search.py`
- Modify: `src/sv/table.py`
- Modify: `tests/test_table.py`

- [ ] **Step 1: Write failing tests**

Add tests that prove:

```python
def test_ranked_search_indices_orders_exact_prefix_substring_and_fuzzy_matches():
    rows = [
        ("alpha docs",),
        ("docs",),
        ("docs-helper",),
        ("find docs",),
        ("detailed overview",),
    ]

    assert ranked_search_indices("docs", rows) == [1, 2, 0, 3]
    assert ranked_search_indices("do", rows)[:2] == [1, 2]
    assert ranked_search_indices("dh", rows) == [2]
```

Update table tests to assert:

```python
state = TableState(["Skill"], [["alpha"], ["docs"], ["docs-helper"], ["find-docs"]])
state.set_search("docs")
assert [row[0] for _, row in state.visible_rows()] == ["docs", "docs-helper", "find-docs"]
```

and footer/prompt behavior:

```python
assert "/ search" in visible_footer
assert "search: docs" in visible_footer
assert _read_key_from_bytes(b"/") == "search"
```

Add a browser-loop test where `_read_key` yields `"search"`, `_read_search_query` returns an applied query, Enter returns the ranked row, and Esc/cancel leaves the previous search unchanged.

- [ ] **Step 2: Verify tests fail for the intended missing behavior**

Run:

```bash
uv run pytest tests/test_search.py tests/test_table.py -q --no-cov
```

Expected: failures reference missing `sv.search`, `set_search`, `/ search`, or visible prompt behavior.

- [ ] **Step 3: Implement minimal code**

Implement `ranked_search_indices(query, fields_by_item)` in `src/sv/search.py` with all query terms required and scoring:

- exact case-insensitive field match: `0`
- prefix: `2`
- substring: `6 + min(position, 20)`
- ordered-character fuzzy for terms of length at least 2: `100 + first_position + gaps`
- add a small per-field weight so earlier fields sort ahead of later fields
- sort by `(score, original_index)`

Implement shared `SearchPromptResult` and `read_search_prompt(fd, stdout, render_prompt, previous_line_count=0)` so table/picker can render `Search: <escaped query>` while bytes are read.

Update `TableState` with `set_search()`, `search_query` property, optional `row_ranker`, and generic ranked fallback. Keep `set_filter()` as an alias for compatibility. Update table footer and `/` handling to use search language and visible prompt.

- [ ] **Step 4: Verify table slice passes**

Run:

```bash
uv run pytest tests/test_search.py tests/test_table.py -q --no-cov
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/sv/search.py src/sv/table.py tests/test_search.py tests/test_table.py
git commit -m "feat: add ranked search to table browser"
```

---

### Task 2: Multi-select picker ranked search

**Files:**
- Modify: `src/sv/selector.py`
- Modify: `tests/test_selector.py`
- Modify: `tests/test_second_coverage_batch.py`

- [ ] **Step 1: Write failing tests**

Update picker tests to use `/ search` language and add coverage that:

```python
state = SelectionState(["alpha", "docs", "docs-helper", "find-docs"])
state.set_search("docs")
assert [item for _, item in state.visible_items()] == ["docs", "docs-helper", "find-docs"]
```

Add/adjust tests for:

- selected hidden items remain selected after search
- no-match footer is stable and says `search: missing`
- `_read_key_from_bytes(b"/") == "search"`
- prompt rendering shows `Search: docs`
- Esc cancellation keeps the previous search unchanged in `select_skills()`

- [ ] **Step 2: Verify tests fail**

Run:

```bash
uv run pytest tests/test_selector.py tests/test_second_coverage_batch.py::test_selector_empty_and_filtered_interactive_paths -q --no-cov
```

Expected: failures reference old `/ filter` behavior or missing picker search APIs.

- [ ] **Step 3: Implement minimal code**

Mirror the table slice in `SelectionState`:

- add `set_search()`, `search_query` property, optional `item_ranker`, and ranked fallback via `ranked_search_indices()`
- keep `set_filter()` and synthetic `filter:` key compatibility
- use `read_search_prompt()` for visible prompt input
- update help text to `/ search` and `search: <query>`
- preserve selected original indices

- [ ] **Step 4: Verify picker slice passes**

Run:

```bash
uv run pytest tests/test_selector.py tests/test_second_coverage_batch.py::test_selector_empty_and_filtered_interactive_paths -q --no-cov
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/sv/selector.py tests/test_selector.py tests/test_second_coverage_batch.py
git commit -m "feat: add ranked search to interactive picker"
```

---

### Task 3: Source-skill-aware rankers in CLI interactive flows

**Files:**
- Modify: `src/sv/cli.py`
- Modify: `tests/test_cli_tty_browsing_contracts.py`
- Modify: `tests/test_cli_add_contracts.py`

- [ ] **Step 1: Write failing tests**

Add tests that capture browser/selector kwargs and assert a callable ranker is passed:

```python
ranker = kwargs["row_ranker"]
assert callable(ranker)
assert ranker("docs") == [expected_index_order]
```

Cover:

- `sv list` source browser
- `sv search <query>` source browser
- repo skill browser opened from `sv repo list`
- `sv add -l` selector with `item_ranker`
- duplicate source chooser if it uses the same source-skill picker

Use source entries whose generic row substring order would not equal `search_source_catalog()` ordering.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
uv run pytest tests/test_cli_tty_browsing_contracts.py tests/test_cli_add_contracts.py::test_add_list_picker_provides_structured_skill_source_description_columns tests/test_cli_add_contracts.py::test_duplicate_source_chooser_uses_aligned_source_table -q --no-cov
```

Expected: missing `row_ranker`/`item_ranker` kwargs.

- [ ] **Step 3: Implement minimal code**

Add helpers in `src/sv/cli.py`:

```python
def _source_skill_ranker(entries: Sequence[SourceSkill]) -> Callable[[str], list[int]]:
    positions = {id(entry): index for index, entry in enumerate(entries)}
    def rank(query: str) -> list[int]:
        return [positions[id(entry)] for entry in search_source_catalog(entries, query)]
    return rank
```

Use that helper in `_browse_source_skills()`, `_browse_repo_source_skills()`, `_handle_add_interactive()`, and `_choose_skill()` by passing `row_ranker=` or `item_ranker=`. Leave repo-list and removal pickers to generic ranked fallback.

- [ ] **Step 4: Verify CLI slice passes**

Run:

```bash
uv run pytest tests/test_cli_tty_browsing_contracts.py tests/test_cli_add_contracts.py::test_add_list_picker_provides_structured_skill_source_description_columns tests/test_cli_add_contracts.py::test_duplicate_source_chooser_uses_aligned_source_table -q --no-cov
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/sv/cli.py tests/test_cli_tty_browsing_contracts.py tests/test_cli_add_contracts.py
git commit -m "feat: use source ranking in interactive skill search"
```

---

### Task 4: Documentation, changelog, and final verification

**Files:**
- Modify: `README.md`
- Modify: `docs/usage.md`
- Modify: `docs/output.md`
- Modify: `docs/getting-started.md`
- Modify: `docs/examples.md`
- Modify: `docs/commands.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update docs and changelog**

Replace user-facing `/ filter` wording for interactive lists with `/ search`, visible prompt, and ranked search language. Add this `CHANGELOG.md` bullet under `## Unreleased`:

```markdown
- Added visible ranked `/` search to interactive table browsers and multi-select pickers, including source-skill-aware ordering that matches `sv search` in source skill lists.
```

- [ ] **Step 2: Run focused regression tests**

```bash
uv run pytest tests/test_search.py tests/test_table.py tests/test_selector.py tests/test_cli_tty_browsing_contracts.py tests/test_cli_add_contracts.py tests/test_second_coverage_batch.py::test_selector_empty_and_filtered_interactive_paths tests/test_second_coverage_batch.py::test_table_remaining_interactive_branches -q --no-cov
```

Expected: all selected tests pass.

- [ ] **Step 3: Run release-gate checks**

```bash
uv run ruff check .
uv run ty check src tests
uv run pytest -m "not integration and not security" -q --no-cov
uv run pytest --cov=sv --cov-report=term-missing
```

Expected: all commands exit 0.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/usage.md docs/output.md docs/getting-started.md docs/examples.md docs/commands.md CHANGELOG.md
git commit -m "docs: document interactive search"
```

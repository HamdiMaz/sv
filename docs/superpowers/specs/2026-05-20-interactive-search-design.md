# Interactive Search Design

Date: 2026-05-20
Branch: fix-search

## Problem

Several interactive `sv` commands advertise `/` as filtering or searching rows, but the current behavior is hard to use: after pressing `/`, input is typed without a visible prompt and the results are only narrowed by substring matching in original order. Users expect `/` to open a visible search prompt and then rank results similarly to `sv search`.

## Goals

- Make `/` open a visible inline `Search:` prompt in every interactive list.
- Show typed search text while the prompt is active.
- Apply the search when Enter is pressed.
- Rank matching results instead of preserving the original order.
- Reuse `sv search`-style ranking for source skill lists.
- Keep existing navigation, selection, detail, action, cancel, and non-TTY fallback behavior.

## Non-goals

- Do not add live filtering while typing; results update after Enter.
- Do not change non-interactive `sv list`, `sv search`, or `sv repo list` table output.
- Do not add new runtime dependencies.
- Do not replace the existing inline TTY UI with a full-screen UI.

## Affected Commands

The new `/` search behavior applies to all interactive lists:

- `sv list`
- `sv search <query>`
- `sv repo list` and `sv repo -l`
- The repo skill browser opened from `sv repo list`
- `sv add -l`
- `sv remove -l`
- `sv repo remove -l`

## Architecture

### Shared ranked search helper

Add a small reusable ranking helper for interactive UI state. It should accept a query and searchable text fields, return matching item indices, and sort matches by score. The generic ranking should mirror the lightweight shape of `sv search`:

1. exact case-insensitive match
2. prefix match
3. substring match
4. ordered-character fuzzy match where appropriate

This keeps repo rows, local skill rows, and generic picker rows useful without coupling those UI primitives to source-skill catalog types.

### Source-skill-aware ranking

Source skill browsers should use the existing catalog search semantics from `search_source_catalog` or the same lower-level scoring behavior. This preserves `sv search` relevance across `/` inside `sv list`, `sv search`, and repo skill browsers.

Because table rows are what the browser currently owns, the CLI should pass an optional row-ranking callback when it has richer domain objects available. The callback maps the current query to row indices in ranked order. Generic tables can fall back to the shared row search helper.

### Table browser state

`TableState` should store a `search_query` and derive visible rows from ranked indices. `/` enters search-input mode. Enter applies the query, resets the cursor and viewport to the first result, and redraws the table. Empty query clears search and restores all rows.

The footer should use search language, for example:

```text
Showing 1-5 of 12 matching 30 • search: docs • ↑/↓ move • ←/→ page • / search • Enter details • q back
```

### Multi-select picker state

`SelectionState` should mirror the table browser behavior with `search_query` and ranked visible indices. Existing selections remain selected even when hidden by the current search. Enter keeps its current meaning: confirm selected items. Empty search restores all items.

The picker footer should use search language and preserve current controls, for example:

```text
Showing 1-5 of 8 matching 30 • search: docs • ↑/↓ move • ←/→ page • Space select • Enter confirm • / search • q cancel
```

## Search Prompt Behavior

When the user presses `/`, the UI temporarily renders a prompt line such as:

```text
Search: docs
```

Input behavior:

- Printable characters append to the query and redraw the prompt.
- Backspace removes one byte/character from the buffered query using the current simple terminal input model.
- Enter applies the query.
- Esc cancels prompt input and leaves the previous search/results unchanged.
- EOF applies the current buffered query if any input was captured, otherwise treats it as an empty search.
- Query text is sanitized before display so terminal controls cannot affect rendering.

## Data Flow

1. User opens an interactive list.
2. UI state starts with no search query and all rows/items visible.
3. User presses `/`.
4. UI renders `Search:` and captures query input with visible feedback.
5. User presses Enter.
6. UI sanitizes and stores the query.
7. UI computes ranked matching indices using the supplied domain ranker or generic ranker.
8. UI resets cursor and viewport to the top of the ranked results.
9. Existing Enter/detail/action/selection behavior operates on the currently highlighted ranked result.

## Error Handling and Safety

- Preserve existing TTY setup and restoration errors as `SvError` messages.
- Preserve existing non-TTY fallback behavior in CLI commands.
- Keep invalid UTF-8 replacement decoding for search input.
- Escape terminal control characters in displayed queries, rows, labels, and prompt text.
- No-match searches render a stable empty-results view instead of exiting.
- Search cancellation does not mutate the previous search state.

## Testing Plan

- Unit-test table state ranked search with exact, prefix, substring, and fuzzy matches.
- Unit-test visible table search prompt rendering and Enter/Esc behavior.
- Unit-test table footer text changes from `/ filter` to `/ search` and `search: <query>`.
- Unit-test picker ranked search and prompt behavior.
- Unit-test picker selection persistence when selected rows are hidden by search.
- Add CLI-level tests that source-skill browsers pass a source-skill-aware ranker so `/` matches `sv search` ordering.
- Keep existing TTY fallback and non-interactive output tests passing.

## Compatibility

This is a behavior improvement for interactive TTY sessions only. Scripts and CI output remain unchanged. Existing keybindings remain available except that `/` is now described and implemented as search rather than hidden filtering.

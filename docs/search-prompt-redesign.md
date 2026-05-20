# Search Prompt Redesign

## Context

The current interactive search prompt is a raw one-line `Search:` input. It works, but it feels unfinished next to the rest of the TTY browser UI and does not clearly explain important behavior such as clearing an existing search.

This redesign applies to the TTY search experience used by table browsing and skill selection flows.

## Goals

- Make `/` search look intentional, polished, and consistent with the existing terminal UI.
- Keep the behavior conservative: search is typed in a prompt and applied with Enter.
- Use one shared search prompt presentation for all TTY search surfaces.
- Make keyboard behavior visible at first glance, including that empty Enter clears the active search.
- Improve footer and no-match states so the active search is easy to understand.

## Non-goals

- No live filtering while the user types.
- No external terminal UI dependency.
- No command-palette overlay or major browser architecture rewrite.
- No changes to search ranking semantics.

## Chosen Direction

Use a framed inline search prompt.

When the user presses `/`, the current TTY UI is redrawn with a compact framed prompt below the visible rows. The prompt includes a contextual title, a styled input field, and concise keyboard hints. Pressing Enter applies the query; pressing Esc cancels the prompt and preserves the previous filtered state.

The prompt hint should include:

```text
Enter apply • empty Enter clear • Esc cancel
```

## UX States

### Opening search

- The user presses `/` from a table browser or multi-select picker.
- A framed prompt appears in place of the raw `Search:` line.
- The title adapts to context, for example:
  - `Search skills`
  - `Search repositories`
  - `Search rows`
- The input placeholder communicates that the user can type a query.

### Typing

- Typed characters appear inside the framed input field.
- Control characters are escaped before display.
- The visible result list is not filtered until Enter is pressed.

### Applying search

- Enter applies the query.
- The browser returns to the filtered table or picker.
- The footer shows the active query as a subtle search indicator.

### Clearing search

- Empty Enter clears the search.
- The full unfiltered list returns.
- The footer search indicator disappears.

### Cancelling search

- Esc cancels the temporary prompt.
- Any previous active search remains unchanged.
- The browser returns to the previous visible state.

### No matches

- No-match state should be clearer than a blank list.
- The UI should show the searched query and a short hint, such as trying a skill name, repo id, source path, or description word where applicable.

## Component Design

Implement the visual prompt as shared search UI in `src/sv/search.py` so table and selector flows do not drift apart.

Suggested structure:

- Add a small prompt configuration object or equivalent parameters for:
  - title
  - placeholder
  - total row/item count label
  - help text
- Add a shared renderer for the framed prompt.
- Update `read_search_prompt()` so prompt redraw supports multi-line prompt output, not just a single line.
- Keep input capture behavior in the existing low-level search reader.
- Update `src/sv/table.py` and `src/sv/selector.py` to pass context labels and use the shared renderer.

Footer rendering can remain local to `table.py` and `selector.py`, but the wording and styling should be consistent.

## Accessibility and Terminal Constraints

- Use ANSI styling only; do not require color support for correctness.
- Fit and truncate long text to terminal width.
- Preserve safe rendering by escaping terminal control characters.
- Degrade gracefully in narrow terminals by truncating title/help text before query safety is compromised.

## Testing

Add tests for:

- Shared framed prompt rendering.
- Multi-line prompt redraw line counts.
- Escaped unsafe input in the framed prompt.
- Empty Enter clearing search.
- Esc cancellation preserving the previous search.
- Table browser using the framed prompt.
- Multi-select picker using the framed prompt.
- Improved footer search indicator and no-match copy.

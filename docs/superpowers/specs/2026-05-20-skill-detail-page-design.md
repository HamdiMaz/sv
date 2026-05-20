# Skill detail page design

Date: 2026-05-20
Branch: `detail-page`

## Problem

In the interactive skill browsers, pressing Enter currently runs a CLI callback that prints skill details above the inline table. This breaks the browsing experience: the user loses visual context, details feel like stray output, and there is no quick detail-level add action.

## Goals

- Pressing Enter on a skill switches the same terminal region into a focused detail page.
- The detail page shows skill metadata without printing stray output above the browser.
- Pressing `a` on a skill detail page adds that specific skill.
- After adding, the user stays on the detail page and sees the add result or status.
- `q` or Esc returns from detail page to the list; from list it keeps the existing back/quit behavior.
- Apply the flow consistently to `sv list`, `sv search`, and repo skill browsing.
- Preserve non-interactive output and script behavior.

## Non-goals

- Rendering the full `SKILL.md` body in the detail page.
- Introducing a new TUI dependency.
- Reworking plain table formatting.
- Changing source discovery, cache semantics, or install behavior outside the interactive browser.

## Chosen approach

Enhance the existing stdlib inline table browser with list/detail modes.

The table browser remains the single renderer for the interactive region. In list mode it renders the existing filterable table. When Enter is pressed and a detail renderer is available, the browser switches to detail mode instead of clearing the table and letting a callback print to stdout. Detail mode renders metadata lines and a footer in the same terminal region. It handles its own keys: `a` for the detail action, `q`/Esc to return to the list, and ignored keys leave the page unchanged.

This fits the existing architecture in `src/sv/table.py`, avoids a full TUI rewrite, and directly fixes the output placement bug.

## Interaction model

### List mode

- ↑/↓ moves the highlighted row.
- ←/→ pages through rows when available.
- `/` filters rows.
- Enter opens the highlighted row detail page.
- `a` keeps its current list-mode meaning where one already exists. In repo skill browsing, `a` remains add-all.
- `q` or Esc exits the current browser as before.

### Detail mode

- Shows:
  - Skill name
  - Source repo ID
  - Source path
  - Description
  - Add result/status when available
  - Footer help
- `a` adds the current skill.
- After add, detail mode remains open and shows the result/status.
- `q` or Esc returns to list mode.

Footer help should distinguish contexts clearly. For example:

- Skill list footer: `Enter details • q back`
- Repo skill list footer: `Enter details • a add all • q back`
- Detail footer: `a add • q back`

## CLI behavior

### `sv list`

- In TTY mode, Enter opens the detail page.
- Pressing `a` on the detail page adds the highlighted skill to the current project or skill vault.
- Duplicate-name ambiguity uses the same existing add resolution behavior. If the row maps to a concrete `SourceSkill`, detail-page add uses that exact entry.

### `sv search <query>`

- Uses the same detail page behavior as `sv list`.
- Search ranking and filtering remain unchanged.

### `sv repo list`

- The top-level repo browser is unchanged: Enter opens the selected repo's skill browser.
- The repo skill browser uses the new detail page for individual skill metadata.
- List-mode `a` continues to install all skills from that repo and the footer labels it as add-all.
- Detail-mode `a` installs only the skill shown on the detail page.

## Components and boundaries

### `src/sv/table.py`

Add detail-mode support to the interactive browser without changing plain table formatting:

- Track whether the browser is in list mode or detail mode.
- Accept optional detail rendering/action callbacks that return text for the selected row and handle detail actions.
- Render detail content through the same redraw/clear mechanism used by the table.
- Preserve existing behavior when no detail renderer is supplied.

### `src/sv/cli.py`

Replace printing detail callbacks with browser-provided detail content and add actions:

- Build detail text from `SourceSkill` metadata.
- Wire detail-page `a` to the existing add-to-context path.
- Capture add result text so it can be displayed in the detail page instead of printed above the browser.
- Keep non-TTY tables and duplicate sections unchanged.

## Error handling

- If interactive table browsing is unavailable, keep the current fallback to plain output.
- If add fails from detail mode, show the error message in the detail page and keep the browser open.
- If the selected row cannot be mapped back to a `SourceSkill`, show a safe status message and do not install anything.
- Terminal control characters in metadata and status messages remain escaped before rendering.
- Terminal settings must still be restored on exceptions.

## Testing plan

- Unit-test table browser state/rendering for list-to-detail and detail-to-list transitions where practical.
- Add or update CLI TTY contract tests for:
  - `sv list`: Enter opens detail without printing details above the table.
  - `sv search`: Enter opens the same detail flow.
  - Detail-page `a` adds one skill and shows status.
  - `sv repo list`: repo skill list keeps list-mode `a` as add-all, while detail-mode `a` adds only the selected skill.
  - Browser-unavailable fallback still prints plain output.
- Run the existing test suite or targeted pytest files covering TTY browsing and CLI add behavior.

## Documentation updates

Update user-facing docs that describe interactive browsing:

- `README.md`
- `docs/getting-started.md`
- `docs/usage.md`
- `docs/output.md`
- `docs/commands.md`

The docs should say Enter opens a detail page, `a` on the detail page adds the skill, and repo skill list mode keeps `a` for add-all where applicable.

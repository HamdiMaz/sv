# Skill Detail Page Redesign

## Goal

Make the interactive skill detail page feel polished instead of raw while staying safe and readable in a plain terminal.

The approved direction is a **polished card**: a compact framed panel with a colored skill title, labeled metadata rows, a distinct description section, and a concise footer.

## Selected visual direction

```text
╭─ Skill────────────────────────────────────────╮
│ find-docs ● available                       │
│ Source  HamdiMaz/Skills                     │
│ Path    skills/find-docs                    │
├─ Description──────────────────────────────────╯
  Retrieves up-to-date documentation, API
  references, and examples for developer tools.

a add  •  q back
```

Color intent:

- Cyan for the card border/section labels.
- Purple or accent color for the skill name.
- Green for availability/status indicators.
- Muted gray for labels, rules, and footer help.
- White/default foreground for user content after sanitization.

## Scope

In scope:

- Restyle the detail view used by interactive `sv list`, `sv search`, and repo skill browsing.
- Change detail footer help from `a add skill • q back` to `a add • q back`.
- Preserve existing key behavior: `a` adds, `q`/Escape returns to the list.
- Preserve terminal safety: skill names, sources, paths, descriptions, and statuses remain escaped before display.
- Keep output width-aware so the card and description fit narrow terminals.

Out of scope:

- Changing table browsing behavior.
- Adding new actions or changing keyboard shortcuts.
- Rendering markdown from `SKILL.md` bodies.
- Persisting screenshots or browser mockup artifacts.

## Implementation shape

The interactive table already separates list browsing from detail rendering. The redesign should keep that boundary:

1. The CLI continues to map the selected row back to a `SourceSkill`.
2. The source skill detail formatter returns the same core data: name, source, path, description, and optional status.
3. Detail rendering adds the visual frame, colors, wrapping, and footer.

Because the detail renderer currently sanitizes lines before writing them, any color support must be added in a trusted rendering layer rather than by letting skill data inject raw ANSI sequences. The preferred approach is to introduce small internal helpers for styled terminal fragments or styled detail lines where:

- Untrusted text is escaped first.
- ANSI color codes are applied only by sv-owned helpers.
- Display-width calculations ignore sv-owned ANSI sequences.

## States

Default detail:

- Shows the skill name and availability/accent marker in the top row.
- Shows `Source` and `Path` as labeled metadata rows.
- Shows `Description` as its own section with wrapped text.
- Ends with muted footer help: `a add • q back`.

Action status:

- If the add action returns a status, show it below the description as a `Status` row or section.
- Success statuses use the existing green/accent treatment.
- Error statuses remain escaped and readable; do not hide error detail.

Unavailable/stale row:

- Preserve the current behavior of showing only a status message when the selected row no longer maps to a source skill.
- Style it consistently with the card/footer, but do not fabricate missing metadata.

## Testing

Add or update tests for:

- Detail footer help is `a add • q back`.
- Detail output contains the framed card structure and expected labels.
- User-controlled content is still escaped and cannot emit terminal controls.
- Long descriptions and narrow terminals fit within terminal width.
- Status text appears after detail actions and remains escaped.
- Existing interactive behavior still returns from detail to list with `q`/Escape.

## Acceptance criteria

- The detail page visually matches the approved polished-card direction.
- The footer is concise: `a add • q back`.
- No raw control characters from source data can affect terminal rendering.
- Existing list/search/repo browsing flows still work and remain covered by tests.

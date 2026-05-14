# sv usage guide

This guide shows the day-to-day `sv` workflow and explains how to read command output. For deeper detail about table columns, wrapping, and duplicate-name guidance, see [reading sv output](output.md).

## Typical workflow

```bash
# See configured sources and cache locations
sv repo list

# Refresh source caches and show available skills
sv list

# Add one skill to the current project
sv add find-docs

# Add a specific source when multiple repos provide the same skill name
sv add HamdiMaz/Skills:find-docs

# Update installed project skills from their recorded sources
sv sync

# Run Pi with only this project's skills
sv run -- <pi args>
```

Run commands from the project root where `.pi/skills` should be managed.

## Reading `sv list`

If no source repos are configured, `sv list` tells you to add one with `sv repo add <owner/repo>` instead of showing an empty skill table.

`sv list` prints a compact table for unique skill names:

| Column | Meaning |
| --- | --- |
| `Skill` | The folder name copied into `.pi/skills`. |
| `Repo` | The configured source repo ID. |
| `Description` | The description parsed from the skill's `SKILL.md` frontmatter. |

When two repos contain the same skill name, `sv list` keeps both rows visible, adds an `Add as` column, and prints a duplicate-name tip. Use the `Add as` value to install the intended source. Unique rows leave `Add as` blank, so the duplicate-aware table does not repeat `repo:skill` text where it is not needed. If the same repo is accidentally listed twice in `~/.sv/config.toml` (including the same or equivalent GitHub URL under different IDs), `sv` coalesces that repeated source so the same skill does not appear twice.

## Adding skills without surprises

- `sv add <skill>` installs the skill when exactly one configured repo provides that name.
- `sv add <repo>:<skill>` installs from one exact source.
- `sv add -l` opens an interactive picker. The picker labels each row with its source repo ID so duplicate names are easy to distinguish without repeating the skill name twice. Long labels are shortened to your terminal width, and control characters in source metadata are printed as escaped text so the inline picker does not jump or clear the screen.
- `sv add --all` installs every valid skill only when there are no duplicate skill names across configured repos.

`sv` never overwrites an existing project skill during add. If the skill folder already exists, it reports that the skill is already present and leaves local files unchanged. When origin metadata is available, the message also tells you whether the existing skill came from the same repo or a different repo than the one you requested.

## Duplicate skill names

Duplicate skill names are allowed in source repos, but a Pi project can only have one folder for a given skill name. To avoid accidental overwrites:

1. Use `sv list` to find the `Add as` value.
2. Install one source explicitly with `sv add <repo>:<skill>`.
3. Avoid selecting two sources for the same skill name in `sv add -l`; `sv` rejects that selection before copying anything.

## Updating project skills

- `sv sync` pulls configured source repos and refreshes installed project skills from their recorded origins.
- `sv update` does the same work but prints explicit progress messages before pulling and syncing.

Synced skill folders are replaced with the source version. Local edits inside managed skill folders are overwritten. Local-only or ambiguous legacy skills are skipped with a clear message.

## Output and table behavior

Tables are plain text so they work in terminals, logs, and CI. Long descriptions, URLs, and cache paths wrap within the current terminal width instead of pushing important columns off screen. Very narrow terminals use tighter column spacing and last-resort hard wrapping when necessary. Duplicate-name guidance wraps as well, so narrow terminals and CI logs stay readable. Terminal control characters from source metadata are escaped before display. Interactive picker rows are single-line and terminal-width aware so arrow-key navigation remains stable.

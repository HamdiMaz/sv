# sv usage guide

This guide shows the day-to-day `sv` workflow and explains how to read command output. For deeper detail about table columns, wrapping, and duplicate-name guidance, see [reading sv output](output.md).

## Typical workflow

```bash
# See configured sources and cache locations
sv repo list

# Show available skills, refreshing lazily from the global cache
sv list

# Force a metadata refresh when listing source skills
sv list --refresh

# Search using cached metadata without network or Git refreshes
sv search docs --cached

# Inspect global cache usage and prune cached skill bodies
sv cache status
sv cache clean

# Add one skill to the current project
sv add find-docs

# Add a specific source when multiple repos or paths provide the same skill name
sv add HamdiMaz/Skills:find-docs
sv add Team/Skills:packages/agents/pi/skills/find-docs

# Safely update unchanged project skills from their recorded sources
sv update

# Force-sync installed project skills from their recorded sources
sv sync

# Run Pi with only this project's skills
sv run -- <pi args>
```

Run commands from the project root where `.pi/skills` should be managed.

## Global cache behavior

`sv` keeps source metadata in the global cache for 24 hours for normal browsing and install commands. `sv list`, `sv search`, and all `sv add` modes use fresh cached metadata, refresh lazily when it expires, and warn while falling back to stale metadata if a refresh fails. `sv sync`, `sv update`, and source-aware `sv status` are refresh-first by default so they report and apply current source changes.

Use `--cached` to avoid network and Git refreshes. Cached mode requires existing metadata, and commands that copy or update skills also require a matching cached skill body before materializing a folder. Normal mode refreshes the source first when metadata exists but the matching skill body is missing, so stale metadata is not paired with newer source content. Cached skill bodies are pruned lazily after cache writes when unused for more than 30 days or when the body cache exceeds 256 MiB. See [global cache](commands.md#global-cache) for details.

`sv` parallelizes independent work by default, so multiple configured source repos and bulk skill operations should complete faster than a purely sequential run. The command output is still printed in stable order. For debugging, run `SV_JOBS=1 sv <command>` to force sequential execution.

## Reading `sv list`

If no source repos are configured, `sv list` tells you to add one with `sv repo add <owner/repo>` instead of showing an empty skill table.

In an interactive terminal, `sv list` opens a read-only browser. Use ↑/↓ to move, `/` to open a visible ranked search prompt, Enter to view skill details, and `q` to quit. The detail view shows focused skill metadata such as source, path, and description.

In non-interactive output, or when the browser is unavailable, `sv list` prints a compact table with one row per skill name:

| Column | Meaning |
| --- | --- |
| `Skill` | The folder name copied into `.pi/skills`. |
| `Source` | The configured source repo ID, or `N sources` when multiple repos provide that skill name. |
| `Description` | The description parsed from `SKILL.md`, or a prompt to choose from the duplicate section. |

When two repos contain the same skill name, non-interactive `sv list` output groups the name into one `N sources` row in the main table, then prints a separate `Duplicate skill names` section with each repo, description, and exact `Add as` value. Each duplicate group shows the skill name once and leaves continuation rows blank in that column, so the section is easy to scan without hiding any source choices. Use the `Add as` value to install the intended source in scripts or CI. Keeping duplicate rows and qualified references out of the main table makes the list easier to scan while preserving every source choice below it. If the same repo is accidentally listed twice in `~/.sv/config.toml` (including the same or equivalent GitHub URL under different IDs), `sv` coalesces that repeated source so the same skill does not appear twice.

## Adding skills without surprises

- `sv add <skill>` installs the skill when exactly one configured repo provides that name. When multiple repos match in an interactive terminal, it shows a compact source-choice table with repo, description, and `Add as` columns before asking you to pick one.
- `sv add <repo>:<skill>` installs from one exact source in the normal `skills/<name>` layout. Path-aware `Add as` values such as `sv add <repo>:<path/to/skill>` install indexed or non-default source paths.
- `sv add -l` opens an interactive picker. The picker labels each row with its source repo ID so duplicate names are easy to distinguish without repeating the skill name twice. Use `/` to open a visible ranked search prompt; source skill lists use the same source-aware ordering as `sv search`. A help line keeps the controls visible while you move through the list. Long labels and the help line are shortened to your terminal width, and control characters in source metadata are printed as escaped text so the inline picker does not jump or clear the screen.
- `sv add --all` installs every valid skill only when there are no duplicate skill names across configured repos.

`sv` never overwrites an existing project skill during add. If the skill folder already exists, it reports that the skill is already present and leaves local files unchanged. When origin metadata is available, the message also tells you whether the existing skill came from the same repo or a different repo than the one you requested. In a skill-vault repository, `sv add --replace <skill>` can intentionally replace an existing vault skill; non-interactive vault replacement requires `--replace`.

## Duplicate skill names

Duplicate skill names are allowed in source repos, but a Pi project can only have one folder for a given skill name. To avoid accidental overwrites:

1. Use `sv list` to find the `Add as` value in the `Duplicate skill names` section.
2. Install one source explicitly with `sv add <repo>:<skill>` or the path-aware `Add as` value from the table.
3. Avoid selecting two sources for the same skill name in `sv add -l`; `sv` rejects that selection before copying anything.

## Updating project skills

- `sv update` pulls configured source repos and refreshes only managed project skills whose local folders still match their recorded baseline. It preserves local edits by skipping modified skills and marking them as modified with an update available.
- `sv sync` force-syncs managed project skills from their recorded origins.

Force-synced skill folders are replaced with the source version, so local edits inside managed skill folders are overwritten by `sv sync`. Local-only or ambiguous legacy skills are skipped with a clear message.

## Output and table behavior

Tables are plain text so they work in terminals, logs, and CI. Long descriptions, URLs, and cache paths wrap within the current terminal width instead of pushing important columns off screen. Repo-like values prefer clean wrap points at `/` and `:` before hard wrapping, which keeps `owner/repo` IDs and `repo:skill` or `repo:path/to/skill` references easier to scan. Very narrow terminals use tighter column spacing and last-resort hard wrapping when necessary. Duplicate-name guidance wraps as well, so narrow terminals and CI logs stay readable. Terminal control characters from source metadata are escaped before display. Interactive picker rows are single-line and terminal-width aware so arrow-key navigation remains stable.

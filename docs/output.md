# Reading sv output

`sv` prints plain-text output so commands are readable in terminals, logs, and CI.

## Skill lists

In an interactive terminal, `sv list` opens a read-only browser so you can move through source skills before choosing anything. In non-interactive output, or when the browser is unavailable, `sv list` shows one row per skill name so the main table stays easy to scan:

| Column | Meaning |
| --- | --- |
| `Skill` | Folder name that will be created under `.pi/skills`. |
| `Source` | Source repo ID, or `N sources` when duplicate names need a source choice. |
| `Description` | Description from `SKILL.md`, or a short prompt to use the duplicate section. |

If two repos provide the same skill name, the main table groups that name and `sv list` prints a separate duplicate section with the exact choices:

```text
Skill      Source           Description
---------  ---------------  ----------------------
find-docs  2 sources        Choose a source below.
review     Team/Skills      Reviews code changes.

Duplicate skill names:
Skill      Repo             Description           Add as
---------  ---------------  --------------------  ----------------------------
find-docs  HamdiMaz/Skills  Retrieves docs.       HamdiMaz/Skills:find-docs
           Team/Skills      Team-specific docs.   Team/Skills:find-docs
```

The duplicate section shows the skill name once, then leaves that cell blank for the remaining sources in the same group. Copy the `Add as` value for the source you want. Most values use `repo:skill`; indexed or non-default source paths can use `repo:path/to/skill`:

```bash
sv add HamdiMaz/Skills:find-docs
sv add Team/Skills:packages/agents/pi/skills/find-docs
```

If your config accidentally repeats the same repo entry, the same repo URL, or an equivalent GitHub URL under two IDs, `sv` coalesces the duplicate while reading the config so the same source skill is not listed twice.

## Tables and narrow terminals

Tables wrap long values to fit the current terminal width. This keeps important columns visible when repo IDs, cache paths, or descriptions are long. Wrapped cell lines are indented under their original column. Repo-like values such as `owner/repo`, `repo:skill`, and `repo:path/to/skill` prefer clean wrap points at `/` and `:` so duplicate-source references stay easier to read and copy. In very narrow terminals, sv tightens column spacing and hard-wraps only as a last resort so table lines stay within the available width. Wide Unicode characters are measured by display width, so CJK characters and emoji do not unexpectedly overflow the table.

Example shape:

```text
Skill      Source       Description
---------  -----------  ------------------------
find-docs  Team/Skills  Retrieves documentation
                         and API examples.
```

Terminal control characters from repo metadata or skill descriptions are escaped before printing, so source content cannot clear your screen or hide output.

`sv status` keeps global source status readable by abbreviating validated index and catalog hashes to the first 12 digest characters plus `...` in the table. The full canonical `sha256:<64 lowercase hex characters>` values remain in `~/.sv/manifest.toml` for auditing or debugging. Project and skill-vault status rows can include `missing` when a manifest still records a managed skill but the local target folder has already been deleted manually, or `invalid target` when that path exists but is no longer a directory.

## Interactive table browser

`sv list`, `sv search`, and TTY `sv repo list` output use the same inline table browser when stdin and stdout are interactive. Use ↑/↓ to move, ←/→ to page when available, `/` to open a visible ranked search prompt, Enter to open details or the highlighted action, and `q` or Esc to go back. `sv list` and `sv search` details show focused skill metadata such as source, path, and description; copy-paste `Add as` references stay in non-interactive duplicate sections where they are easier to use from scripts.

## Interactive picker output

`sv add -l` uses an inline picker instead of a full-screen interface. Each row is kept to one terminal line so arrow-key navigation remains predictable. A muted help line shows the visible range and controls: ↑/↓ move, ←/→ page, `/` search, Space select, Enter confirm, and `q` cancel. Pressing `/` opens a visible `Search:` prompt and applies ranked results after Enter. Long skill labels and the help line are truncated to the current terminal width, and control characters from source names or descriptions are escaped before display.

## Duplicate-name guidance

When duplicate skill names exist across repos, `sv list` prints a wrapped tip below the duplicate section. In non-interactive shells, use the qualified `Add as` value. In an interactive terminal, `sv add <skill>` shows a source-choice table with repo, description, and `Add as` columns, then asks you to choose one.

`sv add --all` is intentionally strict: it stops before copying anything if duplicate skill names exist. Add those skills explicitly with the exact `Add as` value so one source cannot overwrite another by accident.

If a project already has that skill from a different source, `sv add <repo>:<skill>` or `sv add <repo>:<path/to/skill>` does not replace it. The command reports the current origin, the requested origin, and tells you to run `sv remove <skill>` first if you really want to switch sources. Skill-vault replacement is separate and requires `sv add --replace` when non-interactive.

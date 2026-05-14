# Reading sv output

`sv` prints plain-text output so commands are readable in terminals, logs, and CI.

## Skill lists

`sv list` shows one row for each available source skill. For unique skill names, the table stays compact:

| Column | Meaning |
| --- | --- |
| `Skill` | Folder name that will be created under `.pi/skills`. |
| `Repo` | Source repo ID. Use this with `sv repo remove` and qualified skill references. |
| `Description` | Description from the skill's `SKILL.md` frontmatter. |

If two repos provide the same skill name, both rows stay visible and `sv list` adds an `Add as` column. Only duplicate-name rows need a qualified value, so unique rows leave that column blank to keep the list from repeating unnecessary `repo:skill` text. Copy the `Add as` value for the source you want:

```bash
sv add HamdiMaz/Skills:find-docs
```

If your config accidentally repeats the same repo entry, the same repo URL, or an equivalent GitHub URL under two IDs, `sv` coalesces the duplicate while reading the config so the same source skill is not listed twice.

## Tables and narrow terminals

Tables wrap long values to fit the current terminal width. This keeps important columns visible when repo IDs, cache paths, or descriptions are long. Wrapped cell lines are indented under their original column. Repo-like values such as `owner/repo` and `repo:skill` prefer clean wrap points at `/` and `:` so duplicate-source references stay easier to read and copy. In very narrow terminals, sv tightens column spacing and hard-wraps only as a last resort so table lines stay within the available width. Wide Unicode characters are measured by display width, so CJK characters and emoji do not unexpectedly overflow the table.

Example shape:

```text
Skill      Repo         Description
---------  -----------  ------------------------
find-docs  Team/Skills  Retrieves documentation
                         and API examples.
```

Terminal control characters from repo metadata or skill descriptions are escaped before printing, so source content cannot clear your screen or hide output.

## Interactive picker output

`sv add -l` uses an inline picker instead of a full-screen interface. Each row is kept to one terminal line so arrow-key navigation remains predictable. A muted help line shows the visible range and controls: ↑/↓ move, ←/→ page, Space select, Enter confirm, and `q` cancel. Long skill labels and the help line are truncated to the current terminal width, and control characters from source names or descriptions are escaped before display.

## Duplicate-name guidance

When duplicate skill names exist across repos, `sv list` prints a wrapped tip below the table. In non-interactive shells, use the qualified `Add as` value. In an interactive terminal, `sv add <skill>` shows the matching sources and asks you to choose one.

`sv add --all` is intentionally strict: it stops before copying anything if duplicate skill names exist. Add those skills explicitly with `repo:skill` so one source cannot overwrite another by accident.

If a project already has that skill from a different source, `sv add <repo>:<skill>` does not replace it. The command reports the current origin, the requested origin, and tells you to run `sv remove <skill>` first if you really want to switch sources.

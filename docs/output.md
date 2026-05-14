# Reading sv output

`sv` prints plain-text output so commands are readable in terminals, logs, and CI.

## Skill lists

`sv list` shows one row for each available source skill. For unique skill names, the table stays compact:

| Column | Meaning |
| --- | --- |
| `Skill` | Folder name that will be created under `.pi/skills`. |
| `Repo` | Source repo ID. Use this with `sv repo remove` and qualified skill references. |
| `Description` | Description from the skill's `SKILL.md` frontmatter. |

If two repos provide the same skill name, both rows stay visible and `sv list` adds an `Add as` column with the exact `repo:skill` reference accepted by `sv add`. Copy the `Add as` value for the source you want:

```bash
sv add HamdiMaz/Skills:find-docs
```

If your config accidentally repeats the same repo entry, `sv` coalesces the duplicate while reading the config so the same source skill is not listed twice.

## Tables and narrow terminals

Tables wrap long values to fit the current terminal width. This keeps important columns visible when repo IDs, cache paths, or descriptions are long. Wrapped lines are indented under their original column. In very narrow terminals, sv tightens column spacing and hard-wraps only as a last resort so table lines stay within the available width.

Example shape:

```text
Skill      Repo         Description
---------  -----------  ------------------------
find-docs  Team/Skills  Retrieves documentation
                         and API examples.
```

Terminal control characters from repo metadata or skill descriptions are escaped before printing, so source content cannot clear your screen or hide output.

## Duplicate-name guidance

When duplicate skill names exist across repos, `sv list` prints a wrapped tip below the table. In non-interactive shells, use the qualified `Add as` value. In an interactive terminal, `sv add <skill>` shows the matching sources and asks you to choose one.

`sv add --all` is intentionally strict: it stops before copying anything if duplicate skill names exist. Add those skills explicitly with `repo:skill` so one source cannot overwrite another by accident.

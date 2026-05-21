# Reading sv output

`sv` prints plain-text output so commands are readable in terminals, logs, and CI.

## Skill lists

In an interactive terminal, `sv list` opens an interactive browser so you can move through source skills before choosing anything. In non-interactive output, or when the browser is unavailable, `sv list` shows one row per skill name so the main table stays easy to scan:

| Column | Meaning |
| --- | --- |
| `Skill` | Folder name that will be created under the default agent skill folder. |
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

Tables wrap long values to fit the current terminal width. This keeps important columns visible when repo IDs, cache paths, or descriptions are long. Wrapped cell lines are indented under their original column. Repo-like values such as `owner/repo`, `repo:skill`, and `repo:path/to/skill` prefer clean wrap points at `/` and `:` so duplicate-source references stay easier to read and copy. In very narrow terminals, sv tightens column spacing and hard-wraps only as a last resort so table lines stay within the available width. Wide Unicode characters are measured by display width, so CJK characters and emoji do not unexpectedly overflow the table. Non-interactive output stays plain text without ANSI styling and keeps the script-friendly table shape shown above.

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

Interactive TTY browsers render a compact table with a muted rule above and below the header row. Header labels use the interactive accent color, the current row keeps the blue highlight, and the footer lists the active keys. Browse-mode Enter opens details or runs the highlighted row action; `q` or Esc goes back.

When sv needs a project agent and cannot infer one, TTY output opens a highlighted chooser with `Pi`, `Claude`, and `Agents`. Use arrow keys to move, `/` to search, Enter to choose, and `q`/Esc to cancel.

`sv list`, `sv search`, and TTY `sv repo list` use this browser when stdin and stdout are interactive. In `sv list` and `sv search`, use ↑/↓ to move and ←/→ to page when available. Press `/` to open the framed search prompt with ranked results; Enter applies the typed search, empty Enter clears the active search, and Esc cancels without changing the current results. Enter opens an inline detail page; detail `a` adds the shown skill and shows the add result/status, and detail `q`/Esc returns to the list. List `q`/Esc exits the browser. Detail pages show a framed, color-accented card with the skill name, source, path, wrapped description, optional add status, and the concise footer `a add • q back`; copy-paste `Add as` references stay in non-interactive duplicate sections where they are easier to use from scripts. Long values fit the current terminal width, and control characters from source names, paths, descriptions, or statuses are escaped before display.

In TTY `sv repo list`, Enter on a repo opens that repo's skill browser. In the repo skill browser, list-mode `a` still adds all non-conflicting skills from that repo. Enter opens a detail page, where detail-mode `a` adds only the shown skill, shows the add result/status, and detail `q`/Esc returns to the repo skill list. List `q`/Esc backtracks from the repo skill list to the repo list, then exits from the top-level repo list.

## Interactive picker output

Interactive TTY pickers use the same table shell as browsers. Multi-select pickers add a first `Sel` column whose cells are `[ ]` and `[x]`. Space toggles the highlighted row, Enter confirms the selected rows for the command action, `/` searches, and `q` cancels.

`sv add -l`, `sv remove -l`, and duplicate-source choices use inline pickers instead of a full-screen interface. Each row is kept to one terminal line so arrow-key navigation remains predictable. Command-specific footers list the available controls, including ↑/↓ movement, ←/→ paging, and `/` search when available. Long skill labels and footer text are truncated to the current terminal width, and control characters from source names or descriptions are escaped before display.

## Duplicate-name guidance

When duplicate skill names exist across repos, `sv list` prints a wrapped tip below the duplicate section. In non-interactive shells, use the qualified `Add as` value. In an interactive terminal, `sv add <skill>` opens a framed source chooser with `Skill`, `Source`, and `Description` columns, then asks you to choose one.

`sv add --all` is intentionally strict: it stops before copying anything if duplicate skill names exist. Add those skills explicitly with the exact `Add as` value so one source cannot overwrite another by accident.

If a project already has that skill from a different source, `sv add <repo>:<skill>` or `sv add <repo>:<path/to/skill>` does not replace it. The command reports the current origin, the requested origin, and tells you to run `sv remove <skill>` first if you really want to switch sources. Skill-vault replacement is separate and requires `sv add --replace` when non-interactive.

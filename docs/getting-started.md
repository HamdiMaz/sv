# Getting started with sv

This is the shortest path from a new install to a project-local agent skill set.

## 1. Add a skill source

If you have not configured any source repos yet, add one:

```bash
sv repo add HamdiMaz/Skills
# or your team's source repo
sv repo add SomeOrg/TeamSkills
```

Check what `sv` will use:

```bash
sv repo list
```

## 2. Browse available skills

```bash
sv list
```

In an interactive terminal, `sv list` opens an interactive browser with `Skill`, `Source`, and `Description` columns. Use ↑/↓ to move, Enter to open an inline detail page, detail `a` to add the shown skill and show the add result/status, and detail `q` or Esc to return to the list. Press `/` to open the framed search prompt. Enter applies the typed search, empty Enter clears the active search, and Esc cancels without changing the current results. List `q` or Esc exits the browser. In non-interactive output, or if the browser is unavailable, `sv list` prints the same columns as a table. If two repos or source paths provide the same skill name, the table shows one `N sources` row and adds a separate `Duplicate skill names` section with each repo, description, and `Add as` value. Duplicate groups show the skill name once, then list each source choice beneath it. Copy the exact `Add as` value when you need a specific source:

```bash
sv add HamdiMaz/Skills:find-docs
sv add Team/Skills:packages/agents/pi/skills/find-docs
```

## 3. Add skills to your project

Run from the project root. Project skills can be installed under one supported project agent folder:
`.pi/skills`, `.claude/skills`, or `.agents/skills`.

Use `sv default` to show the current project default agent. Use
`sv default pi`, `sv default claude`, or `sv default agents` to set it.
The selected value is stored in `.sv/manifest.toml` as `default_agent`.
Changing it affects future project commands only; sv does not migrate existing
skill folders between agents.

Set an explicit default agent before the first non-interactive add, then install into that default agent skill folder:

```bash
sv default pi
sv add find-docs
```

To pick several skills interactively:

```bash
sv add -l
```

The picker shows the visible range and controls at the bottom. Use ↑/↓ to move, Space to select, Enter to confirm, and `q` to cancel. Press `/` to open the framed search prompt. Enter applies the typed search, empty Enter clears the active search, and Esc cancels without changing the current results.

## 4. Keep skills updated

```bash
sv update
```

`sv update` refreshes configured source repos and updates installed skills from their recorded sources when the local folders are unchanged. It preserves local edits by skipping modified managed skills and marking them as having an update available. Use `sv sync` only when you intentionally want to force managed skill folders back to the source copy and overwrite local edits.

## 5. Run Pi with project skills only

```bash
sv run pi <pi args>
```

`sv run` still requires an explicit supported run agent. The supported run agent is `pi`, and it uses `.pi/skills` regardless of the project default agent.

This validates `.pi/skills` for symlinks and bounded skill trees, then launches Pi as:

```bash
pi --no-skills --skill .pi/skills <pi args>
```

## Common next steps

| Goal | Command |
| --- | --- |
| See source repos | `sv repo list` |
| Add another source | `sv repo add <owner/repo>` |
| Remove a source from config | `sv repo remove <repo-id>` |
| Remove a project skill | `sv remove find-docs` |
| Select project skills to remove | `sv remove -l` |
| Safely refresh unchanged managed skills | `sv update` |
| Force-sync and overwrite managed skills | `sv sync` |

For more detail, read the [examples](examples.md), [usage guide](usage.md), [command reference](commands.md), and [output guide](output.md).

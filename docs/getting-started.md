# Getting started with sv

This is the shortest path from a new install to a project-local Pi skill set.

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

The main list shows `Skill`, `Source`, and `Description`. If two repos provide the same skill name, `sv` shows one `N sources` row in the main list and adds a separate `Duplicate skill names` section with each repo, description, and `Add as` value. Duplicate groups show the skill name once, then list each source choice beneath it. Copy the exact `Add as` value when you need a specific source:

```bash
sv add HamdiMaz/Skills:find-docs
```

## 3. Add skills to your project

Run from the project root:

```bash
sv add find-docs
```

To pick several skills interactively:

```bash
sv add -l
```

The picker shows the visible range and controls at the bottom. Use ↑/↓ to move, Space to select, Enter to confirm, and `q` to cancel.

## 4. Keep skills updated

```bash
sv update
```

`sv update` refreshes configured source repos and updates installed skills from their recorded sources when the local folders are unchanged. It preserves local edits by skipping modified managed skills and marking them as having an update available. Use `sv sync` only when you intentionally want to force managed skill folders back to the source copy and overwrite local edits.

## 5. Run Pi with project skills only

```bash
sv run -- <pi args>
```

This launches Pi as:

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

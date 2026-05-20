# sv examples

Copy these patterns when you are setting up or troubleshooting a project.

## Add a source and browse skills

```bash
sv repo add HamdiMaz/Skills
sv repo list
sv list
```

In an interactive terminal, `sv list` opens the interactive skill browser. In the list, use ↑/↓ to move, `/` to filter, Enter to open a skill detail page, and `q`/Esc to exit. On the detail page, press `a` to add the shown skill and display the add status/result, or press `q`/Esc to return to the list. In scripts, logs, or other non-interactive output, it prints the main skill table as `Skill`, `Source`, and `Description`. If a name appears in more than one source, the table shows one `N sources` row and a separate `Duplicate skill names` section shows each repo, description, and exact `Add as` value to copy. Duplicate groups show the skill name once so repeated source choices are easier to scan.

## Add one skill

```bash
sv add find-docs
```

Use the plain skill name when only one configured source provides it.

## Add a duplicate-name skill from one source

```bash
sv list
sv add HamdiMaz/Skills:find-docs
sv add Team/Skills:packages/agents/pi/skills/find-docs
```

Copy the `Add as` value from the duplicate section. The qualified form is the safest choice in scripts and CI; it can be `repo:skill` or path-aware `repo:path/to/skill`.

## Pick several skills interactively

```bash
sv add -l
```

Use ↑/↓ to move, Space to select, Enter to add, and `q` to cancel. The picker labels each row with the source repo ID so duplicate names remain clear.

## Update installed skills

```bash
sv update
# or, when you intentionally want to overwrite local edits:
sv sync
```

`sv update` refreshes configured source repos and updates only unchanged managed skills from their recorded origins. It preserves local edits by skipping modified skills and marking them as having an update available. Use `sv sync` when you intentionally want to force local managed skills back to the source copy.

## Run Pi with only project skills

```bash
sv run -- <pi args>
```

This validates `.pi/skills` for symlinks and bounded skill trees, then launches `pi --no-skills --skill .pi/skills ...` from the current project.

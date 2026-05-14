# sv examples

Copy these patterns when you are setting up or troubleshooting a project.

## Add a source and browse skills

```bash
sv repo add HamdiMaz/Skills
sv repo list
sv list
```

`sv list` prints the main skill table as `Skill`, `Repo`, and `Description`. If a name appears in more than one source, a separate `Duplicate skill names` section shows the exact `Add as` values to copy.

## Add one skill

```bash
sv add find-docs
```

Use the plain skill name when only one configured source provides it.

## Add a duplicate-name skill from one source

```bash
sv list
sv add HamdiMaz/Skills:find-docs
```

Copy the `Add as` value from the duplicate section. The qualified form is the safest choice in scripts and CI.

## Pick several skills interactively

```bash
sv add -l
```

Use ↑/↓ to move, Space to select, Enter to add, and `q` to cancel. The picker labels each row with the source repo ID so duplicate names remain clear.

## Update installed skills

```bash
sv sync
# or, when you want explicit progress messages:
sv update
```

Both commands refresh configured source repos before syncing managed project skills from their recorded origins.

## Run Pi with only project skills

```bash
sv run -- <pi args>
```

This launches `pi --no-skills --skill .pi/skills ...` from the current project.

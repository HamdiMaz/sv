# sv

`sv` manages project-local AI agent skills. The MVP supports Pi and copies skills from a Git-backed skill source into the current project's `.pi/skills` directory.

Default skill source:

```text
https://github.com/HamdiMaz/Skills.git
```

Global sv files live under:

```text
~/.sv/
```

Project Pi skills live under:

```text
.pi/skills/
```

## Commands

List skills available in the configured source repo:

```bash
sv list
```

Add a skill to the current project:

```bash
sv add github-release
```

Choose one or more skills from an interactive list:

```bash
sv add -l
```

Use ↑/↓ to move, ←/→ to page through skills, Space to select, Enter to add,
and `q` to cancel. The picker renders inline, shows 5 skills at a time, and
scrolls as you move.

Add every skill from the configured source repo:

```bash
sv add all
sv add --all
```

If a skill already exists in `.pi/skills`, sv prints a friendly message and leaves it unchanged.

Remove a skill from the current project:

```bash
sv remove github-release
```

Choose one or more project skills from an interactive list and remove them:

```bash
sv remove -l
```

Update project skills whose names exist in the source repo:

```bash
sv sync
```

Run Pi with global skill discovery disabled and only project skills enabled:

```bash
sv run -- <pi args>
```

Set a different global skill source:

```bash
sv config repo HamdiMaz/Skills
sv config repo https://github.com/HamdiMaz/Skills.git
sv config repo git@github.com:HamdiMaz/Skills.git
```

Show effective configuration:

```bash
sv config show
```

## Source repo layout

sv expects skills to be immediate folders under `skills/`:

```text
skills/
  github-release/
    ...
  find-docs/
    ...
```

sv does not validate skill contents in the MVP. It copies the folder as-is.

## Pi isolation

`sv run` launches Pi like this:

```bash
pi --no-skills --skill .pi/skills
```

This is the MVP mechanism for using only project skills. Start Pi through `sv run` when you want this isolation.

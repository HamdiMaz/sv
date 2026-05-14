# sv

`sv` manages project-local AI agent skills for Pi. It copies valid skills from one or more Git-backed source repositories into the current project's `.pi/skills` directory and records where installed skills came from so they can be synced reliably.

## Installation

`sv` is a Python CLI package and requires Python 3.14 or newer. Install it with your preferred Python package tool, then run it from the root of the project whose Pi skills you want to manage.

```bash
uv tool install sv
# or, from a checkout:
uv tool install .
```

`sv` shells out to `git` to clone and update skill source repositories, so `git` must be available on your `PATH`.

## Where sv stores files

Default skill source when no repo config exists:

```text
https://github.com/HamdiMaz/Skills.git
```

Global sv files live under:

```text
~/.sv/
```

Cached source repositories live under:

```text
~/.sv/sources/
```

Project Pi skills live under:

```text
.pi/skills/
```

## Quick start

```bash
sv list
sv add find-docs
sv sync
sv run -- <pi args>
```

## Commands

Manage source repos:

```bash
sv repo add HamdiMaz/Skills
sv repo add SomeOrg/TeamSkills
sv repo list
sv repo remove SomeOrg/TeamSkills
```

List valid skills available in configured source repos:

```bash
sv list
```

`sv list` shows the skill name, source repo, and parsed description from `SKILL.md`.

Add a skill to the current project:

```bash
sv add github-release
```

If multiple repos provide the same skill name, `sv` shows matching repos and lets you choose. You can skip the prompt with a qualified name:

```bash
sv add HamdiMaz/Skills:github-release
```

Choose one or more skills from an interactive repo-aware list:

```bash
sv add -l
```

Use ↑/↓ to move, ←/→ to page through skills, Space to select, Enter to add,
and `q` to cancel. The picker renders inline, shows 5 skills at a time, and
scrolls as you move. When a source repo is already cached, `sv add -l` reads the
cache without pulling first so the picker opens quickly; run `sv list` or
`sv update` when you want to refresh the cache before choosing skills.

Add every valid skill from every configured source repo:

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

Update project skills from their recorded source repos:

```bash
sv sync
```

Update source repo caches and then sync project skills:

```bash
sv update
```

Run Pi with global skill discovery disabled and only project skills enabled:

```bash
sv run -- <pi args>
```

## Source repo layout

sv expects skills to be immediate folders under `skills/` and requires each skill folder to contain `SKILL.md` with frontmatter:

```text
skills/
  github-release/
    SKILL.md
    ...
  find-docs/
    SKILL.md
    ...
```

```md
---
name: github-release
description: Use when creating or publishing GitHub releases.
---
```

The frontmatter `name` must match the folder name, and `description` must be non-empty. Invalid skill folders are skipped by `sv list`, `sv add`, `sv add all`, `sv sync`, and `sv update`.

## Project manifest

When `sv` installs or syncs a skill, it records the source repo in `.pi/skills/.sv-manifest.toml`. This lets `sv sync` and `sv update` refresh skills from the repo they came from, even when multiple repos contain the same skill name.

Existing project skills without manifest entries are backfilled during sync when exactly one configured repo provides a valid skill with that name. Ambiguous or local-only skills are skipped with a clear message.

## Pi isolation

`sv run` launches Pi like this:

```bash
pi --no-skills --skill .pi/skills
```

Start Pi through `sv run` when you want to use only project-local skills.

## Troubleshooting

- **`Git is required but was not found on PATH.`** Install Git and make sure the `git` executable is available in your shell.
- **`Source path ... exists but is not a Git clone.`** Remove the reported cache directory and rerun the command.
- **`Configured source repo is ..., but existing source clone uses ...`.** The configured repo ID points at a cache cloned from a different remote. Remove the reported cache directory or update your repo config.
- **Interactive selection requires a TTY.** Run `sv add -l` or `sv remove -l` in an interactive terminal, or use non-interactive commands such as `sv add <skill>` and `sv remove <skill>`.

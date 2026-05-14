# sv

`sv` manages project-local AI agent skills for Pi. It copies valid skills from one or more Git-backed source repositories into the current project's `.pi/skills` directory and records where installed skills came from so they can be synced reliably.

Start with [getting started](docs/getting-started.md) for the shortest setup path. For copy-paste workflows, see [examples](docs/examples.md). For a command-focused walkthrough, see the [usage guide](docs/usage.md). For a compact command reference, see [commands](docs/commands.md). For table columns, picker behavior, wrapping, and duplicate-name guidance, see [reading sv output](docs/output.md). For common fixes, see [troubleshooting](docs/troubleshooting.md).

## Installation

`sv` is a Python CLI package and requires Python 3.14 or newer. Install it with your preferred Python package tool, then run it from the root of the project whose Pi skills you want to manage.

```bash
# After publication:
uv tool install sv

# From the Git repository:
uv tool install git+https://github.com/HamdiMaz/sv.git

# From a checkout:
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

## Source repo configuration

`sv` reads global repo configuration from `~/.sv/config.toml`. If that file is missing, `sv` uses the default `HamdiMaz/Skills` repo. After you run `sv repo add`, only repos recorded in the config are used. `sv repo add` accepts GitHub shorthand such as `owner/repo`, GitHub HTTPS/SSH URLs, and local Git repository paths. Existing bare relative paths, and paths starting with `./`, `../`, `/`, or `~`, are resolved to absolute paths before they are saved, so global configuration keeps working no matter which project directory you run `sv` from later. If a local path looks like GitHub shorthand, use an explicit path prefix such as `./owner/repo` or `../repo`. Use `sv repo list` to see the derived repo ID used by qualified skill references and `sv repo remove`.

To use the default repo plus a team repo, add both explicitly:

```bash
sv repo add HamdiMaz/Skills
sv repo add SomeOrg/TeamSkills
```

Removing the last repo writes `repos = []`. The default repo is used again only if `~/.sv/config.toml` is removed or if you add `HamdiMaz/Skills` explicitly.

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

`sv repo remove` updates global configuration only. It does not delete cached source clones or remove project skills that were already installed from that repo.

List valid skills available in configured source repos:

```bash
sv list
```

`sv list` shows the skill name, source, and parsed description from `SKILL.md`. The main list always stays compact with `Skill`, `Source`, and `Description` columns. When duplicate skill names exist across repos, the main list groups that name into one `N sources` row and prints a separate `Duplicate skill names` section with each repo, description, and exact `Add as` value to copy. Tables wrap to the current terminal width so long repo IDs, cache paths, and descriptions stay readable; repo-like values prefer clean wrap points at `/` and `:` before falling back to hard wrapping. If a config accidentally repeats the same repo entry, the same repo URL, or an equivalent GitHub URL under different IDs, `sv` coalesces it while reading the config so the same source skill is not listed twice. See [reading sv output](docs/output.md) for examples.

Add a skill to the current project:

```bash
sv add github-release
```

If multiple repos provide the same skill name, `sv` shows matching repos with their qualified `Add as` values and lets you choose when stdin and stdout are interactive TTYs. In non-interactive use, provide a qualified name:

```bash
sv add HamdiMaz/Skills:github-release
```

If that skill already exists from another source, `sv` leaves the local skill unchanged and prints both the current origin and the requested origin. Run `sv remove <skill>` first when you intentionally want to switch sources.

Choose one or more skills from an interactive repo-aware list:

```bash
sv add -l
```

Use ↑/↓ to move, ←/→ to page through skills, Space to select, Enter to add,
and `q` to cancel. The picker renders inline, shows 5 skills at a time, displays a compact help/status line with the visible range and controls, and
scrolls as you move. Each picker row includes the source repo ID so duplicate skill names are easy to distinguish without repeating the skill name twice. Long picker labels and the help line are truncated to the terminal width, and control characters from source metadata are escaped before display so the inline UI stays stable. If you select two sources for the same skill name, `sv` stops before copying anything and asks you to choose only one source. When a source repo is already cached, `sv add -l` reads the
cache without pulling first so the picker opens quickly. Run `sv list` when you
want to refresh source caches without changing project skills; use `sv update`
only when you also want to sync installed project skills.

Add every valid, non-conflicting skill from configured source repos:

```bash
sv add all
sv add --all
```

If multiple repos provide the same skill folder name, `sv add all` stops before copying anything and asks you to choose sources explicitly with `repo_id:skill` references. If a skill already exists in `.pi/skills`, sv prints a friendly message and leaves it unchanged.

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

`sv sync` pulls configured source repos, then replaces matching project skill folders with the source copy. Local edits inside synced skill folders are overwritten. Legacy skills without manifest entries are adopted and overwritten only when exactly one configured repo provides that skill name; ambiguous or local-only skills are skipped with a clear message.

Update source repo caches and then sync project skills with explicit progress messages:

```bash
sv update
```

`sv sync` and `sv update` both refresh configured source repos before syncing project skills. Use `sv update` when you want the refresh-and-sync operation to be explicit in command output. Use `sv list` when you only want to refresh source caches before listing or choosing skills.

Run Pi with global skill discovery disabled and only project skills enabled:

```bash
sv run -- <pi args>
sv run -- --model fast
```

`sv run` requires the `pi` executable on your `PATH`. Use `--` before Pi options so they are forwarded to Pi instead of parsed by `sv`.

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

The frontmatter `name` must match the folder name, and `description` must be non-empty. Skill folders with missing or malformed metadata are skipped by `sv list`, `sv add`, `sv add all`, `sv sync`, and `sv update`. Unreadable or non-UTF-8 `SKILL.md` files stop the command with a clear error so source repository problems are not missed.

## Project manifest

When `sv` installs or syncs a skill, it records the source repo in `.pi/skills/.sv-manifest.toml`. This lets `sv sync` and `sv update` refresh skills from the repo they came from, even when multiple repos contain the same skill name.

Existing project skills without manifest entries are backfilled during sync when exactly one configured repo provides a valid skill with that name. Ambiguous or local-only skills are skipped with a clear message.

## Safety and failure behavior

`sv add` does not overwrite an existing project skill. It copies into a temporary sibling directory first, then records the installed skill in the project manifest. If the copy or manifest update fails, `sv` removes temporary files and reports a user-facing error.

`sv remove` validates the project manifest before changing files. If manifest cleanup fails during removal, `sv` restores the local skill directory so the project is not left with a missing skill and stale metadata.

`sv sync` replaces managed skills from their recorded source repo. It copies the source skill first and keeps a temporary backup of the local skill so the previous version can be restored if replacement or manifest update fails.

Hidden directories matching `.<skill>.sv-*` inside `.pi/skills` are sv internals for in-progress or rolled-back file operations and should not be edited by hand.

For safety, `sv` refuses to manage symlinked `.pi` / `.pi/skills` paths, symlinked project skill directories, symlinked source cache paths, symlinked source `skills/` roots, and symlinks inside source skill folders. Symlinked source skill directories are skipped during catalog loading. Manifest writes also refuse symlinked temporary manifest files. These checks prevent a project or source repo from redirecting add, remove, sync, or run operations outside the expected directories.

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
- **`No skill source repos configured.`** Add a source with `sv repo add <owner/repo>` before listing or adding skills.
- **Interactive selection requires a TTY.** Run `sv add -l` or `sv remove -l` in an interactive terminal, or use non-interactive commands such as `sv add <skill>` and `sv remove <skill>`.
- **`Multiple source skills match ...` / duplicate skill names.** Run `sv list` and copy the `Add as` value from the `Duplicate skill names` section, for example `sv add HamdiMaz/Skills:find-docs`.
- **`Unable to run 'pi'.`** Install Pi and make sure the `pi` executable is available on your `PATH`.

# sv command reference

Use `sv` from the project root where you want Pi skills installed under `.pi/skills`.

## Daily commands

| Command | Use when | Notes |
| --- | --- | --- |
| `sv list` | You want to see available source skills. | Refreshes configured source caches before listing. If no repos are configured, prints the `sv repo add` next step. The main table stays compact with one row per skill name; duplicate names get a separate section with descriptions and `Add as` values, showing each duplicate skill name once per group. |
| `sv search <query>` | You want to find source skills by text. | Searches skill name, description, repo, and source path. Non-TTY output is a ranked table; TTY output opens the searchable browser so Enter can show details. |
| `sv add <skill>` | One configured repo provides the skill name. | Never overwrites an existing project skill. If multiple repos match in an interactive terminal, shows a compact source-choice table before prompting. |
| `sv add <repo>:<skill>` | Multiple repos provide the same skill name. | Copy the exact value from the `Duplicate skill names` section in `sv list`. |
| `sv add -l` | You want to select several skills interactively. | Requires a TTY. Use Space to select and Enter to confirm. |
| `sv add --all` | You want every non-conflicting source skill. | Stops before copying if duplicate skill names exist across repos. |
| `sv remove <skill>` | You want to remove one project skill. | Updates `.pi/skills/.sv-manifest.toml`. |
| `sv remove -l` | You want to remove several project skills interactively. | Lists installed project skills, not source skills. |
| `sv sync` | You want installed skills refreshed from their recorded sources. | Refreshes configured sources first; overwrites managed skill folders. Trust configured sources: when a refreshed source index reports the same content hash already recorded in the project manifest, `sv` skips re-materializing that skill as an optimization. |
| `sv update` | You want explicit refresh-and-sync output. | Equivalent to refresh sources plus `sv sync`, with progress messages and the same trusted-index behavior; a stale or malicious index can hide source changes until regenerated or re-added. |
| `sv run -- <pi args>` | You want Pi to use only project-local skills. | Runs `pi --no-skills --skill .pi/skills ...`. |

## Source repo commands

| Command | Use when |
| --- | --- |
| `sv repo add <owner/repo>` | Add a GitHub source repo. |
| `sv repo add <path-or-url>` | Add a local or Git URL source repo. |
| `sv repo list` | Show configured source IDs, URLs, and cache paths. If the repo list is empty, prints the `sv repo add` next step instead of an empty table. |
| `sv repo -l` | exact alias for `sv repo list`; opens the same TTY repo browser or prints the same non-TTY table. |
| `sv repo remove <repo-id>` | Stop using a configured source repo. |
| `svx <repo>` | console script alias for `sv repo add <repo>`; passes through repeatable `--skills-path` values. |

`sv repo remove` only changes global configuration. It does not delete cached clones and does not remove skills already installed in projects. `svx` is a shortcut for adding a source repo from scripts or shells where a shorter command is useful.

## Index publishing

Run `sv index` in a Git repo to scan valid `SKILL.md` folders and write `.sv/index.toml`. Limit scan roots with repeatable `--include PATH` flags and skip subtrees with repeatable `--exclude PATH` flags. To make those defaults persistent for a repo, create `.sv/index-config.toml`:

```toml
schema_version = 1
include_paths = ["skills", "packages/agents/pi/skills"]
exclude_paths = ["skills/drafts"]
```

## Searching source skills

Use `sv search <query>` when you know part of a skill's purpose, source repo, or path but not the exact skill name:

```bash
sv search find-docs
```

Search is case-insensitive across skill name, description, repo, and source path. It uses lightweight fuzzy matching for skill names, repo IDs, aliases, and source paths, while descriptions match by text substring. In non-interactive output, matching skills are sorted by rank. In an interactive terminal, search opens the same read-only browser used by `sv list`, with `/` filtering and Enter for details.

## Duplicate skill names

A project can only contain one `.pi/skills/<name>` folder. When multiple source repos provide the same skill name:

1. Run `sv list`.
2. Copy the `Add as` value from the `Duplicate skill names` section for the source you want.
3. Install it with `sv add <repo>:<skill>`.

Example:

```bash
sv list
sv add HamdiMaz/Skills:find-docs
```

`sv add -l` also shows repo-aware labels and rejects a selection that contains two sources for the same skill before copying anything. If duplicate repo entries point to the same or equivalent GitHub URL, `sv` coalesces them so the same source is not listed twice.

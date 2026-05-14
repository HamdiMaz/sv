# sv command reference

Use `sv` from the project root where you want Pi skills installed under `.pi/skills`.

## Daily commands

| Command | Use when | Notes |
| --- | --- | --- |
| `sv list` | You want to see available source skills. | Refreshes configured source caches before listing. If no repos are configured, prints the `sv repo add` next step. The main table stays compact with one row per skill name; duplicate names get a separate section with descriptions and `Add as` values, showing each duplicate skill name once per group. |
| `sv add <skill>` | One configured repo provides the skill name. | Never overwrites an existing project skill. If multiple repos match in an interactive terminal, shows a compact source-choice table before prompting. |
| `sv add <repo>:<skill>` | Multiple repos provide the same skill name. | Copy the exact value from the `Duplicate skill names` section in `sv list`. |
| `sv add -l` | You want to select several skills interactively. | Requires a TTY. Use Space to select and Enter to confirm. |
| `sv add --all` | You want every non-conflicting source skill. | Stops before copying if duplicate skill names exist across repos. |
| `sv remove <skill>` | You want to remove one project skill. | Updates `.pi/skills/.sv-manifest.toml`. |
| `sv remove -l` | You want to remove several project skills interactively. | Lists installed project skills, not source skills. |
| `sv sync` | You want installed skills refreshed from their recorded sources. | Pulls sources first; overwrites managed skill folders. |
| `sv update` | You want explicit refresh-and-sync output. | Equivalent to refresh sources plus `sv sync`, with progress messages. |
| `sv run -- <pi args>` | You want Pi to use only project-local skills. | Runs `pi --no-skills --skill .pi/skills ...`. |

## Source repo commands

| Command | Use when |
| --- | --- |
| `sv repo add <owner/repo>` | Add a GitHub source repo. |
| `sv repo add <path-or-url>` | Add a local or Git URL source repo. |
| `sv repo list` | Show configured source IDs, URLs, and cache paths. If the repo list is empty, prints the `sv repo add` next step instead of an empty table. |
| `sv repo remove <repo-id>` | Stop using a configured source repo. |

`sv repo remove` only changes global configuration. It does not delete cached clones and does not remove skills already installed in projects.

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

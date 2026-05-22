# sv command reference

Use `sv` from the project root where you want project skills installed under the default agent skill folder.

## Daily commands

| Command | Use when | Notes |
| --- | --- | --- |
| `sv init [folder]` | You want to create a skill-vault repository scaffold. | Without a folder, initializes the enclosing Git/sv repository root or the current directory when outside one. With a folder, creates a standalone vault only when run outside existing repositories. |
| `sv status` | You want local managed-skill state. | Reports modified, update-available, orphan, missing/invalid target, index, and README status for projects, skill-vaults, or global source context. Uses fresh cached source metadata when available; use `sv status --refresh` for an immediate current-source check or `sv status --cached` to avoid source refreshes. Global source index/catalog hashes are abbreviated to the first 12 digest characters for readable tables; full hashes stay in `~/.sv/manifest.toml`. |
| `sv list` | You want to see available source skills. | Uses fresh cached metadata when available, refreshes lazily when it expires, and supports `--refresh` to force a source refresh. If no repos are configured, prints the `sv repo add` next step. TTY output opens an interactive browser. Press `/` to open the framed search prompt with ranked results; Enter applies the typed search, empty Enter clears the active search, and Esc cancels without changing the current results. Enter opens a framed inline detail card, detail `a` adds the shown skill and shows the add result/status, detail `q`/Esc returns to the list, and list `q`/Esc exits; non-TTY output prints the compact table and duplicate-name `Add as` section. |
| `sv search <query>` | You want to find source skills by text. | Searches skill name, description, repo, and source path. Non-TTY output is a ranked table; TTY output opens the searchable browser. Press `/` to open the framed search prompt with ranked results; Enter applies the typed search, empty Enter clears the active search, and Esc cancels without changing the current results. Enter opens a framed inline detail card, detail `a` adds the shown skill and shows the add result/status, detail `q`/Esc returns to the list, and list `q`/Esc exits. |
| `sv add <skill>` | One configured repo provides the skill name. | Installs into the default agent skill folder and never overwrites an existing project skill. If multiple repos match in an interactive terminal, shows a compact source-choice table before prompting. |
| `sv add <repo>:<skill>` or `sv add <repo>:<path/to/skill>` | Multiple repos or source paths provide the same skill name. | Copy the exact `Add as` value from the `Duplicate skill names` section in `sv list`. |
| `sv add -l` | You want to select several skills interactively. | Requires a TTY. Press `/` to open the framed search prompt. Enter applies the typed search, empty Enter clears the active search, and Esc cancels without changing the current results. Use Space to select and Enter to confirm. |
| `sv add --all` | You want every non-conflicting source skill. | Stops before copying if duplicate skill names exist across repos. |
| `sv add --all --repo <repo>` | You want every non-conflicting skill from one source. | Restricts bulk add to the configured repo ID, useful when other repos contain duplicate skill names. |
| `sv add --replace <skill>` | You are adding inside a skill-vault and intentionally want to replace an existing vault skill. | Normal project adds to the default agent skill folder still never overwrite; non-TTY vault replacement requires `--replace`. |
| `sv remove <skill>` | You want to remove one project skill from the default agent skill folder. | Updates the canonical `.sv/manifest.toml` project manifest. |
| `sv remove -l` | You want to remove several sv-managed entries interactively. | Lists managed local skills, not source skills, and may include stale missing/invalid manifest entries so they can be pruned. |
| `sv remove --all` | You want to remove every sv-managed local skill. | Removes managed skills only; manual/unmanaged skill folders are kept. Confirmed bulk removal also prunes stale manifest entries whose managed skill folder is missing or no longer a directory. |
| `sv remove --all --yes` | You want non-interactive bulk removal. | Confirms bulk removal without prompting, which is required for non-TTY `--all` use. |
| `sv default [pi|claude|agents]` | You want to show or change the project default agent folder. | Without an argument, prints the current default and supported values. With an agent, writes `default_agent` to `.sv/manifest.toml`. |
| `sv update` | You want a safe refresh of sources and unchanged local skills. | Refreshes configured sources, updates only managed skill folders without local modifications, and preserves local edits by marking modified skills with update-available state. Uses the same trusted-index optimization as sync. |
| `sv sync` | You want installed skills force-refreshed from their recorded sources. | Refreshes configured sources first; overwrites managed skill folders. Trust configured sources: when a refreshed source index reports the same content hash already recorded in the project manifest, `sv` skips re-materializing that skill as an optimization. |
| `sv run pi [pi args]` | You want Pi to use only project-local skills. | Uses the explicit supported agent name `pi`, validates `.pi/skills` for symlinks and bounded skill trees, then runs `pi --no-skills --skill .pi/skills ...`. |

## Source repo commands

| Command | Use when |
| --- | --- |
| `sv repo add <owner/repo>` | Add a GitHub source repo and warm its source metadata cache by default. |
| `sv repo add <path-or-url>` | Add a local or Git URL source repo and warm its source metadata cache by default. |
| `sv repo add <repo> --skills-path <path>` | Add a source repo with one bounded discovery root. Repeat `--skills-path` to scan multiple repo-relative skill roots. |
| `sv repo add <repo> --no-warm-cache` | Add or update source configuration without the default best-effort local cache warm step. |
| `sv repo list [--refresh/--cached]` | Show configured source IDs, URLs, and cache paths. If the repo list is empty, prints the `sv repo add` next step instead of an empty table. In a TTY, Enter on a repo opens that repo's skill browser using fresh cached metadata when available; `--refresh` forces a metadata refresh before opening repo skills, and `--cached` requires existing cached metadata while avoiding network and Git refreshes. In the repo skill browser, list-mode `a` remains add-all for non-conflicting skills from that repo; Enter opens a detail page, detail-mode `a` adds only the shown skill and shows the add result/status, detail `q`/Esc returns to the repo skill list, and list `q`/Esc backtracks or exits. |
| `sv repo -l [--refresh/--cached]` | exact alias for `sv repo list`; opens the same TTY repo browser or prints the same non-TTY table. |
| `sv repo remove <repo-id>` | Stop using a configured source repo. |
| `sv repo remove -l` | Choose one or more source repos to remove from an interactive list. Use `--yes` to skip the confirmation prompt. |
| `svx <repo>` | console script alias for `sv repo add <repo>`; passes through repeatable `--skills-path` values and warms source metadata by default. |
| `svx <repo> --no-warm-cache` | Add a source repo through `svx` without the default best-effort local cache warm step. |

`sv repo remove` only changes global configuration. It does not delete cached clones and does not remove skills already installed in projects. Use `sv repo -l` as a shorter spelling of `sv repo list`; it accepts the same cache flags. In non-TTY output, repo-list cache flags parse successfully but the plain repo table does not load source metadata. `svx` is a shortcut for adding a source repo from scripts or shells where a shorter command is useful. Cache warming after `sv repo add` or `svx` is local and best-effort: warnings do not undo the repo configuration, and `--no-warm-cache` skips only that warm step.

Project skills can be installed under one supported project agent folder:
`.pi/skills`, `.claude/skills`, or `.agents/skills`.

Use `sv default` to show the current project default agent. Use
`sv default pi`, `sv default claude`, or `sv default agents` to set it.
The selected value is stored in `.sv/manifest.toml` as `default_agent`.
Changing it affects future project commands only; sv does not migrate existing
skill folders between agents.

`sv run` still requires an explicit supported run agent. The supported run agent is `pi`, and it uses `.pi/skills` regardless of the project default agent. Put Pi arguments after the agent name, for example `sv run pi --model fast`.

## Index publishing

Run `sv index` in a Git repo to scan valid `SKILL.md` folders and write `.sv/index.toml`. Source-published indexes are the fastest path for first use by every user because `sv` can read one metadata file instead of probing each skill folder. In a skill-vault repo, `sv index` also refreshes the generated README skill table; README files touched by that update must be UTF-8 and no larger than 4 MiB. Index scanning is bounded by skill-candidate and directory-depth limits so huge accidental trees fail closed instead of exhausting traversal. If the scan finds duplicate skill content with the same full content hash and `SKILL.md` hash, the generated index keeps the highest-level `source_path`, uses lexicographic `source_path` order as the tie-breaker, and omits deeper duplicates while preserving same-name skills whose hashes differ. Limit scan roots with repeatable `--include PATH` flags and skip subtrees with repeatable `--exclude PATH` flags. To make those defaults persistent for a repo, create `.sv/index-config.toml`; `sv` uses the same persistent scan config when it auto-refreshes a local index or checks skill-vault index/README freshness:

The generated index includes `executable_paths` for every skill's executable file bits. New indexes write an empty list when a skill has no executable files, so installers can distinguish an authoritative empty set from older indexes that did not publish executable metadata. This avoids extra per-skill GitHub API calls on the normal indexed install path.

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

Search is case-insensitive across skill name, description, repo, and source path. It uses lightweight fuzzy matching for skill names, repo IDs, aliases, and source paths, while descriptions match by text substring. In non-interactive output, matching skills are sorted by rank. In an interactive terminal, search opens the same interactive browser used by `sv list`. Press `/` to open the framed search prompt. Enter applies the typed search, empty Enter clears the active search, and Esc cancels without changing the current results. Enter opens an inline detail page, detail `a` adds the shown skill and shows the add result/status, detail `q`/Esc returns to the list, and list `q`/Esc exits.

## Duplicate skill names

A project can only contain one folder for a given skill name in its default agent skill folder. When multiple source repos provide the same skill name:

1. Run `sv list`.
2. Copy the `Add as` value from the `Duplicate skill names` section for the source you want.
3. Install it with `sv add <repo>:<skill>` or, for path-aware entries, `sv add <repo>:<path/to/skill>`.

Example:

```bash
sv list
sv add HamdiMaz/Skills:find-docs
# path-aware Add as values are also valid:
sv add Team/Skills:packages/agents/pi/skills/find-docs
```

`sv add -l` also shows repo-aware labels and rejects a selection that contains two sources for the same skill before copying anything. If duplicate repo entries point to the same or equivalent GitHub URL, `sv` coalesces them so the same source is not listed twice.

## Source discovery and refresh

GitHub source repos are refreshed through `gh api` first, then the GitHub HTTPS API, then lightweight treeless/blobless sparse Git fallback. Use `gh auth login`, `GH_TOKEN`, or `GITHUB_TOKEN` when you need private GitHub repos or higher API rate limits. Local path sources read the local Git worktree directly, and non-GitHub remotes use lightweight Git.

Sources can publish skills under `skills/<name>`, one-level `*/skills/<name>` folders, roots configured with repeatable `sv repo add --skills-path <path>`, or arbitrary paths listed in `.sv/index.toml` generated by `sv index`.

## Global cache

`sv` keeps a global cache under `~/.sv/cache/v1`. Source metadata is cached for 24 hours for normal browsing/install commands (`sv list`, `sv search`, TTY `sv repo list` nested skill browsing, and all `sv add` modes, including `sv add`, `sv add -l`, and `sv add --all`). `sv repo add` and `svx` warm this same 24-hour catalog cache by default so the first later list/search/add command can reuse local metadata. These commands use fresh cached metadata, refresh lazily when it expires, and warn while using stale metadata if refresh fails and cached metadata exists.

Default `sv status` uses fresh cached source metadata when available and refreshes only when metadata is missing or stale. It fails closed if that required refresh fails. Use `sv status --refresh` for an immediate current-source check, or `sv status --cached` to avoid network and Git refreshes and require existing cached metadata. `sv sync` and `sv update` remain refresh-first by default so they continue to apply current source changes before mutating local skills. These commands still use the cache manager for `--cached`, cache writes, body-cache reuse, and cache-only error handling.

Materialized skill folders are cached by content hash. Indexed sources provide the content hash in source metadata; non-index sources learn and record the hash after the first successful add/sync/update materialization. Reinstalling or syncing a skill can reuse a cached skill body when the cached hash matches the source metadata. If normal mode has cached metadata but no matching cached body, `sv` refreshes that repo's metadata before materializing from source, so stale metadata is not paired with a newer source body. Cached skill bodies are pruned lazily after cache writes: entries unused for more than 30 days are removed, and the cache is reduced to 256 MiB when it grows beyond that size.

Use `--refresh` on source-reading commands to force metadata refresh. Use `--cached` to avoid network and Git refreshes; cached mode requires existing metadata and errors if that metadata is missing. Commands that need to materialize a skill with `--cached` also require a cached skill body; if the body is missing, rerun once without `--cached` to populate it. Use `sv cache status` to inspect cache usage and `sv cache clean` to prune cached skill bodies immediately.

## Parallelism

Source-reading commands can refresh independent configured repos in parallel. When a source does not publish an index, fallback source metadata reads can also use `SV_JOBS` within that repo while probing candidate skill folders. Bulk `add --all`, `sync`, and `update` prepare independent skill trees in parallel, then commit final filesystem and manifest changes in stable order. `status` and `index` can hash independent skill folders in parallel.

Use `SV_JOBS=1` when you want fully sequential execution for debugging or reproducing a race. Use `SV_JOBS=N` with `N` from 1 through 64 to choose a specific worker count. Invalid values fail before command work starts with a clear error.

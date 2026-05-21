# sv

`sv` manages project-local AI agent skills for supported project agents. It copies valid skills from one or more Git-backed source repositories into the current project's default agent skill directory and records where installed skills came from so they can be synced reliably.

Start with [getting started](docs/getting-started.md) for the shortest setup path. For copy-paste workflows, see [examples](docs/examples.md). For a command-focused walkthrough, see the [usage guide](docs/usage.md). For a compact command reference, see [commands](docs/commands.md). For table columns, picker behavior, wrapping, and duplicate-name guidance, see [reading sv output](docs/output.md). For test commands and marker usage, see [testing sv](docs/testing.md). For common fixes, see [troubleshooting](docs/troubleshooting.md).

## Installation

`sv` is a Python CLI package and requires Python 3.14 or newer. Install it with your preferred Python package tool, then run it from the root of the project whose agent skills you want to manage.

```bash
# After publication:
uv tool install sv

# From the Git repository:
uv tool install git+https://github.com/HamdiMaz/sv.git

# From a checkout:
uv tool install .
```

`sv` reads GitHub sources through `gh api` or the GitHub HTTPS API when available, then falls back to lightweight Git checkouts. Local sources and generic Git remotes require `git` on your `PATH`; GitHub CLI (`gh`) is optional but useful for authenticated/private GitHub repositories.

## Where sv stores files

Source repos are configured explicitly. If no source config exists yet, interactive commands can offer recommended sources; non-interactive commands fail with a helpful `sv repo add <repo>` next step instead of assuming a default repo.

Global sv files live under:

```text
~/.sv/
```

Cached source repositories live under:

```text
~/.sv/sources/
```

Global cache metadata and skill bodies live under:

```text
~/.sv/cache/v1/
```

Source metadata for normal browsing/install commands and default `sv status` checks is cached locally for 24 hours. `sv list`, `sv search`, TTY `sv repo list` nested skill browsing, all `sv add` modes, and plain `sv status` use fresh cached metadata when available and refresh lazily when metadata is missing or stale. `sv repo add` and `svx` warm this same machine-local catalog cache by default; `--no-warm-cache` skips only that best-effort local warm step. Use `sv status --refresh` when you need an immediate current-source status check. `sv sync` and `sv update` still refresh metadata by default before changing local skills. `--cached` requires existing metadata and errors if metadata is missing. Skill bodies are cached by content hash and pruned lazily after 30 days unused or when the skill-body cache exceeds 256 MiB. For non-index sources, the body hash is recorded after the first successful materialization. If cached metadata points at a skill whose body is not cached, normal mode refreshes that repo before source materialization; `--cached` fails instead of refreshing.

Project skills can be installed under one supported project agent folder:
`.pi/skills`, `.claude/skills`, or `.agents/skills`.

Use `sv default` to show the current project default agent. Use
`sv default pi`, `sv default claude`, or `sv default agents` to set it.
The selected value is stored in `.sv/manifest.toml` as `default_agent`.
Changing it affects future project commands only; sv does not migrate existing
skill folders between agents.

```bash
sv default
sv default pi
sv default claude
sv default agents
```

## Parallel work

`sv` runs independent source refreshes, non-index fallback metadata reads, bulk skill materialization preparation, and read-only skill hashing in parallel by default. Visible output, manifest writes, final skill-folder replacement, and cache metadata writes remain deterministic and serialized. Set `SV_JOBS=1` to disable worker threads for debugging, or set `SV_JOBS=N` to choose a worker count from 1 through 64. The default is bounded to at most 8 workers.

## Source repo configuration

`sv` reads global repo configuration from `~/.sv/config.toml`. If that file is missing, or if it only contains `schema_version`, `sv` does not assume any source repo. In an interactive TTY it can prompt you to add a recommended source; in non-interactive use it reports `No skill source repos are configured` and tells you to run `sv repo add <repo>`. After you run `sv repo add`, only repos recorded in the config are used. `sv repo add` accepts GitHub shorthand such as `owner/repo`, GitHub HTTPS/SSH URLs, and local Git repository paths. It warms source metadata into the local catalog cache by default so listing, searching, and adding can start from cached metadata; pass `--no-warm-cache` when you only want to update config. Existing bare relative paths, and paths starting with `./`, `../`, `/`, or `~`, are resolved to absolute paths before they are saved, so global configuration keeps working no matter which project directory you run `sv` from later. If a local path looks like GitHub shorthand, use an explicit path prefix such as `./owner/repo` or `../repo`. Use `sv repo list` to see the derived repo ID used by qualified skill references and `sv repo remove`.

To use the public HamdiMaz skills repo plus a team repo, add both explicitly:

```bash
sv repo add HamdiMaz/Skills
sv repo add SomeOrg/TeamSkills
# Config-only/offline setup:
sv repo add SomeOrg/TeamSkills --no-warm-cache
```

Removing the last repo writes `repos = []`, which intentionally disables all sources until you add another repo. Removing `~/.sv/config.toml` returns to first-run behavior, not to an implicit default source. Repo IDs and aliases must stay unambiguous; `sv` rejects config loads and `sv repo add` attempts that would make one reference point at different source repos.

## Quick start

```bash
sv repo add HamdiMaz/Skills
sv default pi
sv list
sv add find-docs
sv sync
sv run pi <pi args>
```

The first `sv repo add` warms the local catalog cache by default; use `--no-warm-cache` to skip that warm step for config-only or offline setup.

## Commands

Manage source repos:

```bash
sv repo add HamdiMaz/Skills
sv repo add SomeOrg/TeamSkills
sv repo add SomeOrg/TeamSkills --no-warm-cache
sv repo list
sv repo remove SomeOrg/TeamSkills
```

`sv repo add` and `svx <repo>` warm source metadata into the local catalog cache by default; `svx <repo> --no-warm-cache` and `sv repo add <repo> --no-warm-cache` skip that best-effort warm step. `sv repo remove` updates global configuration only. It does not delete cached source clones or remove project skills that were already installed from that repo. In TTY `sv repo list`, Enter on a repo opens that repo's skill browser. In the repo skill browser, list-mode `a` adds all non-conflicting skills from that repo, Enter opens a detail page, detail-mode `a` adds only the shown skill and shows the add result/status, detail `q`/Esc returns to the repo skill list, and list `q`/Esc backtracks or exits.

List valid skills available in configured source repos:

```bash
sv list
```

`sv list` uses fresh cached source metadata when available, refreshes lazily when metadata expires, and supports `--refresh` to force a source refresh. In an interactive terminal it opens an interactive browser with `Skill`, `Source`, and `Description` columns; use ↑/↓ to move, Enter to open an inline detail page, detail `a` to add the shown skill and show the add result/status, and detail `q`/Esc to return to the list. Press `/` to open the framed search prompt. Enter applies the typed search, empty Enter clears the active search, and Esc cancels without changing the current results. List `q`/Esc exits the browser. In non-interactive output, or when the browser is unavailable, it prints the same compact columns as a table. When duplicate skill names exist across repos, the table groups that name into one `N sources` row and prints a separate `Duplicate skill names` section with each repo, description, and exact `Add as` value to copy; each duplicate group shows the skill name once to keep the choices easy to scan. Tables wrap to the current terminal width so long repo IDs, cache paths, and descriptions stay readable; repo-like values prefer clean wrap points at `/` and `:` before falling back to hard wrapping. If a config accidentally repeats the same repo entry, the same repo URL, or an equivalent GitHub URL under different IDs, `sv` coalesces it while reading the config so the same source skill is not listed twice. See [reading sv output](docs/output.md) for examples.

Add a skill to the current project:

```bash
sv add github-release
```

If multiple repos provide the same skill name, `sv` shows a compact source-choice table with repo, description, and qualified `Add as` values, then lets you choose when stdin and stdout are interactive TTYs. In non-interactive use, provide a qualified name. The `Add as` value may be `repo_id:skill` for normal layouts or `repo_id:path/to/skill` for indexed/non-default source paths:

```bash
sv add HamdiMaz/Skills:github-release
sv add Team/Skills:packages/agents/pi/skills/github-release
```

If that skill already exists from another source, `sv` leaves the local skill unchanged and prints both the current origin and the requested origin. Run `sv remove <skill>` first when you intentionally want to switch sources.

Choose one or more skills from an interactive repo-aware list:

```bash
sv add -l
```

Use ↑/↓ to move, ←/→ to page through skills, Space to select, Enter to add,
and `q` to cancel. Press `/` to open the framed search prompt. Enter applies the typed search, empty Enter clears the active search, and Esc cancels without changing the current results. The picker renders inline, shows 5 skills at a time, displays a compact help/status line with the visible range and controls, and
scrolls as you move. Each picker row includes the source repo ID so duplicate skill names are easy to distinguish without repeating the skill name twice. Long picker labels and the help line are truncated to the terminal width, and control characters from source metadata are escaped before display so the inline UI stays stable. If you select two sources for the same skill name, `sv` stops before copying anything and asks you to choose only one source. When a source repo is already cached, `sv add -l` reads the
cache without pulling first so the picker opens quickly. Run `sv list` when you
want to refresh source caches without changing project skills; use `sv update`
when you also want to safely update unchanged installed project skills.

Add every valid, non-conflicting skill from configured source repos:

```bash
sv add --all
sv add --all --repo HamdiMaz/Skills
```

Use `--repo` to bulk-add only from one configured source. If multiple repos provide the same skill folder name, `sv add --all` stops before copying anything and asks you to choose sources explicitly with `repo_id:skill` or `repo_id:path/to/skill` references. If a skill already exists in the default agent skill folder, sv prints a friendly message and leaves it unchanged. In a skill-vault repository, `sv add --replace` can replace an existing vault skill target; non-TTY vault replacement requires that flag and warns about local edits before overwriting.

Remove a skill from the current project:

```bash
sv remove github-release
```

Choose one or more project skills from an interactive list and remove them:

```bash
sv remove -l
```

Safely update source metadata and unchanged project skills from their recorded source repos:

```bash
sv update
```

`sv update` refreshes configured sources, then updates managed project skills only when the local folder still matches its recorded baseline. It preserves local edits by skipping modified skills and marking them as modified with an update available in the project manifest/status output.

Force-sync project skills from their recorded source repos:

```bash
sv sync
```

`sv sync` refreshes configured sources, then replaces managed project skill folders with the source copy recorded in the manifest. Local edits inside synced managed skill folders are overwritten. Unmanaged local skill folders without manifest entries are not adopted automatically; unique matches and local-only folders are skipped as local-only, while ambiguous matches report the possible sources. Use `sv list` when you only want to refresh source caches before listing or choosing skills.

Treat configured source repositories and their source-published `.sv/index.toml` files as trusted inputs. When a refreshed source index reports the same content hash already recorded in the project manifest, `sv sync`/`sv update` can skip re-materializing that skill as an optimization; a stale or malicious index can therefore hide source changes until the index is regenerated or the source is removed and re-added.

Run Pi with global skill discovery disabled and only project skills enabled:

```bash
sv run pi <pi args>
sv run pi --model fast
```

`sv run` still requires an explicit supported run agent. The supported run agent is `pi`, and it uses `.pi/skills` regardless of the project default agent.

`sv run` requires the `pi` executable on your `PATH`. Before launching Pi, `sv` rejects symlinked project skill paths and nested symlinks inside `.pi/skills` so Pi is not pointed at files outside `.pi/skills`. It launches `pi --no-skills --skill .pi/skills` followed by forwarded Pi arguments.

## Source repo layout

sv discovers skills in immediate folders under `skills/`, one-level nested `*/skills/` folders, repeatable repo-relative roots configured with `sv repo add --skills-path`, and arbitrary repo-relative paths published through a source repo's `.sv/index.toml` generated by `sv index`. The published `.sv/index.toml` is source metadata committed by the source maintainer and is the fastest path because `sv` reads one metadata file. The catalog cache under `~/.sv/cache/v1/` is separate machine-local metadata populated by source reads and repo-add warming. `sv repo add` does not mutate remote sources or create `.sv/index.toml`; run and commit `sv index` in the source repository when you want to publish an index. Each skill folder must contain `SKILL.md` with frontmatter:

When `sv index` sees multiple valid skill folders with identical `content_hash` and `skill_file_hash` values, it writes only one entry for that content. The retained entry is the skill closest to the repository root, with lexicographic `source_path` order as the tie-breaker. Skills that share a name but have different hashes remain separate path-aware entries.

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

The frontmatter `name` must match the folder name, and `description` must be non-empty. Skill names must be plain single-folder names: no leading `.`, no leading `-`, no slashes, backslashes, colons, control characters, or Unicode format controls such as zero-width and bidi override characters. Each `SKILL.md` metadata file is limited to 1 MiB so a hostile source cannot exhaust memory during discovery. Skill folders with missing or malformed metadata are skipped by `sv list`, `sv add`, `sv add --all`, `sv sync`, and `sv update`. Unreadable or non-UTF-8 `SKILL.md` files stop the command with a clear error so source repository problems are not missed.

## Project manifest

When `sv` installs or syncs a skill, it records the source repo in the canonical project manifest at `.sv/manifest.toml`. Legacy `.pi/skills/.sv-manifest.toml` files remain readable for migration. This lets `sv sync` and `sv update` refresh skills from the repo they came from, even when multiple repos contain the same skill name.

Existing project skills without manifest entries stay unmanaged during sync. `sv` skips unique or local-only folders as local-only and reports ambiguous folders with the matching source repos, so accidental adoption never overwrites hand-managed skills.

## Safety and failure behavior

`sv add` does not overwrite an existing project skill. It copies into a temporary sibling directory first, then records the installed skill in the project manifest. If the copy or manifest update fails, `sv` removes temporary files and reports a user-facing error.

`sv status` reports managed manifest entries even when their local skill folder was deleted or replaced manually, marking them as `missing` or `invalid target` so stale metadata is visible. If every recorded target is unavailable, status stays local and avoids refreshing configured sources just to print that local state. Confirming `sv remove --all` also cleans those stale entries from the manifest while still leaving unmanaged folders or files alone.

`sv remove` validates the project manifest before changing files. If manifest cleanup fails during removal, `sv` restores the local skill directory so the project is not left with a missing skill and stale metadata.

`sv sync` replaces managed skills from their recorded source repo. It copies the source skill first and keeps a temporary backup of the local skill so the previous version can be restored if replacement or manifest update fails.

Hidden directories matching `.<skill>.sv-*` inside the active/default agent skill folder are sv internals for in-progress or rolled-back file operations and should not be edited by hand.

For safety, `sv` refuses to manage symlinked project agent skill paths such as `.pi/skills`, `.claude/skills`, or `.agents/skills`, symlinked project skill directories, symlinked source cache paths, symlinked source `skills/` roots, and symlinks inside source or project skill folders before copy/sync/run operations. Symlinked source skill directories are skipped during catalog loading. Manifest reads and writes reject symlinked manifest files or temporary manifest files. TOML config and manifest files are limited to 1 MiB; source `.sv/index.toml` files fetched from configured repos and README files touched by skill-vault table updates are limited to 4 MiB; skill-tree hashing and materialization enforce file-count, byte, depth, and unsafe nested-path-name budgets; index generation enforces skill-candidate and directory-depth budgets; content hashes must use the canonical `sha256:<64 lowercase hex characters>` form before `sv` trusts them. These checks prevent a project or source repo from redirecting add, remove, sync, run, or index operations outside the expected directories, hiding file names with terminal control or Unicode format characters, or feeding unbounded metadata into the CLI.

## Pi isolation

`sv run` launches Pi like this:

```bash
pi --no-skills --skill .pi/skills
```

Start Pi through `sv run pi` when you want to use only project-local Pi skills. `sv run pi` validates `.pi/skills` for symlinks and bounded size before launching Pi, regardless of the project default agent.

## Troubleshooting

- **`Git is required but was not found on PATH.`** Install Git and make sure the `git` executable is available in your shell.
- **`Source path ... exists but is not a Git clone.`** Remove the reported cache directory and rerun the command.
- **`Configured source repo is ..., but existing source clone uses ...`.** The configured repo ID points at a cache cloned from a different remote. Remove the reported cache directory or update your repo config.
- **`No skill source repos configured.`** Add a source with `sv repo add <owner/repo>` before listing or adding skills.
- **Interactive selection requires a TTY.** Run `sv add -l` or `sv remove -l` in an interactive terminal, or use non-interactive commands such as `sv add <skill>` and `sv remove <skill>`.
- **`Multiple source skills match ...` / duplicate skill names.** Run `sv list` and copy the `Add as` value from the `Duplicate skill names` section, for example `sv add HamdiMaz/Skills:find-docs`.
- **`Unable to run 'pi'.`** Install Pi and make sure the `pi` executable is available on your `PATH`.

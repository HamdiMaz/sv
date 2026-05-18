# Troubleshooting sv

Use this page when `sv` output is surprising or you are not sure which command to run next.

## `No skill source repos configured.`

Your global config exists and contains no repos, usually after removing the last source with `sv repo remove`. `sv list` and `sv repo list` both print this message instead of an empty table.

Add a source repo before listing or adding skills:

```bash
sv repo add HamdiMaz/Skills
# or your team's source repo
sv repo add SomeOrg/TeamSkills
```

## Duplicate skill names

A Pi project can only contain one `.pi/skills/<name>` directory. If two source repos provide the same skill name, `sv list` keeps the main skill table compact with one `N sources` row and adds a separate `Duplicate skill names` section with each repo, description, and qualified `Add as` value. The duplicate section shows each skill name once and leaves continuation rows blank in that column, which is expected. Copy the `Add as` value for the source you want.

Install the exact source you want. Most `Add as` values use `repo-id:skill`; path-aware entries can use `repo-id:path/to/skill`:

```bash
sv add <repo-id>:<skill>
sv add <repo-id>:<path/to/skill>
```

If the skill already exists from a different source, `sv add` leaves it unchanged and tells you the current source and the requested source. Remove it first if you intentionally want to switch:

```bash
sv remove <skill>
sv add <repo-id>:<skill>
```

## Tables look wrapped

`sv` wraps tables to your terminal width so descriptions, repo IDs, and cache paths stay visible in terminals and CI logs. If the output is too narrow to scan comfortably, widen your terminal or set `COLUMNS` for one command:

```bash
COLUMNS=120 sv list
```

Wide Unicode characters are counted by display width so rows should stay within the reported terminal width. In `sv status`, index and catalog hashes are intentionally abbreviated in the table; inspect `~/.sv/manifest.toml` when you need the full digest.

## Metadata validation errors

`sv` rejects oversized metadata before parsing it: TOML config and manifest files plus `SKILL.md` files are limited to 1 MiB, while source `.sv/index.toml` files fetched from configured repos and README files touched by skill-vault table updates have a separate 4 MiB limit. Source indexes and manifests also must store hashes as `sha256:<64 lowercase hex characters>`. If you see a size-limit, UTF-8, or hash-format error, regenerate the source index or manifest with a current `sv` instead of editing placeholder values by hand.

Skill names, configured/indexed source paths, and nested file names inside materialized skill folders must be visually safe. Rename source skill folders, frontmatter names, copied skill files, or `--skills-path` / `.sv/index.toml` paths that start with `-`, contain path separators or `:`, include terminal control characters, or include invisible Unicode format controls such as zero-width spaces or bidi override characters.

## The same source appears twice

If `~/.sv/config.toml` repeats the exact same repo, lists the same normalized URL under more than one repo ID, or mixes equivalent GitHub URL forms (HTTPS, SSH, optional `.git`), `sv` keeps the first entry and treats later IDs as aliases while loading skills. Existing sync metadata or qualified commands that point at a duplicate repo ID are still matched to the kept source. Use `sv repo list` to inspect the active sources; config rewrites preserve duplicate-source aliases so legacy `repo:skill` or path-aware `repo:path/to/skill` references keep working without listing the same skill twice.

## Interactive commands fail in scripts

`sv add -l` and `sv remove -l` require an interactive terminal with usable terminal settings. If your shell, editor, or pseudo-terminal cannot provide those settings, `sv` reports a user-facing terminal error instead of a traceback. In scripts, use explicit commands instead:

```bash
sv add find-docs
sv add HamdiMaz/Skills:find-docs
sv remove find-docs
```

## Global cache and offline mode

If you see a warning like `using stale cached metadata`, `sv` tried to refresh source metadata, the refresh failed, and the command used an older cached catalog instead. Only browsing/install commands allow this fallback: `sv list`, `sv search`, and all `sv add` modes (`sv add`, `sv add -l`, and `sv add --all`).

Use `--refresh` to force a new source check. `sv sync`, `sv update`, and source-aware `sv status` already refresh by default and fail closed on refresh errors unless you explicitly pass `--cached`.

Use `--cached` when you need to avoid network and Git source refreshes. This mode requires existing cached metadata. Commands that materialize skills (all add modes that copy skills, including `sv add --cached`, `sv add --all --cached`, and interactive add with `--cached`; plus `sv sync --cached` and `sv update --cached`) also require matching cached skill bodies. If a cached body is missing, rerun once without `--cached` to populate the body cache, then retry the cached command.

In normal mode, if cached metadata exists but the matching cached skill body is missing, `sv` refreshes that source repo before materializing from source. This prevents stale metadata from being paired with newer source content.

Invalid or tampered cache files are ignored or rejected depending on the command and cache mode. Symlinked cache paths are always refused. Run `sv cache clean` to remove expired and over-budget cached skill bodies.

## I want to disable parallel execution

Set `SV_JOBS=1` before the command:

```bash
SV_JOBS=1 sv list --refresh
SV_JOBS=1 sv update
```

`SV_JOBS` accepts integers from 1 through 64. Values outside that range, empty values, or non-integers fail with `SV_JOBS must be an integer between 1 and 64.`

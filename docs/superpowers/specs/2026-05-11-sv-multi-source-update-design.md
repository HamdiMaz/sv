# sv Multi-Source Update Design

Date: 2026-05-11

## Purpose

This design evolves `sv` from a single-source MVP into a multi-source skill manager. It adds a fast `update` command, supports multiple configured Git repositories, shows repository context in source listings and interactive add flows, and validates skills by parsing `SKILL.md` metadata.

The goal is to keep the user workflow simple:

```bash
sv repo add HamdiMaz/Skills
sv repo add SomeOrg/TeamSkills
sv list
sv add github-release
sv update
```

`sv update` is the convenient command users can run when they want everything current: source caches are refreshed first, then project skills are synced from their recorded origins.

## Scope

In scope:

- Multiple global Git-backed skill source repositories.
- Top-level source repository commands: `sv repo add`, `sv repo remove`, and `sv repo list`.
- Separate source cache directories per configured repository.
- Source skill cataloging with parsed `SKILL.md` metadata.
- Repo-aware tables for `sv list` and source selection flows.
- Repo-aware duplicate handling for `sv add <skill>`.
- Project manifest tracking installed skill origins.
- `sv update`, which refreshes all configured source caches and then runs project sync.
- Backfill behavior for old project skills that predate the manifest.

Out of scope for this round:

- Skill version pinning or lockfiles.
- Registry or marketplace API integration.
- Per-project source repository overrides.
- Non-Pi agent adapters.
- Full YAML parsing beyond the simple frontmatter shape used by Pi skills.
- Deleting cached source clones when a repo is removed from config.

## Source Repository Model

A configured source repository still uses the existing layout:

```text
repo/
  skills/
    github-release/
      SKILL.md
      ...
```

Each configured repo has:

- a normalized URL used for Git operations
- a stable repository ID used in UI, cache paths, and project manifests

For GitHub sources, the preferred repo ID is `owner/repo`. This applies to shorthand inputs like `HamdiMaz/Skills`, HTTPS GitHub URLs, and SSH GitHub URLs. GitHub guarantees uniqueness for `owner/repo`, so this ID is sufficient for distinguishing source repositories in normal use.

For local paths or non-GitHub URLs, `sv` derives a stable safe ID from the input. The derived ID is mainly for tests and power users. It must be safe for use as a directory name under `~/.sv/sources`.

Configured source caches live under:

```text
~/.sv/sources/<repo-id>/repo/
```

## Global Config

The current single-value config:

```toml
repo = "https://github.com/HamdiMaz/Skills.git"
```

is replaced by a multi-repo config. The implementation can choose a simple TOML shape, but it must preserve these properties:

- source repo order is stable
- each repo has a stable ID
- each repo has a normalized URL
- old single-repo configs can be migrated or read as a one-repo config

The user-facing config command is simplified. `sv config` is removed or treated as deprecated because the beta does not need a generic config surface yet.

New commands:

```bash
sv repo add <repo>
sv repo remove <repo-id>
sv repo list
```

`sv repo add <repo>` normalizes the repo input, derives the repo ID, and stores it unless the repo is already configured.

`sv repo remove <repo-id>` removes the repo from config. It does not delete cached clones and does not delete project skills.

`sv repo list` prints configured repos and their cache paths.

If no config exists, `sv` should preserve the current default behavior by treating `https://github.com/HamdiMaz/Skills.git` as the configured default source.

## Skill Metadata Parsing

A source folder is a valid skill only if it contains a valid `SKILL.md`.

`sv` parses YAML-style frontmatter at the start of `SKILL.md`:

```md
---
name: github-release
description: Use when creating releases...
---
```

Required fields:

- `name`
- `description`

Validation rules:

- `name` must be a safe single-folder skill name.
- the folder name must match the parsed `name`.
- `description` must be non-empty.
- the frontmatter block must appear at the top of `SKILL.md`.

Invalid skills are skipped from the source catalog and are not installable through `add`, `add all`, `sync`, or `update`.

Parsing is intentionally small and stdlib-only. It only needs to support the simple `key: value` frontmatter shape used by current Pi skills. Quoted values may be supported if easy, but full YAML semantics are not required.

## Source Catalog

`sv` should build a source catalog from all configured repos. Each catalog entry contains:

- skill name
- description
- repo ID
- normalized repo URL
- source repo cache path
- source skill path

The catalog is the shared input for source-backed commands:

- `sv list`
- `sv add <skill>`
- `sv add all` / `sv add --all`
- `sv add -l` / `sv add --list`
- `sv sync`
- `sv update`

Catalog entries are sorted for stable output, first by skill name and then by repo ID.

## Tables and Interactive Selection

`sv list` refreshes all configured source caches, parses valid skills, and prints a table with repository context.

Table columns:

- `Skill`
- `Repo`
- `Description`

Example:

```text
Skill           Repo              Description
--------------  ----------------  --------------------------------------
github-release  HamdiMaz/Skills   Use when creating GitHub releases.
find-docs       HamdiMaz/Skills   Retrieves up-to-date documentation.
```

`sv add -l` uses the same repo-aware source entries. The interactive picker should show enough information for users to distinguish similarly named skills. A compact table-like row is acceptable in the existing selector UI.

The selector returns catalog entries, not only skill names, so installs preserve the chosen repo origin.

## Add Behavior

`sv add <skill>` searches the catalog for valid skills named `<skill>`.

If exactly one repo provides the skill, `sv` installs it directly.

If multiple repos provide the skill, `sv` shows all matching skills in a small table with:

- `#`
- `Skill`
- `Repo`
- `Description`

In a TTY, `sv` prompts the user to choose which matching skill to install.

In a non-TTY context, `sv` prints the matches and asks the user to rerun with a qualified form:

```bash
sv add owner/repo:skill-name
```

The qualified form is also supported for users who want to skip the prompt.

`sv add all` and `sv add --all` install all valid catalog entries from all configured repos. If two repos provide the same skill name, `sv` should avoid overwriting an existing project skill folder. It can install the first one encountered and report the duplicate as already existing, or skip duplicates with a clear message. It must not silently replace an installed skill with another repo's skill of the same name.

## Project Manifest

`sv` tracks installed skill origins in a project-local manifest:

```text
.pi/skills/.sv-manifest.toml
```

For each installed skill, the manifest records:

- skill name
- source repo ID
- normalized source repo URL
- source path within the repo, usually `skills/<name>`
- parsed name snapshot
- parsed description snapshot

The manifest is updated only after a copy or sync succeeds. Failed copies must not leave the manifest claiming a skill was installed or updated.

The manifest is kept outside individual skill folders so copied third-party skill contents remain unchanged.

## Sync Behavior

`sv sync` refreshes configured source caches, builds the valid catalog, and synchronizes project skills.

For manifest-tracked skills:

1. Find the catalog entry matching the recorded skill name and repo ID.
2. If found, safely replace the local skill folder from that source.
3. If not found, skip the skill and print a clear message.

For old untracked skills already present under `.pi/skills`:

- if exactly one configured repo has a valid skill with that folder name, sync it and backfill the manifest
- if multiple repos have that skill name, skip it and print the possible repos
- if no repo has that skill name, skip it as local-only

Sync replacement remains safe: copy to a temporary sibling first, then replace the target folder only after the copy succeeds.

`sv sync` never deletes local skills that are absent from source repositories.

## Update Behavior

`sv update` is a convenience command for users who want source caches and installed skills current.

It performs exactly two high-level operations:

1. update all configured source caches with Git clone or `git pull --ff-only`
2. run the same project sync flow used by `sv sync`

It prints both phases clearly so users understand whether failures happened during source update or project skill sync.

## Git Behavior

For each configured repo:

- if the cache is missing, clone it into `~/.sv/sources/<repo-id>/repo`
- if the cache exists and is a Git clone with the expected remote, run `git pull --ff-only`
- if the cache exists but is not a Git clone, report a clear error
- if the cache remote does not match the configured repo URL, report a clear error

Commands that need fresh source state use the update behavior. `sv add -l` may continue to skip pulls for speed only if that behavior remains explicit and safe; otherwise, consistency should take priority over this optimization in the multi-source implementation.

## Error Handling and User Output

User-facing errors should stay concise and actionable.

Important messages:

- no repos configured, if the default source is disabled in the future
- no valid skills found in configured repos
- invalid skill folders skipped because of missing or malformed `SKILL.md`
- duplicate skill add prompt when multiple repos match
- non-TTY duplicate add requiring qualified form
- tracked skill skipped because its source repo or valid catalog entry is missing
- untracked skill skipped because multiple repos match

Invalid source skills should not make the entire command fail unless all requested skills are invalid or missing. For list commands, invalid entries can be omitted from the main table and summarized separately if useful.

## Testing Strategy

Use unit tests with temporary directories and fake Git runners where possible. Use local temporary Git repositories for integration-style CLI tests.

Core test cases:

- default config still yields the default source repo
- old single `repo = "..."` config loads as a one-repo config
- `sv repo add` stores normalized repos with stable IDs
- `sv repo remove` removes by repo ID without touching cache or project skills
- `sv repo list` prints repo IDs, URLs, and cache paths
- source cache paths are per-repo
- valid `SKILL.md` metadata parses name and description
- missing `SKILL.md` is invalid
- missing required frontmatter fields are invalid
- folder/frontmatter name mismatch is invalid
- `sv list` prints a table with skill, repo, and description
- `sv add <skill>` installs directly when exactly one repo matches
- `sv add <skill>` prompts when multiple repos match in a TTY path
- `sv add owner/repo:skill` installs the qualified match
- `sv add -l` receives repo-aware catalog entries and installs selected origins
- project manifest is written after successful add
- manifest-tracked sync updates from the recorded repo
- old untracked skill backfills when exactly one repo matches
- old untracked skill is skipped when multiple repos match
- invalid source skills are not installed by `add all`, `sync`, or `update`
- `sv update` pulls all source repos and then runs sync

## Migration Notes

Existing users with no config continue to get the default source repo.

Existing users with the old single-repo config continue to work because the loader interprets `repo = "..."` as a one-item repo list.

Existing project skills without a manifest continue to work as normal Pi skills. They become managed by `sv` only when sync/update can backfill their origin uniquely.

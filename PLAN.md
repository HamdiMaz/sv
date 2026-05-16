# sv Feature Roadmap and First Implementation Plan

Date: 2026-05-15

This document captures the shared understanding from discovery for the next major `sv` work. It is intentionally written as a reviewable plan before implementation.

`sv` means **Skills Vault** and is heavily inspired by `uv`: fast defaults, good interactive UX, scriptable escape hatches, and generated state files that keep the ecosystem easy to manage.

## Current project context

From the current codebase:

- `sv` is a Python CLI package under `src/sv`.
- It currently requires Python 3.14+.
- It has Pi-only project skill support through `.pi/skills`.
- It stores configured source repos in `~/.sv/config.toml`.
- It caches source repositories as full Git clones under `~/.sv/sources/<repo-id>/repo`.
- It builds catalogs by scanning only `skills/<skill>/SKILL.md` in those clones.
- It tracks project skill origins in `.pi/skills/.sv-manifest.toml`.
- It already has repo-aware `sv list`, `sv add`, `sv add -l`, `sv repo add/list/remove`, `sv sync`, `sv update`, and `sv run`.
- The interactive selector is currently Unix-terminal oriented and not yet a full cross-platform table UI.
- The repository branch is currently `master`, while the desired model is `dev -> protected main`.

## Master roadmap

### Epic 1: Discovery, indexing, vault mode, state, and TTY UX

This is the first detailed implementation target.

It includes:

- Lightweight source discovery without full clones.
- `.sv/index.toml` consumption and generation.
- `sv init` for creating skill-vault repositories.
- Full skill-vault behavior.
- Global source-state manifest.
- Project-shared manifest.
- Hash-based `sv update` vs force-reset `sv sync` semantics.
- Orphan skill tracking.
- `sv status`.
- Full interactive TTY list/search/repo browsing/picker experience.

Explicitly out of this first implementation target:

- Cross-agent adapter support beyond preserving current Pi behavior.
- Public installer/release work.
- API documentation generation and governance files.
- Branch protection changes.

### Epic 2: Agent adapter research and phased support

Research each agent's real project-skill folder model and isolation flags before implementation.

Target candidates:

- Pi
- Claude
- Codex
- Gemini
- Copilot CLI
- Cursor/Windsurf
- opencode
- cline
- devin
- jules
- amp

Desired future behavior:

- `sv add <skill> --agent <name>` for scripts.
- If no known target folder exists, show an interactive agent picker and create the chosen folder.
- If multiple supported target folders exist, ask where to install.
- Use the shared `.sv/manifest.toml` project manifest for all agents.
- `sv run <agent-name> ...args` forwards all args after the agent name.
- `sv run` with no agent:
  - if exactly one supported agent folder exists, run that agent;
  - if multiple or none exist, show an interactive picker.
- `sv run <agent>` must fail loudly if the agent cannot truly isolate project skills from global/user skills.

### Epic 3: Quality hardening

- Lower Python floor to 3.11+.
- Officially support macOS, Linux, and Windows.
- Add/expand cross-platform CI.
- Resolve the terminal UI dependency decision with a short spike comparing stdlib, prompt-toolkit/Rich-style options, and the project goals:
  - cross-platform UX;
  - tiny install;
  - fast delivery.
- Adopt Ruff format/check with line length 88.
- Move toward very strict linting.
- Add security/performance checks where useful.

### Epic 4: Installer, release, docs, and governance

- Publish to PyPI and GitHub.
- Provide curl and PowerShell installers using `uv tool install` under the hood.
- Document one-line install commands plus inspect-first alternatives.
- Stable channel only initially.
- Add `sv doctor`.
- If Git is missing during an interactive command that needs Git, prompt to install Git or show official manual installation guidance.
- Use common package managers when available: `winget`, `brew`, `apt`, `dnf`, `pacman`, `zypper`.
- Non-TTY missing-Git behavior prints instructions and exits.
- Rename `master` to `main`.
- Use a long-lived `dev` branch and PR merge `dev -> main`.
- Protect `main` with required CI and no force-push.
- Add Makefile targets for check/test/build, dev install, and release prep.
- Generate API docs with `pdoc`.
- Commit generated API docs under `docs/api/`.
- Publish GitHub Pages from main `/docs`.
- CI should fail if generated API docs are stale.
- Add `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, and Apache-2.0 `LICENSE`.
- Strengthen README quick start.

## First implementation target: layered foundation

Use a layered implementation strategy:

1. Backend/catalog/state foundations.
2. Index and init/vault behavior.
3. Update/sync/status/remove semantics.
4. Interactive TTY UX on top.

Avoid a big rewrite. Keep existing tests passing while migrating behavior behind clearer abstractions.

## Source setup and repository configuration

### First-run behavior

When no global repo config exists:

- In a TTY, prompt the user with pluggable recommended sources.
- The exact recommended source list is release-time configuration. Do not hardcode `HamdiMaz/Skills` as the permanent default.
- In non-TTY, fail helpfully and show `sv repo add <repo>` guidance.

When config exists with:

```toml
repos = []
```

that explicitly disables all sources.

### Repo paths

`sv repo add` should support repeated explicit paths for deep generic repos:

```bash
sv repo add owner/repo --skills-path packages/agents/pi/skills --skills-path tools/skills
```

These paths are stored in config and scanned in addition to the default bounded discovery paths.

`sv repo list` should not show indexed/generic mode by default. Keep mode details hidden unless later exposed through status/debug UX.

## Lightweight discovery model

### Discovery order

For each configured source repo:

1. Try reading `.sv/index.toml` at repo root.
2. If present and supported, trust it for `sv list` and `sv search`.
3. If absent, treat the repo as generic and use bounded discovery.
4. If a repo used to have an index but the file disappears, fall back to generic discovery next time. Do not treat this as a hard error.

### Generic fallback discovery

Do not recursively scan every repo for consumers.

Default bounded discovery checks only:

```text
skills/
*/skills/
```

Plus configured `--skills-path` roots.

Deep layouts such as:

```text
packages/agents/pi/skills/find-docs/SKILL.md
```

must be handled by either:

- the repo publishing `.sv/index.toml`, or
- the user adding explicit `--skills-path` config.

### Validation

`sv list` and `sv search` should only show valid skills.

For indexed repos:

- Trust `.sv/index.toml` first for list/search speed.
- During add/sync/update, validate the selected materialized skill folder before installing or replacing local files.

For generic fallback discovery:

- Fetch only candidate `SKILL.md` files during discovery.
- Validate:
  - frontmatter exists;
  - `name` exists;
  - `description` exists;
  - `name` matches the skill folder;
  - path is safe.

Do not download full skill folders during list/search.

## Backend order

For each source, use the lightest available backend:

1. GitHub via `gh api` when available/authenticated.
2. Direct GitHub HTTPS API fallback.
3. Treeless partial Git.
4. Blobless sparse Git.
5. Never silently full-clone.

If all lightweight methods fail, fail with a helpful message. Do not full-clone without a future explicit opt-in design.

Private GitHub repos:

- Prefer `gh auth` / `gh api` when available.
- If GitHub API auth/rate limits fail, fall back to lightweight Git so normal Git/SSH auth can work.
- Explain that `gh auth login` or `GH_TOKEN` improves API limits.

Rate limits:

- On API rate limit, try Git fallback.
- If fallback also fails, show an actionable auth/rate-limit message.

`sv list` and `sv search` refresh remote metadata by default, but only through these lightweight paths.

## Download/materialization model

During:

```bash
sv add <skill>
sv update
sv sync
```

only the selected skill folder is materialized.

For GitHub API sources:

- Download selected folder files directly into an operation temp directory.

For Git backends:

- Use sparse/partial checkout or archive-style extraction for the selected folder only.

Then:

1. Validate safe paths and `SKILL.md`.
2. Compute local content hash.
3. Atomically install/replace.
4. Update manifests only after file operations succeed.

Do not persistently cache full skill folders by default.

## `.sv/index.toml`

### Purpose

`.sv/index.toml` is the ecosystem file that makes skill repos easy to discover without expensive scans.

It is generated by:

- `sv init`;
- `sv index`;
- `sv add` / `sv remove` / `sv update` / `sv sync` when `.sv/index.toml` already exists.

Normal users who only manage local project skills do not need an index. If `.sv/index.toml` does not exist, normal project skill operations should not create it implicitly.

### Path

```text
.sv/index.toml
```

### Kind

The index includes a kind:

```toml
kind = "skill-vault"
```

or:

```toml
kind = "project-index"
```

Meanings:

- `skill-vault`: a repo intended primarily to collect/share skills.
- `project-index`: a normal project that has opted into publishing an index of valid local skills.

### Schema versioning

The file must include a schema version.

If `sv` is old and sees a future unsupported schema, it should tell the user to update `sv` instead of throwing a scary low-level error.

If `sv` is new and sees an old schema, it should read it and migrate/transform when safely possible.

### Suggested shape

Exact field names can evolve during implementation, but the file should contain this information:

```toml
schema_version = 1
kind = "skill-vault"
generated_by = "sv"
generated_at = "2026-05-15T00:00:00Z"

[[skills]]
name = "find-docs"
description = "Retrieves up-to-date documentation, API references, and examples."
source_path = "skills/find-docs"
content_hash = "sha256:..."
skill_file_hash = "sha256:..."
```

Optional fields may include source commit/tree metadata when known.

### `sv index`

`sv index` can run in any Git repo.

It:

- recursively scans for valid `SKILL.md` files;
- skips common build/cache directories;
- includes known agent project skill folders such as `.pi/skills`;
- includes manually-created valid skills even when they are not manifest-managed;
- includes multiple entries with the same skill name if their `source_path` differs;
- warns and skips invalid skills;
- supports include/exclude path flags and config equivalents;
- writes `.sv/index.toml`;
- updates README only when `kind = "skill-vault"`.

Default skipped directories include:

```text
.git
.sv
.venv
node_modules
dist
build
__pycache__
```

and other common cache/build directories.

## Global source-state manifest

Path:

```text
~/.sv/manifest.toml
```

Purpose:

- Track source catalog state, not every project installation.
- Store per-repo last refresh metadata.
- Store backend/index/catalog hashes as needed.
- Help list/search/sync/update detect remote changes efficiently.

It should include a schema version and the same forward/backward compatibility behavior described for `.sv/index.toml`.

## Project-shared manifest

Path:

```text
.sv/manifest.toml
```

This replaces the Pi-only manifest as the canonical project/vault manifest.

Existing legacy manifests at:

```text
.pi/skills/.sv-manifest.toml
```

must still be read and migrated on the next write.

The manifest tracks **manifest-managed skills**, which is the same as **sv-managed local skills**.

Suggested tracked fields per entry:

- skill name;
- target kind: project agent folder or skill-vault root skill;
- target agent, when applicable;
- target path;
- source repo ID;
- source repo URL;
- source path;
- source backend;
- source commit/tree/hash metadata;
- installed source content hash;
- current local content hash when last checked;
- description snapshot;
- orphan state;
- modified/update-available state.

Manual/unmanaged user skill folders are never removed by bulk sv operations.

## `sv update` vs `sv sync`

### `sv update`

`sv update` fetches source metadata and updates local sv-managed skills only when local files have not been modified.

Algorithm:

1. Refresh source metadata.
2. For each manifest-managed local skill:
   - if source is missing, keep local files and mark orphan;
   - if source exists and remote hash is unchanged, skip;
   - if local content hash differs from last installed/synced hash, keep local edits and mark modified/update-available when relevant;
   - if local content hash matches last installed/synced hash and remote changed, download and replace with the new source version.
3. Refresh `.sv/index.toml` if it already exists.
4. Refresh README generated block if `kind = "skill-vault"`.

`sv update` must not overwrite local user edits.

### `sv sync`

`sv sync` means: restore sv-managed local skills to latest source state.

Algorithm:

1. Refresh source metadata.
2. For each manifest-managed local skill:
   - if source is missing, keep local files and mark orphan;
   - if source exists, download latest source folder and replace local folder, even if local edits exist.
3. Clear modified/update-available state after successful replacement.
4. Refresh `.sv/index.toml` if it already exists.
5. Refresh README generated block if `kind = "skill-vault"`.

`sv sync` is the command users run when they want to discard local edits and get back to the source version.

## Orphan skills

A skill becomes orphaned when it is manifest-managed but its recorded source skill no longer exists.

Rules:

- Never delete orphan local files automatically.
- Mark orphan state in `.sv/manifest.toml`.
- Show orphan state in `sv status` and relevant remove/update/sync output.
- If the same repo/path/name reappears later, automatically reattach it and resume normal update/sync behavior.

## `sv status`

`sv status` behavior depends on context.

Context detection:

- Find nearest Git root.
- Inspect `.sv/index.toml` and `.sv/manifest.toml` at that root.

Inside a normal project repo:

- Show installed sv-managed skills.
- Include agent/target path, source, local modified state, remote update availability, and orphan state.

Inside a `kind = "skill-vault"` repo:

- Show root vault skills.
- Include source, local edits, update availability, orphan state, index freshness, and README generated-block freshness.

Outside any Git/project context:

- Show global source status from `~/.sv/manifest.toml`: configured repos, last refresh, backend/index state, and cached catalog health.

## `sv init`

Command:

```bash
sv init
sv init folder-name
```

Behavior:

- Create the target folder if it does not exist.
- Initialize Git if the target is not already a Git repo.
- Create `skills/`.
- Create `.sv/index.toml` with `kind = "skill-vault"`.
- Create `.sv/manifest.toml` as needed.
- Create README with sv-generated skill table markers.
- Do not create a sample skill.

README generated section markers:

```md
<!-- sv:skills:start -->
<!-- sv:skills:end -->
```

The generated table includes:

| Skill | Description |
| --- | --- |

Only the content between markers is replaced. User-written README content outside the markers is preserved.

## Skill-vault mode

A skill-vault repo is a Git repo whose `.sv/index.toml` has:

```toml
kind = "skill-vault"
```

In skill-vault mode:

- `sv add <skill>` installs into root `skills/<skill>` instead of an agent folder.
- `sv remove <skill>` removes from root `skills/<skill>`.
- `sv update` updates unchanged vault skills from source and skips locally edited skills.
- `sv sync` force-resets vault skills from source.
- Existing target paths prompt for replacement in TTY.
- Non-TTY replacement requires `--replace`.
- Replacement warns when local edits are detected.
- After add/remove/update/sync, regenerate `.sv/index.toml` and update the README generated block.

Manual valid skills in root `skills/` are included by `sv index` even if they are not manifest-managed.

## Normal project mode during first implementation

Cross-agent support is deferred.

For now:

- Preserve current Pi target behavior for normal project installs.
- Store canonical state in `.sv/manifest.toml`.
- Read/migrate legacy `.pi/skills/.sv-manifest.toml`.
- Do not create `.sv/index.toml` automatically unless it already exists.
- If `.sv/index.toml` exists, refresh it after add/remove/update/sync.

## Interactive TTY experience

### General rules

In a TTY, these commands become interactive by default:

- `sv list`
- `sv search <query>`
- `sv repo list`
- `sv repo -l`

In non-TTY, they print plain/scriptable output.

Interactive tables:

- Use headers and aligned columns.
- Do not use numeric row prefixes.
- Selectable pickers use checkboxes only.
- Read-only tables use highlighted current row.
- Long descriptions truncate to one line.
- Enter opens details where appropriate.
- Footer shows range/count and key help.
- `/` filters rows.
- `q` cancels/goes back.

Before implementing this fully, run the planned terminal UI dependency spike and choose the UI approach deliberately.

### `sv list`

TTY:

- Shows one row per source entry.
- Columns: Skill, Source, Description.
- Source normally shows repo only.
- If two rows have the same skill and source repo but different source paths, show path information to distinguish them.
- Enter opens details.
- TTY details should focus on browsing, not copy-paste commands.

Non-TTY:

- Print plain table.
- Include exact scriptable references where needed.

### `sv search <query>`

Searches configured sources by:

- skill name;
- description;
- repo ID;
- source path.

Matching:

- case-insensitive text matching;
- lightweight fuzzy ranking.

TTY:

- Interactive filtered table.
- Enter shows details.

Non-TTY:

- Plain ranked results.

### `sv add <skill>`

If exactly one valid source matches, add it.

If multiple sources match:

- TTY: show interactive repo/path/description chooser.
- Non-TTY: fail with choices and exact scriptable references/flags.

Normal users should not be forced to type paths for duplicate resolution.

For same-repo same-name duplicates, interactive choices must show the full source path.

### `sv add -l`

TTY multi-select table picker:

- Checkboxes only.
- No numeric prefixes.
- Slash filtering.
- Footer help.

If selected entries contain duplicate skill names:

- Resolve inline before copying anything.
- Show repo, path, and description.
- User chooses one source per duplicate skill name.

### `sv add --all`

`sv add all` is removed as special behavior. `all` is just a normal skill name.

Supported:

```bash
sv add --all
sv add --all --repo <repo-id>
```

For all-source installs:

- TTY duplicate names resolve inline.
- Non-TTY duplicate names fail with choices.

Installing all skills from one repo should also be available through the interactive repo skill view.

### `sv remove -l`

Interactive multi-select skill removal with confirmation.

Rows should show:

- Skill;
- agent/target path;
- source/orphan status.

Never remove unmanaged/manual skills through this flow unless the user explicitly selects a manifest-managed entry that points to that target.

### `sv remove --all`

Removes only manifest-managed / sv-managed local skills.

It must not remove manual/unmanaged skill folders.

TTY:

- Show the managed skills that will be removed.
- Ask for confirmation.

Non-TTY:

- Require an explicit confirmation flag such as `--yes`.

After removal:

- Refresh `.sv/index.toml` if it exists.
- Refresh README generated block if `kind = "skill-vault"`.

### `sv repo list` and `sv repo -l`

`sv repo -l` is an exact alias for `sv repo list`.

TTY:

- Opens interactive repo browser.
- Enter on a repo shows that repo's skills.
- Repo skill view supports:
  - install one;
  - install all;
  - filter skills;
  - back to repos.

Non-TTY:

- Print plain configured repo table.

### `sv repo remove -l`

Interactive multi-select repo removal with confirmation.

### `svx <repo>`

Add a separate console script:

```bash
svx <repo>
```

Equivalent to:

```bash
sv repo add <repo>
```

Do not add `sv add repo <repo>` as an alias.

## Command changes summary

| Command | Change |
| --- | --- |
| `sv init [folder]` | New. Creates a skill-vault repo, Git repo if needed, `skills/`, `.sv/index.toml`, `.sv/manifest.toml`, and README markers. |
| `sv index` | New. Recursively scans valid skills and writes `.sv/index.toml`. |
| `sv list` | TTY interactive by default; non-TTY plain output. Uses lightweight discovery. |
| `sv search <query>` | New. Searches configured source catalog with fuzzy/text matching. |
| `sv add <skill>` | Uses lightweight discovery/materialization. Duplicate choices are interactive in TTY. |
| `sv add -l` | Full table multi-select picker with filtering and duplicate resolution. |
| `sv add --all` | Kept. Duplicate resolution in TTY. Supports `--repo`. |
| `sv add all` | Removed as special alias; `all` becomes a normal skill name. |
| `sv remove -l` | Multi-select removal with confirmation. |
| `sv remove --all` | New. Removes only manifest-managed skills, with confirmation. |
| `sv repo list` | TTY repo browser; non-TTY plain table. |
| `sv repo -l` | New alias for `sv repo list`. |
| `sv repo remove -l` | New interactive multi-select repo removal. |
| `sv update` | New semantics: update unchanged skills only, preserve local edits. |
| `sv sync` | New semantics: force reset sv-managed skills to latest source. |
| `sv status` | New. Context-aware project/vault/global status. |
| `svx <repo>` | New console script alias for `sv repo add <repo>`. |

## Testing and acceptance criteria

### Discovery and backend tests

- Indexed GitHub source lists skills without cloning the full repo.
- Generic GitHub source discovers only `skills/` and `*/skills/` by default.
- Explicit `--skills-path` discovers deep skills.
- Missing index falls back to generic discovery.
- API rate-limit/auth failures fall back to Git backends.
- Full clone is never used silently.
- Listing/searching fetches only metadata/SKILL.md, not full skill folders.
- Adding/syncing materializes only selected skill folders.

### Index tests

- `sv init` creates `.sv/index.toml` with `kind = "skill-vault"`.
- `sv index` recursively includes valid `SKILL.md` files.
- Invalid skills warn and skip.
- Duplicate names with different paths are included.
- Build/cache dirs are skipped.
- Known agent skill dirs are included.
- Manual valid skills are included.
- Existing unsupported future schema tells user to update sv.
- Older schema is read/migrated when possible.

### Manifest/state tests

- New project manifest is `.sv/manifest.toml`.
- Legacy `.pi/skills/.sv-manifest.toml` is read and migrated on next write.
- Global source state is `~/.sv/manifest.toml`.
- Manifest updates happen only after successful file operations.
- Managed vs unmanaged skills are distinguished.

### Update/sync/orphan tests

- `sv update` updates unchanged managed skills.
- `sv update` skips locally modified managed skills.
- `sv update` marks missing source skills as orphan without deleting local files.
- `sv sync` replaces locally modified managed skills from source.
- Orphan skills reattach when same repo/path/name reappears.
- Hashes prevent unnecessary downloads/replacements.

### Vault tests

- `sv init folder-name` creates folder and Git repo if needed.
- Skill-vault `sv add` installs into root `skills/`.
- Skill-vault `sv remove` removes from root `skills/`.
- Existing vault skill prompts for replacement in TTY.
- Non-TTY replacement requires `--replace`.
- README generated block updates only between markers.
- Normal project add does not create `.sv/index.toml` unless it already exists.

### TTY/non-TTY tests

- TTY `sv list`, `sv search`, and `sv repo list` use interactive tables.
- Non-TTY versions print plain output.
- Pickers have checkboxes and no numeric prefixes.
- Slash filtering works in list/search/add/remove/repo views.
- Long descriptions truncate; details open with Enter.
- `sv add -l` resolves selected duplicates inline before copying.
- Non-TTY duplicate add fails with scriptable choices.
- `sv remove -l` confirms selected removals.
- `sv remove --all` removes only manifest-managed skills and requires confirmation/`--yes`.
- `sv repo remove -l` confirms selected repo removals.

## Risks and decisions to revisit during implementation

- The full TTY UX is large. Do the UI dependency spike before committing to the implementation approach.
- The backend abstraction must not compromise existing path/symlink security checks.
- Schema migrations should be conservative: read old formats where safe, but never silently corrupt future/newer formats.
- Trusting `.sv/index.toml` is intentional for speed, but add/sync/update must validate materialized folders before installing.
- `sv update` preserving local edits requires reliable content hashing and clear status output.
- Skill-vault mode changes `sv add` target behavior based on repo kind; command output must make this obvious.

## Checklist mapping

| Requested item | Planned destination |
| --- | --- |
| Remove `sv add all`, keep `sv add --all` | Epic 1 |
| Curl command to install sv | Epic 4 |
| Development branch and protected main branch | Epic 4 |
| `sv repo remove -l` interactive picker | Epic 1 |
| `sv repo -l` same as `sv repo list` | Epic 1 |
| Install all skills from a specific source | Epic 1 via `sv add --all --repo` and repo browser install-all |
| Browse repos interactively and open repo skills | Epic 1 |
| Picker/table UX like screenshot, organized headers | Epic 1 |
| Interactive `sv list` with pagination/footer help | Epic 1 |
| Duplicate selected sources resolved interactively | Epic 1 |
| Duplicate `sv add skill-name` resolved interactively | Epic 1 |
| Cross-platform tool | Epic 3 |
| sv can install Git if missing | Epic 4 via interactive Git prompt/doctor |
| `sv init` for skill repos | Epic 1 |
| `sv add skill-name` agent folder picker | Epic 2 |
| `sv run agent-name` | Epic 2 |
| Distinguish sv-made repos and generic repos | Epic 1 via `.sv/index.toml` kind |
| Generate sv index file | Epic 1 |
| `svx <repo>` alias for repo add | Epic 1 |
| Meaning of sv is Skills Vault, inspired by uv | Docs across epics |
| Orphan skills | Epic 1 |
| Global sv manifest for source updates | Epic 1 |
| `sv search` | Epic 1 |
| Lint file, CONTRIBUTING, Code of Conduct | Epic 4 |
| Preferred code style in lint | Epic 3/4 |
| API reference from code/docstrings | Epic 4 |
| Makefile | Epic 4 |
| Quick start in README | Epic 4 |
| Best possible `sv list` experience | Epic 1 |

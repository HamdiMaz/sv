# PLAN.md Implementation Review

Date: 2026-05-16
Base commit reviewed: `e13f6465483c75f85197df188b6b47d713b53d94`
Scope: uncommitted rework driven by `PLAN.md`, `tasks.json`, and `ralph-loop.sh`.

## Executive decision

**Recommendation: do not commit in this working session.**

The previously identified commit blockers have been addressed in the uncommitted working tree and marked `(FIXED)` below. Per current workflow instructions, leave all changes uncommitted.

## Change inventory

Observed uncommitted scope before this review:

- 31 tracked files modified.
- New source modules: `src/sv/hashing.py`, `src/sv/index.py`, `src/sv/materialization.py`, `src/sv/tomlutil.py`, `src/sv/ui.py`.
- New plan/automation files: `PLAN.md`, `tasks.json`, `ralph-loop.sh`.
- New test files for indexing, status, search, TTY browsing, hashing, materialization, global manifest, vault targets, etc.
- `tasks.json` reports **42 / 42 tasks done** with `passes: true`.

## Verification results

| Command | Result | Notes |
| --- | --- | --- |
| `uv run pytest` | PASS (FIXED) | 958 passed; required 95% coverage reached at **95.01%**. |
| `uv run pytest --no-cov` | PASS | Previously passed with 860 tests before coverage-focused additions. |
| `uv run ruff check .` | PASS (FIXED) | Previously failed on an unused `SvPaths` import; current run passes. |
| `uv run ty check .` | PASS (FIXED) | Previously reported 27 diagnostics are resolved; current run passes. |
| `uv build` | PASS | Built sdist and wheel. |
| `git diff --check` | PASS | No whitespace/conflict-marker issues. |
| `uv run python -m compileall -q src` | PASS | Source compiles. |

Coverage result from the current `uv run pytest` run:

```text
Required test coverage of 95% reached. Total coverage: 95.01%
958 passed
```

## High-impact findings

### 1. Commit gates fail (FIXED)

Severity: **Critical / commit-blocking**

- `pyproject.toml` sets `--cov-fail-under=95`; full `uv run pytest` now passes at 95.01% coverage (FIXED).
- `ruff` previously failed on an unused import at `tests/test_cli_tty_browsing_contracts.py:7` (FIXED).
- `ty` previously failed with 27 diagnostics, including one runtime-adjacent crash path (`src/sv/cli.py:2469`) (FIXED).

### 2. Normal project commands use `cwd` instead of detected repo root (FIXED)

Severity: **High**

Relevant code:

- `src/sv/cli.py:1922` - add uses `adapter.project_skill_dir(cwd)`.
- `src/sv/cli.py:1946` - add-all uses `adapter.project_skill_dir(cwd)`.
- `src/sv/cli.py:2114` / `src/sv/cli.py:2191` - remove paths use `cwd`.
- `src/sv/cli.py:2300` - sync uses `cwd`.
- `src/sv/cli.py:2682` - update uses `cwd`.
- `src/sv/cli.py:2325` - status uses `context.repo_root`, creating inconsistent behavior.

Impact: running `sv add`, `sv sync`, `sv update`, or `sv remove` from a subdirectory of a Git repo can write `.pi/skills` and `.sv/manifest.toml` under that subdirectory, while `sv status` looks at the Git root and will not show the result.

Manual reproduction used during review:

```text
context root /tmp/.../proj
Added Pi skill 'demo' from local/demo to /tmp/.../proj/sub/.pi/skills/demo
root skill exists False
sub skill exists True
root manifest False
sub manifest True
```

Expected behavior for the new repo-aware context model should be consistent: normal project operations should target `context.repo_root`, unless the old cwd behavior is explicitly retained everywhere including status.

### 3. `sv init folder` inside an existing worktree creates a vault that is not detected as a vault (FIXED)

Severity: **High**

Relevant code:

- `src/sv/cli.py:1194` calls `_ensure_init_git_repo(target, ...)`.
- `src/sv/cli.py:1291` returns early when `git rev-parse --is-inside-work-tree` is true.
- `_detect_local_context()` then finds the parent repo root first and ignores the nested folder's `.sv/index.toml`.

Manual reproduction used during review:

```text
Initialized skill-vault repo at /tmp/.../proj/vault
target .git exists False
target index exists True
detected root /tmp/.../proj
detected is vault False
```

This conflicts with `PLAN.md`: `sv init folder-name` should create a skill-vault repo and initialize Git if the target is not already a Git repo.

### 4. Global `sv status` can crash when a global source state has no backend (FIXED)

Severity: **High**

Relevant code:

- `src/sv/manifest.py:24` defines `GlobalSourceState.backend: str | None = None`.
- `src/sv/cli.py:2469` passes `state.backend` directly into `_escape_control_characters()` when `state` exists, even if `backend is None`.

Manual reproduction with a valid `~/.sv/manifest.toml` source entry that omits `backend`:

```text
TypeError: 'NoneType' object is not iterable
  File "src/sv/cli.py", line 2469, in _handle_global_status
    _escape_control_characters(state.backend if state else "unknown"),
```

This should be handled as `state.backend or "unknown"`.

### 5. `sv add --all` does not implement TTY duplicate resolution (FIXED)

Severity: **High**

Relevant code:

- `src/sv/cli.py:1848` starts `_handle_add_all()`.
- `src/sv/cli.py:1857` always calls `_raise_on_duplicate_source_skills(catalog)`.

`PLAN.md` requires:

- TTY `sv add --all` resolves duplicate names inline.
- Non-TTY duplicate installs fail with choices.

Current behavior fails before interactive duplicate resolution. The duplicate-resolution helper exists for `sv add -l`, but not for `sv add --all`.

### 6. Partial source refresh failures can be hidden when at least one repo succeeds (FIXED)

Severity: **High / Important design bug**

Relevant code:

- `src/sv/cli.py:746` defaults `allow_partial_failures=True`.
- `src/sv/cli.py:778-789` only raises on blocking failures when partial failures are disabled.
- `src/sv/cli.py:790-804` raises only if failures exist and there are no entries.

Impact: `list`, `search`, and `add` can operate on an incomplete catalog if one configured repo refreshes and another fails. For `add`, this can produce misleading “not found” behavior or install from a lower-priority/available source while a failed source is silently absent.

At minimum, partial failures should be surfaced to the user. For install-affecting commands, failing closed is safer.

## PLAN alignment review

| PLAN area | Status | Notes |
| --- | --- | --- |
| Versioned TOML helpers | Fixed (FIXED) | Future schema errors exist; new versioned config, canonical project manifest, and global manifest files now reject missing `schema_version`, while legacy single-repo config and legacy Pi manifests remain readable for migration. |
| Safe hashing/materialization | Fixed (FIXED) | Symlink checks, atomic helpers, Git/GitHub subprocess timeouts, GitHub API materialization quotas, and local materialization file-count/byte/depth quotas are now present with regression coverage. |
| Source path-aware catalog | Mostly implemented | Same-name/source-path models exist. |
| Canonical project manifest | Fixed (FIXED) | New `.sv/manifest.toml` support exists, and project add/remove/sync/update operations now target the detected repository root consistently with status. |
| Legacy Pi manifest migration | Fixed (FIXED) | Added subdirectory sync regression coverage proving legacy `.pi/skills/.sv-manifest.toml` origins are read from the detected repo root and migrated into the canonical `.sv/manifest.toml`. |
| Global source manifest | Fixed (FIXED) | Refresh state records the successful lightweight backend and remote index hash, with legacy missing backend values rendered as `unknown` in status output. |
| Explicit `--skills-path` | Fixed (FIXED) | Config validation rejects unsafe paths, and adding `--skills-path` to an already configured repo now merges the new bounded discovery roots instead of ignoring them. |
| `.sv/index.toml` parser/writer | Implemented | Duplicate names with paths are supported. |
| `sv index` scanner | Fixed (FIXED) | Recursive scanner supports repeatable `--include`/`--exclude` path flags plus `.sv/index-config.toml` defaults, with tests covering CLI/config integration and scan filtering. |
| README generated table | Implemented | Marker-only updates are present. |
| `sv init` | Implemented (FIXED) | Nested folder initialization inside an existing worktree now creates a real, detectable skill-vault Git repository; regression tests cover both init and status detection. |
| Vault mode targeting | Partially implemented | Root-vault add/remove exists. Nested init/context bug can make vault mode invisible. |
| Lightweight backend order | Fixed (FIXED) | `source_backends_for_repo()` now keeps GitHub API backends first even when the local cache path already exists, so later refreshes still try `gh api -> HTTPS -> Git`. |
| Bounded generic discovery | Mostly implemented | Default roots and configured roots exist. |
| Materialize selected folders only | Fixed (FIXED) | GitHub API materialization now streams selected-folder files into an operation temporary directory with file-count, depth, entry, and total-byte limits; local materialization is also quota guarded. |
| `sv search` | Implemented | Non-TTY ranked output exists; TTY browser path exists. |
| First-run config behavior | Mostly implemented | Missing config non-TTY errors; TTY prompt is pluggable. |
| Update vs sync semantics | Mostly implemented | Hash/orphan logic exists. There is a trust risk when skipping solely on remote index hash. |
| Orphan tracking | Implemented in reviewed paths | Needs root/cwd fix to be reliable. |
| `sv status` | Fixed (FIXED) | Project/vault/global handlers exist; global source state now reports the recorded backend/index hash and legacy missing backend values as `unknown` without crashing. |
| Duplicate `sv add <skill>` resolution | Implemented (FIXED) | Non-TTY choices exist; TTY duplicate source selection uses the checkbox picker and source-choice tables no longer support numeric row prefixes. |
| `sv add -l` picker | Fixed (FIXED) | Uses checkbox selector with structured Skill/Source/Description columns and aligned headers/rows instead of an unaligned single-column label list. |
| `sv add --all --repo` | Fixed (FIXED) | Repo filtering and TTY duplicate resolution are covered, including same-repo duplicate source paths. |
| Managed removal flows | Fixed (FIXED) | Root/cwd targeting is covered, and `sv remove -l` now uses structured Skill/Target/Source-Status picker columns for the managed-removal TTY flow. |
| `sv repo -l` and `svx` | Implemented | Scripts/help metadata added. |
| Repo-aware `sv run` | Fixed (FIXED) | Running `sv run` from a Git subdirectory now validates the detected repository root's `.pi/skills` directory and passes that absolute skills path to Pi, with regression coverage. |
| TTY list/search/repo browser | Implemented (FIXED) | Read-only table browser exists, the multi-select picker uses aligned Skill/Source/Description rows, and duplicate source choosing now uses the same aligned picker UX. |

## Security and robustness notes

No critical security issue was found in the reviewed areas, and many path/symlink protections are present. These should still be addressed before release-quality work:

1. **HTTP and credential-bearing repo URLs are accepted and persisted. (FIXED)**  
   `src/sv/config.py` now rejects cleartext `http://` URLs and credential-bearing HTTP(S)/SSH URLs during normalization/config load, and `src/sv/source.py` rejects those URLs before invoking Git.

2. **Git/gh subprocesses have timeout/noninteractive controls. (FIXED)**  
   `src/sv/source.py` now runs subprocesses with a bounded timeout and noninteractive Git/GitHub environment (`GIT_TERMINAL_PROMPT=0`, SSH batch mode by default, and `GH_PROMPT_DISABLED=1`) so bad remotes, SSH prompts, or helpers fail instead of hanging CI/non-TTY commands.

3. **GitHub API materialization accumulates all bytes in memory. (FIXED)**  
   `GitHubGhApiBackend` now streams GitHub API materialization into a temporary destination with file-count, depth, and total-byte limits, cleaning the temporary directory on failures to avoid malicious-source DoS.

4. **Update may skip materialization based solely on remote index hash. (FIXED)**  
   `src/sv/project.py:656` trusts `source_hash == baseline_hash`. That is efficient, but a stale or malicious index can suppress updates. This is now documented as a trusted-source/index behavior in `README.md` and `docs/commands.md`.

5. **Terminal sanitization does not handle Unicode bidi/format controls. (FIXED)**  
   C0/C1 controls are escaped, and Unicode format controls (including bidi and zero-width controls) are now rendered as literal `\\uXXXX` escapes before terminal output.

## Positive findings

- Functional tests pass without coverage: `860 passed`.
- Build succeeds.
- New tests cover a broad set of Epic 1 behavior: source discovery, manifests, indexing, status, vault mode, hashing, materialization, and security regressions.
- The implementation uses argument arrays for subprocesses, not `shell=True`.
- Skill names and source-relative paths reject traversal, separators, and C0/C1 control characters in reviewed paths.
- Atomic materialization/rollback helpers are present and generally align with the plan.

## Recommended pre-commit fix list

Do these before committing:

1. Fix coverage or add meaningful tests so `uv run pytest` passes the configured 95% gate. (FIXED)
2. Fix `ruff` (`tests/test_cli_tty_browsing_contracts.py:7`). (FIXED)
3. Fix `ty` diagnostics, especially `src/sv/cli.py:2469`. (FIXED)
4. Make normal project add/remove/sync/update target the same root that status uses, or explicitly restore old cwd semantics everywhere. (FIXED)
5. Fix `sv init folder` inside an existing Git worktree so the target is a real, detectable skill-vault repo. (FIXED)
6. Implement TTY duplicate resolution for `sv add --all`; keep non-TTY failure with scriptable choices. (FIXED)
7. Decide and implement a safe policy for partial source refresh failures. (FIXED)
8. Add `sv index` include/exclude support or explicitly defer it in `PLAN.md`/`tasks.json`. (FIXED)
9. Correct global source state reporting (`backend`, `index_hash`) and the `None` backend crash. (FIXED)
10. Add regression tests for the bugs above. (FIXED)

Then rerun at minimum:

```bash
uv run pytest
uv run ruff check .
uv run ty check .
git diff --check
uv build
```

## Final recommendation

**Do not commit this rework in this working session.** The listed pre-commit issues are marked fixed in the current uncommitted working tree, and the required `pytest`, `ruff`, and `ty` gates now pass. Leave the changes uncommitted per the current workflow instructions.

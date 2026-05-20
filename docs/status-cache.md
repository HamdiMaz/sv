# Design: Fast `sv status` by default

## Summary

Change `sv status` so its default source lookup uses the normal metadata cache policy instead of forcing a source refresh on every run. Fresh cached metadata should make repeated status checks fast. Users who need a current-source check can run `sv status --refresh`; users who need strict offline/cache-only behavior can continue using `sv status --cached`.

## Problem

`sv status` currently passes `CacheMode.FORCE_REFRESH` as its default cache policy. In a GitHub-backed source without a generated `.sv/index.toml`, that causes a full remote discovery every time status runs. On this worktree, default status took about 9 seconds, while `sv status --cached` took about 0.12 seconds.

The current behavior optimizes for up-to-the-moment source state, but it makes a common diagnostic command feel slow. The command name and user expectation are closer to a quick local/project health check, with explicit opt-in for network freshness.

## Goals

- Make repeated `sv status` runs fast when source metadata is already cached and fresh.
- Preserve an explicit command path for current-source checks with `sv status --refresh`.
- Keep `sv status --cached` as strict cache-only behavior that never refreshes sources.
- Preserve existing local status behavior: missing targets, invalid targets, modified local folders, and manifest-only state should still be reported.
- Keep `sv sync` and `sv update` current-source by default; this design only changes `sv status`.

## Non-goals

- Do not redesign GitHub discovery or reduce the number of `gh api` calls during an actual refresh.
- Do not require source repositories to publish `.sv/index.toml`, although indexed sources remain a useful separate optimization.
- Do not change manifest schema or cache file formats.
- Do not change `sv list`, `sv add`, `sv sync`, or `sv update` cache semantics.

## Proposed behavior

### Default `sv status`

Default status should use `CacheMode.NORMAL`:

1. Load project or vault manifest entries as it does today.
2. If there are no managed entries, print the existing no-skills message and do not refresh sources.
3. If all recorded targets are missing or invalid, stay local and do not refresh sources, preserving current behavior.
4. Otherwise, load configured source metadata through the normal cache policy:
   - use cached metadata when it exists and is within the metadata TTL;
   - refresh source metadata when no cache exists or when cached metadata is stale;
   - if a required refresh fails, report the error instead of silently treating stale data as current.
5. Refresh manifest state from the catalog loaded in step 4, then render the same status table as today.

This means default status may not show source changes made after the last fresh cache refresh until the cache expires or the user runs `sv status --refresh`.

### `sv status --refresh`

`--refresh` should retain the current force-refresh semantics:

- always attempt a source metadata refresh before computing source-aware status;
- fail if the refresh fails, rather than falling back to stale cached metadata;
- detect update/orphan state using the refreshed catalog whenever the source backend can provide enough metadata or local source content to compute it.

### `sv status --cached`

`--cached` should keep its current meaning:

- never call Git, GitHub, or source backends;
- require existing cached metadata when source-aware status is needed;
- remain the best option for guaranteed offline/no-network status checks.

## Implementation outline

- In `src/sv/cli.py`, change the status command policy construction from force-refresh default to normal default:

  ```python
  status_policy = _cache_policy_from_args(
      args,
      default_mode=CacheMode.NORMAL,
      allow_stale_on_error=False,
  )
  ```

- Keep passing this policy into `_handle_status` and `_handle_vault_status` unchanged.
- Keep `_status_catalog_if_configured` accepting the supplied policy. It should not create a force-refresh policy unless no policy is passed by an internal caller.
- Do not change `_entries_require_source_status_refresh`; status should still compute source-aware flags when managed entries are available.
- Update user-facing docs that currently say source-aware `sv status` refreshes metadata by default.

## Tests

Update and add contract tests around status cache behavior:

1. Replace or rename `test_status_refreshes_sources_even_when_metadata_cache_is_fresh` so it verifies `sv status --refresh` refreshes even when cache metadata is fresh.
2. Add a default-status test showing fresh cached metadata is used without calling source backends. The test should populate the cache, make the source backend fail if called, run `sv status`, and assert the command succeeds.
3. Add or adjust a test showing default status refreshes when metadata is missing or stale.
4. Keep `test_status_cached_does_not_refresh_source` to prove `--cached` remains stricter than normal status.
5. Run the existing status contract tests plus the full test suite after implementation.

## Documentation updates

Update README and command docs to describe the new contract:

- `sv status` uses fresh cached source metadata when available and refreshes only when metadata is missing or stale.
- `sv status --refresh` performs a current-source check.
- `sv status --cached` never refreshes sources and requires existing cache metadata.
- `sv sync` and `sv update` still refresh sources by default.

## Compatibility and migration

This is a behavior change for users who relied on plain `sv status` to always check the latest remote source state. The migration path is explicit and simple: use `sv status --refresh` when current-source status is required.

The default becomes faster but less immediately fresh within the metadata TTL. This trade-off should be acceptable because status is observational, while mutating commands that update local skills continue to refresh by default.

## Risks

- Users may temporarily miss an update-available or orphan state until cache expiry if they do not use `--refresh`.
- Existing tests and docs encode current-source default semantics and must be updated deliberately.
- A stale cache can make status look healthy even when the source changed recently; command help and docs need to make the freshness model visible.

## Success criteria

- In a project with fresh cached source metadata, `sv status` does not invoke source backends and completes in roughly the same path as `sv status --cached`.
- `sv status --refresh` still detects source changes immediately in the existing update/orphan scenarios.
- `sv status --cached` still performs no source calls.
- Status output format remains unchanged.
- Tests cover the three status freshness modes: normal, refresh, and cached.

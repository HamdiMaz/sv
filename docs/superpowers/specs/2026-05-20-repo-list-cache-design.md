# Repo List Cache Behavior Design

Date: 2026-05-20
Branch: `fix-caching`

## Problem

TTY `sv repo list` shows configured source repos. Pressing Enter on a repo currently opens that repo's skills by directly refreshing source metadata through `_update_sources_and_catalog_from_repos(..., update=True, ...)`. This bypasses the shared metadata cache path used by normal browsing/install commands.

Expected behavior: interactive repo skill browsing should use the same cache semantics as `sv list`, `sv search`, and `sv add` unless the user explicitly requests a refresh.

## Goals

- Make `sv repo list` Enter use fresh cached metadata when available.
- Add `--refresh` and `--cached` support for `sv repo list` and the `sv repo -l` alias.
- Keep `sv repo list` non-TTY repo table behavior unchanged except for accepting cache flags.
- Preserve existing skill-body cache behavior when installing from the nested repo skill browser.
- Add command-level regression tests for the cache behavior.

## Non-goals

- Do not change default refresh-first behavior for `sv sync`, `sv update`, or source-aware `sv status`.
- Do not prefetch all repo catalogs when opening the repo table.
- Do not change the interactive table browser UI controls.

## Design

### Parser

Add the existing cache policy flags to the repo list command path:

- `sv repo list --refresh`
- `sv repo list --cached`
- `sv repo -l --refresh`
- `sv repo -l --cached`

The flags should be accepted only for repo listing/browsing. If a user combines `sv repo --refresh` or `sv repo --cached` with unsupported repo subcommands such as `add` or `remove`, the command should fail with a clear usage error.

### Command flow

Thread a `CachePolicy` from `_handle_repo()` into `_handle_repo_browser()` when the selected action is repo listing.

When the repo browser's Enter callback opens a selected repo, replace the direct refresh call:

```python
_update_sources_and_catalog_from_repos([repo], ..., update=True, ...)
```

with the cache-aware source command path:

```python
_catalog_for_source_command(
    [repo],
    paths,
    git_runner,
    policy=repo_policy,
    record_global_source_state=record_global_source_state,
    lightweight_discovery=True,
)
```

Default policy is normal cache mode, matching `sv list` and `sv add`:

- fresh cached metadata: use cache and do not refresh source metadata;
- missing or expired metadata: refresh lazily;
- refresh failure with stale metadata: warn and use stale metadata;
- `--refresh`: force refresh before showing repo skills;
- `--cached`: use cached metadata only and fail if unavailable.

The nested skill browser continues receiving wrapped `SourceSkill` entries from `_catalog_for_source_command()`, so installing one skill or all skills keeps the existing skill-body cache materialization behavior.

### Documentation

Update command reference/global cache docs to mention that TTY `sv repo list` nested skill browsing follows normal cache behavior and supports `--refresh` / `--cached`.

### Tests

Add regression tests in the CLI TTY/source-command contract area:

1. Populate cache with `sv list`, mutate the source, open `sv repo list` Enter, and assert the nested skill rows still reflect cached metadata.
2. Repeat with `sv repo list --refresh` and assert the newly added source skill appears.
3. Run `sv repo list --cached` without cached metadata and assert the existing cache-only error is reported.
4. Cover at least one alias path (`sv repo -l --refresh` or `sv repo -l --cached`).

## Risks and mitigations

- **Risk:** Cache flags on `sv repo` could be accepted too broadly.
  - **Mitigation:** Validate flags in `_handle_repo()` and reject unsupported repo subcommand combinations.
- **Risk:** Nested install could lose body-cache wrapping.
  - **Mitigation:** Use `_catalog_for_source_command()` rather than calling `get_catalog_with_cache()` directly.
- **Risk:** Existing repo browser tests may rely on live refresh.
  - **Mitigation:** Update tests to explicitly request `--refresh` when they need newly committed source content.

## Success criteria

- `sv repo list` Enter no longer refreshes source metadata when fresh cached metadata exists.
- `sv repo list --refresh` forces a live metadata refresh.
- `sv repo list --cached` never refreshes source metadata.
- Existing `sv list`, `sv search`, `sv add`, `sv status`, `sv sync`, and `sv update` cache semantics remain unchanged.

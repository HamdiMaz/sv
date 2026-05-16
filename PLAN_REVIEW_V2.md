# PLAN.md Implementation Review V2

Date: 2026-05-16
Reviewed HEAD: `8cd6f1b` (`master`)
Scope: current repository implementation of `PLAN.md` Epic 1 / `tasks.json` T001-T042, plus follow-up fixes from `PLAN_REVIEW.md`.

## Executive summary

The main verification gates now pass and the original `PLAN_REVIEW.md` blockers I spot-checked appear fixed. `tasks.json` also reports **42 / 42** tasks done with `passes: true`.

However, I would **not call the Epic 1 implementation fully complete yet**. I found remaining functional, security/robustness, and documentation gaps that are not caught by the current test suite. The most important are:

1. Git sparse fallback metadata cache can be narrowed to only the last discovered skill, making later `sv add -l` / cached catalog reads incomplete. (FIXED)
2. Local path Git sources can silently ignore `--filter`/`--depth`, violating the plan's “never silently full-clone” rule.
3. Unknown/unsafe Git URL schemes such as `git://` and `ext::` are accepted and passed to Git. (FIXED)
4. README and command docs still describe removed or changed behavior (`sv add all`, default `HamdiMaz/Skills`, and destructive `sv update`). (FIXED)
5. Remote metadata/index reads still lack size/entry limits for some backends.

## Verification run

| Command | Result | Evidence |
| --- | --- | --- |
| `uv run pytest` | PASS | `1049 passed`; coverage `95.12%` with configured `--cov-fail-under=95`. |
| `uv run ruff check .` | PASS | `All checks passed!` |
| `uv run ty check .` | PASS | `All checks passed!` |
| `git diff --check` | PASS | exit 0, no output. |
| `uv build` | PASS | Built `dist/sv-0.1.0.tar.gz` and wheel. |
| `uv run python -m compileall -q src` | PASS | exit 0, no output. |
| task completion audit | PASS | `42 42`, no incomplete task ids. |

Working-tree note: `.coverage` is modified after running tests; it was already present as a modified/generated artifact during review.

## PLAN_REVIEW.md follow-up status

The previously reported blockers are broadly addressed in current code:

- Full test, lint, type, build, whitespace, and compile gates pass.
- Normal project operations now use detected project roots consistently with status.
- Nested `sv init folder` is covered and detectable as a vault.
- Global status no longer crashes on missing/`None` backend values.
- `sv add --all` has TTY duplicate-resolution paths.
- Partial source refresh failures fail closed for install-affecting/source-refreshing commands.
- The security fixes listed in `PLAN_REVIEW.md` are mostly present: subprocess timeouts/noninteractive env, URL credential/HTTP rejection, materialization quotas, trusted-index documentation, and terminal control escaping.

## Findings

### High: Git sparse fallback can leave cached metadata incomplete (FIXED)

Evidence:

- `src/sv/source.py:656` `GitSparseSourceBackend.read_file()` calls `_prepare_checkout()` with only the requested `SKILL.md` file.
- `src/sv/catalog.py` generic fallback discovery lists all candidate `SKILL.md` files, then calls `backend.read_file()` for each candidate.
- After the final candidate is read, the sparse checkout is left narrowed to that last file.
- `src/sv/source.py:669` / `src/sv/source.py:685` allow `update=False` catalog reads to trust an existing local metadata checkout when `skills/` exists.
- `src/sv/cli.py:407` uses `update=False` for `sv add -l`.

Manual reproduction result with a non-indexed local Git source containing `alpha` and `beta`:

```text
first update=True: ['alpha', 'beta']
second update=False: ['beta']
cache skill dirs: ['beta']
```

Impact: after `sv list` or a canceled first `sv add -l`, later cached interactive selection can hide valid skills from the same source.

Suggested fix: after generic discovery, restore the sparse checkout to metadata patterns, or make `read_file()` preserve/augment metadata checkout patterns. Add a regression test: source with two generic skills -> refresh once -> cached `update=False` catalog still returns both.

### High: local Git sources silently ignore sparse/partial filters

Evidence:

- `src/sv/source.py:999` creates/fetches lightweight Git caches with `git clone --filter=... --sparse --no-checkout --depth=1`.
- Git ignores `--filter` and `--depth` for ordinary local path clones but returns exit 0.
- `_run_git()` ignores stderr on successful commands, so the warning is hidden.

Manual reproduction of the same Git command shape:

```text
exit=0
stderr=Cloning into '.../cache'...
warning: --depth is ignored in local clones; use file:// instead.
warning: --filter is ignored in local clones; use file:// instead.
done.
```

Impact: this violates `PLAN.md`'s “Never silently full-clone” requirement for local path sources, and can copy/hardlink the full object store without the user knowing.

Suggested fix: for local path sources, avoid `git clone` local optimizations (`--no-local` or `file://` with verified behavior), or implement a direct local metadata/materialization backend. Treat “filter is ignored” / “depth is ignored” warnings as failures when the command is supposed to be lightweight.

### High: unsafe/unknown Git URL schemes are accepted (FIXED)

Evidence:

- `src/sv/config.py:96` `normalize_repo()` returns unknown non-local strings unchanged.
- `src/sv/source.py:1481` `_validate_repo_url()` rejects leading `-`, controls, cleartext HTTP, and credentials, but does not allowlist schemes.
- These values are later passed to `git clone` in `src/sv/source.py:999`.

Manual check:

```text
ACCEPT 'git://github.com/org/repo.git' -> 'git://github.com/org/repo.git'
ACCEPT 'ext::sh -c echo-pwn' -> 'ext::sh -c echo-pwn'
ACCEPT 'foo::bar' -> 'foo::bar'
ACCEPT 'https://github.com/org/repo.git' -> 'https://github.com/org/repo.git'
REJECT 'http://github.com/org/repo.git': repo URL must use HTTPS instead of cleartext HTTP
```

Impact: `git://` is unauthenticated/cleartext-ish from a trust perspective, and `ext::` / remote-helper style forms can become dangerous depending on local Git configuration.

Suggested fix: use a strict source URL allowlist: GitHub shorthand, GitHub HTTPS, vetted SSH forms, and explicit local paths if intended. Reject unknown schemes. Consider setting `GIT_ALLOW_PROTOCOL` for subprocesses.

### High: README/docs are materially stale against Epic 1 behavior (FIXED)

Evidence:

- `README.md:26` still documents a default `HamdiMaz/Skills` source when no config exists. The implementation and plan require TTY prompt / non-TTY helpful failure instead.
- `README.md:123` and `README.md:127` still document `sv add all` as “add every skill”; T034/PLAN removed this special behavior, leaving `all` as a normal skill name.
- `README.md:149-155`, `docs/usage.md:62-67`, and `docs/commands.md:18` still describe `sv update` as equivalent to destructive sync. Epic 1 requires `update` to preserve local edits, while `sync` force-resets.
- `docs/commands.md` omits or under-documents implemented Epic 1 items: `sv init`, `sv status`, `sv remove --all`, `sv repo remove -l`, `sv repo add --skills-path`, and `sv add --all --repo`.

Impact: users following docs will run wrong commands or expect wrong overwrite behavior. This also means T042 (“Polish CLI help and user-facing error contracts”) is only partially satisfied from a docs perspective.

Suggested fix: update README and docs to match current CLI help and Epic 1 semantics. Add doc tests that catch semantic stale examples such as `sv add all`.

### Medium: metadata/index reads are still unbounded for Git and `gh api` backends

Evidence:

- `src/sv/source.py:538` `GitLocalSourceBackend.read_file()` uses `target.read_bytes()` with no size cap.
- `src/sv/source.py:424` `_run_api()` returns captured `gh api` stdout with no output cap; `subprocess.run(capture_output=True)` has already accumulated it in memory.
- `src/sv/catalog.py` then parses full `.sv/index.toml` and every candidate `SKILL.md`.
- GitHub HTTPS responses are capped, and materialization has quotas, but metadata/index paths are not consistently capped across backends.

Impact: a malicious or accidental huge `.sv/index.toml` / `SKILL.md` can cause memory/CPU pressure during `sv list`, `sv search`, or `sv add`, before materialization quotas apply.

Suggested fix: impose max byte limits for index files and candidate `SKILL.md` files across all backends. For `gh`, use a bounded runner/output strategy or reject output over a configured cap before parsing. Add index entry count / field length limits.

### Medium: `sv index --include` can scan through symlinked roots before rejecting

Evidence:

- `src/sv/index.py:245` builds scan roots from include paths and calls `_collect_candidate_skill_files()`.
- `_collect_candidate_skill_files()` uses `os.scandir(path)` and follows a symlinked include root to a directory.
- `scan_repo_for_index()` parses the external `SKILL.md` at `src/sv/index.py:192` before `sha256_skill_directory()` rejects symlinked ancestors at `src/sv/index.py:198`.

Impact: a repo-controlled `.sv/index-config.toml` or CLI include path can make `sv index` read files outside the repository boundary. The entry may be skipped later, but the read already happened.

Suggested fix: reject symlinked include roots and symlinked ancestors before scanning or parsing. Validate `path.resolve().relative_to(root.resolve())` before reading any candidate.

### Medium: `sv status` can fail on remote source refresh even when a project has no managed skills (FIXED)

Evidence:

- `_handle_status()` calls `_status_catalog_if_configured()` before loading local manifest entries.
- `_status_catalog_if_configured()` refreshes configured sources with `allow_partial_failures=False`.

Manual reproduction: empty Git project + configured missing source:

```text
exit=1
error: git-treeless-partial backend failed ... repository '.../missing' does not exist ...
```

Impact: `sv status` cannot report “No sv-managed Pi skills found in this project” if source config is currently unreachable, even though no local state needs remote checks.

Suggested fix: load local manifest/managed entries first. If there are no managed local skills, report that without refreshing sources. Refresh only when update/orphan state needs source comparison.

### Low: TTY browse details still include copy-paste-oriented `Add as`

Evidence:

- `src/sv/cli.py:1955` prints `Add as:` from `_print_source_skill_detail()`.
- `PLAN.md` says TTY details should focus on browsing, not copy-paste commands.

Impact: minor UX mismatch. Non-TTY duplicate sections should keep exact scriptable references; TTY browse detail can omit or de-emphasize this.

## Positive findings

- The implementation has broad test coverage across indexing, manifests, vault mode, update/sync/orphan/status, TTY primitives, security path handling, and command parsing.
- Core gates pass under the configured coverage threshold.
- Canonical `.sv/manifest.toml`, legacy Pi manifest migration, source path-aware catalogs, vault targeting, `sv init`, `sv index`, `sv status`, `sv search`, `sv repo -l`, and `svx` are present.
- Update vs sync behavior appears implemented in code/tests: `update` preserves modified local skills, `sync` force-resets managed skills.
- Subprocess calls use argument arrays and have timeout/noninteractive environment controls.
- Many path/symlink and terminal-control protections are present.

## Recommended fix list before declaring Epic 1 complete

1. Fix sparse Git metadata cache narrowing and add regression coverage. (FIXED)
2. Enforce a strict repo URL/protocol allowlist and set defensive Git protocol environment. (FIXED)
3. Handle local path sources without silently ignoring lightweight clone requirements.
4. Add metadata/index size and entry limits for all backends, including `gh` and Git local cache reads.
5. Reject symlinked `sv index` include roots before scanning/reading.
6. Update README and docs for first-run config, `sv add --all`, `sv update` vs `sv sync`, and missing Epic 1 commands/flags. (FIXED)
7. Make `sv status` avoid remote refresh when no managed local entries exist. (FIXED)
8. Optionally remove `Add as` from TTY browse details or explicitly document that deviation.

## Suggested post-fix verification

Run:

```bash
uv run pytest
uv run ruff check .
uv run ty check .
git diff --check
uv build
uv run python -m compileall -q src
```

Add focused regressions for:

- Generic Git fallback source with two skills: `update=True` catalog then `update=False` catalog still returns both.
- Local source clone does not hide `--filter is ignored` / `--depth is ignored` warnings.
- `normalize_repo()` / source backend rejects `git://`, `ext::`, and unknown schemes.
- Oversized `.sv/index.toml` and oversized `SKILL.md` fail safely before parsing.
- `sv index --include symlink-to-outside` does not read outside repo.
- Empty project `sv status` succeeds even when configured source is unreachable.
- Docs no longer contain `sv add all` as install-all guidance or destructive `sv update` descriptions.

# Changelog

## Unreleased

- Fixed qualified `sv add repo:skill` and missing `sv repo remove` error messages to escape control characters from user input before printing.
- Fixed manifest writes to refuse symlinked temporary manifest files before writing metadata.
- Fixed `sv run` to refuse symlinked project skill paths before launching Pi.
- Fixed source cache handling to reject symlinked cache ancestors and symlinked `.git` metadata paths before running Git.
- Fixed Git and Pi process launch `OSError`s to report user-facing errors instead of tracebacks.
- Fixed Git failure, Git remote URL, and Pi launch error output with terminal control characters to render escaped text instead of raw escape sequences.
- Fixed GitHub `ssh://git@github.com/...` source URLs to derive the same `owner/repo` IDs as HTTPS and scp-style SSH URLs.
- Fixed local repo configuration so relative paths and `~` paths are saved as absolute paths, keeping `sv` commands independent of the current working directory.
- Fixed project and source symlink handling so `sv add`, `sv remove`, and `sv sync` refuse unsafe paths, while catalog loading rejects symlinked source roots and skips symlinked source skill directories.
- Fixed repo URL validation and `git clone` invocation to reject option-like/control-character repo values before calling Git.
- Fixed source skill descriptions with terminal control characters to render escaped text instead of raw escape sequences.
- Documented repo input formats, non-interactive duplicate-skill selection, `sv sync`/`sv update` behavior, Pi runtime requirements, and symlink safety rules.
- Fixed `sv remove` to restore the skill directory if manifest update fails during removal.
- Fixed `SKILL.md` parsing to require a real closing frontmatter delimiter line.
- Fixed ambiguous non-interactive `sv add <skill>` to fail with qualified `repo:skill` guidance instead of silently doing nothing.
- Fixed malformed `~/.sv/config.toml` and `.pi/skills/.sv-manifest.toml` files to report user-facing errors instead of tracebacks.
- Fixed unsafe repo IDs in config and `sv repo add` input to be rejected before they can escape the `~/.sv/sources` cache directory.
- Fixed `sv add all` to stop before copying when multiple source repos provide the same skill folder name.
- Fixed `sv add` to report non-directory target paths, manifest failures, and copy failures as user-facing errors without leaving untracked or partial skill directories.
- Fixed `sv sync` to restore the previous local skill if a late filesystem or manifest update failure occurs during replacement.
- Fixed `sv remove` to validate the manifest before deleting a skill directory.
- Fixed unreadable `SKILL.md` files to report user-facing errors instead of raw tracebacks.
- Tightened config and manifest validation to reject non-string fields, escape control characters when writing TOML, write manifests atomically, and wrap read/write filesystem failures.
- Tightened skill name validation to reject leading-dot folder names and colons that cannot be synced or referenced consistently.
- Documented source repo configuration, cache refresh behavior, and sync/update overwrite semantics.
- Fixed `sv remove` to delete the removed skill's `.sv-manifest.toml` entry while preserving other recorded origins.
- Fixed `sv repo remove` so removing the last configured repo leaves an explicit empty repo list instead of restoring the default repo.
- Fixed `sv add -l` to open faster by using cached source repos without pulling when the cache already exists.
- Expanded the README with installation, quick-start, storage layout, cache-refresh, and troubleshooting guidance.
- Added `sv update` to refresh configured source repo caches and sync project skills in one command.
- Added `sv repo add`, `sv repo remove`, and `sv repo list` for multiple source repositories.
- Changed `sv list` and `sv add -l` to show repo-aware skill metadata from parsed `SKILL.md` files.
- Added project skill origin tracking in `.pi/skills/.sv-manifest.toml` for reliable multi-repo sync.
- Added validation for source skills requiring `SKILL.md` frontmatter with matching `name` and non-empty `description`.
- Added `sv remove -l` / `sv remove --list` to remove project skills from an interactive list.
- Added `sv add -l` / `sv add --list` to choose multiple skills from an interactive scrolling list.
- Added ←/→ pagination in the `sv add -l` picker.
- Added `sv add all` and `sv add --all` to install every non-conflicting skill from configured source repos.

## v0.1.0

- Initial release of `sv`, a project-local AI agent skill manager for Pi.
- Added commands to list, add, sync, run, and configure skills.
- Added Git-backed skill source support with project-local `.pi/skills` installation.
- Added tests covering CLI, configuration, project paths, and source behavior.

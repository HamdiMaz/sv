# Changelog

## Unreleased

- Added `sv remove -l` / `sv remove --list` to remove project skills from an interactive list.
- Added `sv add -l` / `sv add --list` to choose multiple skills from an interactive scrolling list.
- Made `sv add -l` open faster by using the cached source clone when available.
- Added ←/→ pagination in the `sv add -l` picker.
- Changed `sv list` to show numbered skills.
- Added `sv add all` and `sv add --all` to install every skill from the configured source repo.

## v0.1.0

- Initial release of `sv`, a project-local AI agent skill manager for Pi.
- Added commands to list, add, sync, run, and configure skills.
- Added Git-backed skill source support with project-local `.pi/skills` installation.
- Added tests covering CLI, configuration, project paths, and source behavior.

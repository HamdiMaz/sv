# `sv init` root guardrail design

## Goal

Prevent `sv init` from accidentally creating a nested skill-vault inside an existing Git or sv-managed repository, while keeping the convenient standalone `sv init <folder>` workflow outside repositories.

## Command behavior

- `sv init` without a folder resolves the nearest enclosing repository context and initializes that root.
  - If run from a subdirectory of an existing Git repository, it targets the Git repository root.
  - If run from a subdirectory of an existing non-Git sv-managed root, it targets that sv root.
  - If no existing Git or sv root is found, it targets the current directory.
- `sv init <folder>` is allowed only when the current directory is not inside an existing Git or sv-managed repository.
  - Outside repositories, it keeps the existing behavior and initializes `cwd / folder` as a standalone skill-vault.
  - Inside repositories, it fails before creating the target folder or running `git init`.

## Implementation approach

Add a small init-target resolution helper before the existing scaffold steps:

1. Detect the nearest enclosing Git root or sv-managed root from `cwd`.
2. For bare `sv init`, return that root if present; otherwise return `cwd`.
3. For `sv init <folder>`, reject if an enclosing root is present; otherwise return `cwd / folder`.
4. Pass the resolved target into the existing `_prepare_init_target`, `_ensure_init_git_repo`, skills directory, index, README, and manifest setup flow.

This keeps the change focused on target selection. Existing protections for symlinked targets, invalid `.git` metadata, existing manifests, non-vault indexes, README marker validation, and Git failures stay unchanged.

## Error handling

When `sv init <folder>` is run inside an existing repository, print an actionable error explaining that nested skill-vault initialization is refused. The message should tell users to run bare `sv init` to target the existing repository root, or run `sv init <folder>` from outside a repository to create a standalone vault.

## Tests

Add or update coverage for these cases:

- Bare `sv init` from a Git subdirectory initializes the Git root and does not create nested state under the subdirectory.
- Bare `sv init` from an existing non-Git sv root subdirectory targets that sv root.
- `sv init <folder>` inside a Git repository fails, does not create the folder, and does not call `git init`.
- `sv init <folder>` inside an sv-managed repository fails, does not create the folder, and does not call `git init`.
- `sv init <folder>` outside repositories still creates a standalone skill-vault.

## Documentation updates

Update the command reference and related release notes to remove wording that advertises nested vault creation. Document the new rule: bare `sv init` targets the repository root when run from inside one, and folder initialization is for creating standalone vaults outside existing repositories.

# sv Skill Manager Design

Date: 2026-05-08

## Purpose

`sv` is a command-line tool for managing project-local skills for AI coding agents. The MVP focuses only on Pi. It lets a user keep a public Git repository of skills, copy selected skills into a project, refresh project skills from the source repo, and launch Pi with only those project skills enabled.

The initial default source repository is:

```text
https://github.com/HamdiMaz/Skills.git
```

Users can replace that default globally with `sv config repo <repo>`.

## MVP Scope

In scope:

- Pi-only project skill management.
- Git-backed source repository.
- Global sv directory at `~/.sv`.
- Direct writes into the native Pi project folder `.pi/skills`.
- Commands: `list`, `add`, `sync`, `run`, and `config`.
- No skill-structure validation beyond copying folders that exist under the source repo's `skills/` directory.

Out of scope for the MVP:

- Claude, Codex, Gemini, or other agent adapters.
- A remote marketplace or registry service.
- GitHub API integration.
- Private repository token management beyond whatever `git` already supports.
- Skill version pinning, lockfiles, or semver.
- Offline mode beyond using an already-cloned repo when no update is attempted in a future enhancement.
- Validation that copied skills contain `SKILL.md`.

## Source Repository Model

The skill source is a Git repository with this layout:

```text
repo/
  skills/
    github-release/
      ...
    find-docs/
      ...
```

`sv list` enumerates immediate directories under `skills/*`. It does not inspect or validate their contents.

Supported repo input forms:

- `https://github.com/user/repo.git`
- `git@github.com:user/repo.git`
- `user/repo`, normalized to `https://github.com/user/repo.git`

The configured source is cloned into:

```text
~/.sv/sources/default/repo/
```

Global config lives at:

```text
~/.sv/config.toml
```

## Project Model

The current working directory is the project root for the MVP. sv writes Pi skills directly to:

```text
.pi/skills/<skill-name>/
```

If `.pi/skills` does not exist, sv creates it automatically.

No manifest is used in the MVP. `sv sync` determines managed updates by scanning local `.pi/skills/*` and comparing those folder names with source repo `skills/*` folder names.

## Commands

### `sv list`

Behavior:

1. Ensure `git` is available.
2. Clone the configured source repo into `~/.sv/sources/default/repo` if missing.
3. If already cloned, update it with `git pull --ff-only`.
4. List immediate directory names under `~/.sv/sources/default/repo/skills/*`.

If the source repo has no `skills` directory or no skill folders, print a clear empty-state message.

### `sv add <skill>`

Behavior:

1. Ensure the source repo is cloned and updated.
2. Look for `~/.sv/sources/default/repo/skills/<skill>`.
3. If it does not exist, print that the skill was not found in the configured source.
4. Create `.pi/skills` if needed.
5. If `.pi/skills/<skill>` already exists, print a friendly "skill already exists" message and exit successfully without overwriting.
6. Copy the full source skill directory into `.pi/skills/<skill>`.

`sv add` does not validate that the skill contains `SKILL.md`.

### `sv sync`

Behavior:

1. Ensure the source repo is cloned and updated.
2. If `.pi/skills` does not exist, print "no Pi skills found to sync" and exit successfully.
3. Scan immediate local directories under `.pi/skills/*`.
4. For each local skill name that also exists under source `skills/*`, replace the local folder with the latest source copy.
5. Leave local skills that do not exist in the source repo untouched.

`sv sync` is the update command for skills already added to a project. It does not delete unknown local skills.

### `sv run [-- <pi args...>]`

Behavior:

Launch Pi with global skill discovery disabled and project skills enabled:

```bash
pi --no-skills --skill .pi/skills <pi args...>
```

This is the MVP mechanism for "force only project skills." Isolation is guaranteed only when the user starts Pi through `sv run`.

### `sv config repo <repo>`

Behavior:

Store the configured source repo in `~/.sv/config.toml`. Shorthand `user/repo` is normalized to `https://github.com/user/repo.git`.

Changing the repo should cause subsequent commands to use the new configured source. If the existing clone belongs to a different remote, sv should report the mismatch and tell the user to remove `~/.sv/sources/default/repo` or use a future reset command.

### `sv config show`

Behavior:

Print the effective configuration, including:

- source repo URL
- source clone path
- Pi project skill path for the current directory

## Git Behavior

When the source clone is missing:

```bash
git clone <repo-url> ~/.sv/sources/default/repo
```

When the source clone exists:

```bash
git pull --ff-only
```

If `git` is not installed, sv prints a clear error that Git is required.

If clone or pull fails, sv prints the failing operation and the relevant path. For pull failures caused by local changes, corruption, or remote mismatch, sv suggests removing `~/.sv/sources/default/repo` and rerunning the command.

## Agent Adapter Shape

The MVP is Pi-only, but the implementation should keep Pi-specific paths and launch behavior behind a small adapter boundary:

```text
AgentAdapter
  name = "pi"
  project_skill_dir(project_root) = project_root/.pi/skills
  run_command(project_root, extra_args) = ["pi", "--no-skills", "--skill", ".pi/skills", ...extra_args]
```

Future adapters can add Claude, Codex, or other agents without changing the source-repo and copying logic.

## Safety and Data Handling

- `sv add` never overwrites an existing project skill.
- `sv sync` only replaces local skills whose names exist in the source repo.
- `sv sync` never deletes local skills that are absent from the source repo.
- No project manifest or lockfile is created in the MVP.
- The source clone under `~/.sv` is treated as sv-managed data.
- Project skills under `.pi/skills` are normal project files and may be committed by the user.

## Testing Strategy

Use unit tests around isolated temporary directories and fake command execution where practical.

Core test cases:

- repo shorthand normalization
- default config resolution
- creation of `~/.sv` paths
- `sv list` lists immediate `skills/*` directories only
- `sv add` creates `.pi/skills` and copies a skill directory
- `sv add` existing skill exits successfully without overwrite
- `sv add` missing skill reports not found
- `sv sync` updates matching local skills
- `sv sync` leaves unknown local skills untouched
- `sv sync` with no `.pi/skills` exits successfully
- `sv run` constructs `pi --no-skills --skill .pi/skills` with forwarded args

Integration tests can use a local temporary Git repository as the source so tests do not depend on GitHub or network access.

## Future Enhancements

Likely next steps after the MVP:

- `sv source reset` to remove/reclone the global source.
- Multiple named sources.
- Project-level source override.
- Optional cache/offline behavior controls.
- Skill validation once the expected structure is better understood.
- Claude/Codex adapters.
- Manifest or lockfile support if reproducibility becomes important.
- Registry or marketplace UI on top of Git sources.

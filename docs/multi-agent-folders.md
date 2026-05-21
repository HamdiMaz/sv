# Multi-agent project folders

## Goal

Support installing sv-managed skills into one of three project-local agent folders:

| Agent option | Folder | Skills directory |
| --- | --- | --- |
| Pi | `.pi` | `.pi/skills` |
| Claude | `.claude` | `.claude/skills` |
| Agents | `.agents` | `.agents/skills` |

`sv` should ask for the target agent only when it cannot infer one, then remember the project default so repeated commands do not prompt again.

## Project default

The selected default agent is stored in the canonical project manifest:

```toml
schema_version = 1
default_agent = "pi"

[[skills]]
name = "find-docs"
target_kind = "project-agent"
target_agent = "pi"
target_path = ".pi/skills/find-docs"
```

`default_agent` is optional for backward compatibility. Valid values are `pi`, `claude`, and `agents`.

`sv default` manages this field:

- `sv default` shows the current project default and supported values.
- `sv default pi|claude|agents` sets the project default.

Changing the default does not migrate existing skills. It only changes which agent future project commands target.

## Target resolution

For normal projects, commands resolve the active agent in this order:

1. If `.sv/manifest.toml` contains `default_agent`, use it.
2. Otherwise, inspect supported project folders: `.pi`, `.claude`, `.agents`.
3. If exactly one supported folder exists, use that agent and write it as `default_agent`.
4. If multiple supported folders exist, ask the user to choose interactively.
5. If no supported folders exist, ask the user to choose interactively.
6. Persist any interactive choice as `default_agent`.
7. If a choice is required in a non-TTY shell, fail with a hint to run `sv default <agent>`.

Interactive choices should use the same highlighted terminal UI style as `sv list`. Options are shown as agent names: `Pi`, `Claude`, and `Agents`.

Skill-vault repositories keep their existing behavior and install to `skills/<name>`.

## Command behavior

Project commands operate only on the resolved/default agent target:

- `sv add <skill>` installs into the default agent's skills directory.
- `sv add -l` and `sv add --all` install selected skills into the default agent's skills directory.
- `sv remove <skill>`, `sv remove -l`, and `sv remove --all` remove sv-managed skills only from the default agent.
- `sv status` reports sv-managed skills only for the default agent.
- `sv sync` and `sv update` operate only on sv-managed skills for the default agent.

Example:

```bash
sv default pi
sv add find-docs
# writes .pi/skills/find-docs

sv default claude
sv add brainstorming
# writes .claude/skills/brainstorming
# existing .pi/skills/find-docs remains untouched
```

## Manifest model

Existing manifest fields already support multiple project agents:

- `target_kind = "project-agent"`
- `target_agent = "pi" | "claude" | "agents"`
- `target_path = ".<agent>/skills/<skill>"`

Legacy manifests without `default_agent` and existing Pi entries remain valid. Legacy `.pi/skills/.sv-manifest.toml` loading remains Pi-only for backward compatibility.

## Error handling

- Unsupported default values in `.sv/manifest.toml` fail with a clear manifest error.
- Non-TTY commands that need agent selection fail before mutating files.
- Symlink checks continue to reject symlinked agent folders, skills directories, or managed skill folders.
- Existing project skill overwrite rules remain unchanged: project adds do not replace existing skill directories.

## Testing

Add coverage for:

- Default resolution from manifest.
- Inference and persistence when exactly one supported folder exists.
- Interactive selection when multiple or no supported folders exist.
- Non-TTY failure when selection is required.
- `sv default` show/set behavior.
- Installing into `.claude/skills` and `.agents/skills`.
- Default-only behavior for status, remove, sync, and update.
- Backward compatibility for existing `.pi/skills` manifests.

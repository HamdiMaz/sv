# sv adapter architecture

`sv` keeps pure domain logic as plain functions and places side effects behind small adapters.

## Adapter modules

| Adapter | Module | Owns | Does not own |
| --- | --- | --- | --- |
| UI/terminal | `sv.ui` | Plain tables, TTY selection, TTY table browsing façade | Business decisions about which command to run |
| Runtime/environment | `sv.runtime` | cwd, home, env, streams, prompts, terminal width, current time | Command parsing |
| Concurrency | `sv.parallel` | worker-count policy, ordered worker execution | Final manifest/cache writes |
| Process execution | `sv.process` | subprocess execution, noninteractive env, Pi process launcher | Source backend selection |
| Source backends | `sv.source_backends` | GitHub, HTTPS, local Git, sparse Git source access | Project install policy |
| Filesystem/materialization | `sv.materialization` | safe copy, validation, install, replace, remove, rollback | Manifest semantics |
| Persistence stores | `sv.stores` | config and manifest store façades | TOML schema definitions |
| Cache | `sv.source_cache` | catalog cache, skill body cache, cache-aware materializers | Source provider implementation |

## Compatibility policy

Existing public functions remain available during adapter migration. New code should import adapter modules directly; compatibility modules such as `sv.source` continue to re-export old names for tests and downstream users.

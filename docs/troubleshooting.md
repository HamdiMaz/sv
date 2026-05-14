# Troubleshooting sv

Use this page when `sv` output is surprising or you are not sure which command to run next.

## `No skill source repos configured.`

Your global config exists and contains no repos, usually after removing the last source with `sv repo remove`. `sv list` and `sv repo list` both print this message instead of an empty table.

Add a source repo before listing or adding skills:

```bash
sv repo add HamdiMaz/Skills
# or your team's source repo
sv repo add SomeOrg/TeamSkills
```

## Duplicate skill names

A Pi project can only contain one `.pi/skills/<name>` directory. If two source repos provide the same skill name, `sv list` keeps the main skill table compact and adds a separate `Duplicate skill names` section. Copy the qualified `Add as` value for the source you want.

Install the exact source you want:

```bash
sv add <repo-id>:<skill>
```

If the skill already exists from a different source, `sv add` leaves it unchanged and tells you the current source and the requested source. Remove it first if you intentionally want to switch:

```bash
sv remove <skill>
sv add <repo-id>:<skill>
```

## Tables look wrapped

`sv` wraps tables to your terminal width so descriptions, repo IDs, and cache paths stay visible in terminals and CI logs. If the output is too narrow to scan comfortably, widen your terminal or set `COLUMNS` for one command:

```bash
COLUMNS=120 sv list
```

Wide Unicode characters are counted by display width so rows should stay within the reported terminal width.

## The same source appears twice

If `~/.sv/config.toml` repeats the exact same repo, lists the same normalized URL under more than one repo ID, or mixes equivalent GitHub URL forms (HTTPS, SSH, optional `.git`), `sv` keeps the first entry and treats later IDs as aliases while loading skills. Existing sync metadata or qualified commands that point at a duplicate repo ID are still matched to the kept source. Use `sv repo list` to inspect the active sources; config rewrites preserve duplicate-source aliases so legacy `repo:skill` references keep working without listing the same skill twice.

## Interactive commands fail in scripts

`sv add -l` and `sv remove -l` require an interactive terminal with usable terminal settings. If your shell, editor, or pseudo-terminal cannot provide those settings, `sv` reports a user-facing terminal error instead of a traceback. In scripts, use explicit commands instead:

```bash
sv add find-docs
sv add HamdiMaz/Skills:find-docs
sv remove find-docs
```

## Source caches are stale

`sv list`, `sv add <skill>`, `sv add --all`, `sv sync`, and `sv update` refresh source repos before reading them. `sv add -l` opens quickly by using the current cache when it already exists. Run `sv list` first when you want to refresh before opening the picker.

# sv Multi-Source Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add multi-repo source management, repo-aware skill listing/adding, `SKILL.md` validation, project origin manifests, and `sv update`.

**Architecture:** Keep the CLI thin and move core behavior into focused modules: config owns global repo state, source owns Git cache updates, skills/catalog own metadata parsing and catalog entries, manifest owns project origin tracking, project owns file copy/sync safety, and table/selector own display. Source-backed commands build one catalog from configured repos and pass catalog entries through add/sync flows so repo origin is preserved.

**Tech Stack:** Python 3.14 stdlib (`argparse`, `dataclasses`, `pathlib`, `subprocess`, `shutil`, `tomllib`, `hashlib`, `re`), pytest, uv, existing Pi adapter.

---

## Scope Check

The approved spec covers one cohesive change set: multi-source repositories, metadata parsing, repo-aware UX, manifest-backed sync, and `update`. These pieces are dependent because `update` needs multi-source cache refresh and manifest-backed sync, and repo-aware add/list need the parsed catalog. Keep them in one implementation plan.

## File Structure

- Modify `src/sv/config.py` — multi-repo config model, repo normalization, repo ID derivation, cache path resolution, config read/write, repo add/remove helpers.
- Modify `src/sv/source.py` — keep single-repo Git operations and add a helper to clone/pull all configured repos.
- Create `src/sv/skills.py` — parse and validate `SKILL.md` frontmatter.
- Create `src/sv/catalog.py` — build valid source skill entries from configured cached repos.
- Create `src/sv/table.py` — small deterministic plain-text table formatter.
- Modify `src/sv/selector.py` — allow display labels for non-string objects while preserving current remove selection behavior.
- Create `src/sv/manifest.py` — read/write `.pi/skills/.sv-manifest.toml` with installed skill origins.
- Modify `src/sv/project.py` — add/sync project skills from catalog entries and update manifest only after successful copy/replace.
- Modify `src/sv/cli.py` — add `sv repo`, `sv update`, multi-source list/add/sync behavior, duplicate-choice handling, and repo-aware output.
- Modify `README.md` — document `sv repo`, repo-aware tables, `sv update`, manifest behavior, and valid `SKILL.md` requirements.
- Modify `CHANGELOG.md` — add Unreleased bullets for the shipped behavior.
- Modify `tests/test_config.py` — multi-repo config and migration tests.
- Modify `tests/test_source.py` — multi-source Git update path tests.
- Create `tests/test_skills.py` — `SKILL.md` parsing tests.
- Create `tests/test_catalog.py` — catalog building and invalid skill skipping tests.
- Create `tests/test_table.py` — table formatting tests.
- Modify `tests/test_selector.py` — label callback tests.
- Create `tests/test_manifest.py` — manifest read/write tests.
- Modify `tests/test_project.py` — add/sync manifest and backfill tests.
- Modify `tests/test_cli.py` — repo command and parser tests.
- Modify `tests/test_cli_source_commands.py` — end-to-end multi-repo CLI behavior with local Git repos.

---

### Task 1: Multi-Repo Config and `sv repo` Commands

**Files:**
- Modify: `src/sv/config.py`
- Modify: `src/sv/cli.py`
- Test: `tests/test_config.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing config tests**

Replace the config expectations in `tests/test_config.py` with tests that cover default repos, old config migration, repo ID derivation, add/remove, and per-repo cache paths:

```python
from pathlib import Path

import pytest

from sv.config import (
    DEFAULT_REPO,
    RepoConfig,
    SvConfig,
    SvPaths,
    add_repo,
    derive_repo_id,
    load_config,
    normalize_repo,
    remove_repo,
)


def test_normalize_repo_accepts_github_shorthand():
    assert normalize_repo("HamdiMaz/Skills") == "https://github.com/HamdiMaz/Skills.git"


def test_normalize_repo_keeps_https_url():
    url = "https://github.com/HamdiMaz/Skills.git"
    assert normalize_repo(url) == url


def test_normalize_repo_keeps_ssh_url():
    url = "git@github.com:HamdiMaz/Skills.git"
    assert normalize_repo(url) == url


def test_normalize_repo_rejects_empty_value():
    with pytest.raises(ValueError, match="repo cannot be empty"):
        normalize_repo("   ")


def test_derive_repo_id_uses_owner_repo_for_github_forms():
    assert derive_repo_id("HamdiMaz/Skills") == "HamdiMaz/Skills"
    assert derive_repo_id("https://github.com/HamdiMaz/Skills.git") == "HamdiMaz/Skills"
    assert derive_repo_id("git@github.com:HamdiMaz/Skills.git") == "HamdiMaz/Skills"


def test_derive_repo_id_uses_safe_hashed_id_for_local_paths(tmp_path: Path):
    repo = tmp_path / "skill-source"

    repo_id = derive_repo_id(str(repo))

    assert repo_id.startswith("local-skill-source-")
    assert "/" not in repo_id
    assert " " not in repo_id


def test_paths_include_sources_root_and_per_repo_cache(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)

    assert paths.sv_home == tmp_path / ".sv"
    assert paths.config_file == tmp_path / ".sv" / "config.toml"
    assert paths.sources_dir == tmp_path / ".sv" / "sources"
    assert paths.source_repo_for("HamdiMaz/Skills") == (
        tmp_path / ".sv" / "sources" / "HamdiMaz" / "Skills" / "repo"
    )


def test_load_config_uses_default_repo_when_config_missing(tmp_path: Path):
    config = load_config(SvPaths.from_home(tmp_path))

    assert config == SvConfig(
        repos=(RepoConfig(id="HamdiMaz/Skills", url=DEFAULT_REPO),)
    )


def test_load_config_reads_old_single_repo_config(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text('repo = "https://github.com/SomeOrg/TeamSkills.git"\n')

    config = load_config(paths)

    assert config == SvConfig(
        repos=(
            RepoConfig(
                id="SomeOrg/TeamSkills",
                url="https://github.com/SomeOrg/TeamSkills.git",
            ),
        )
    )


def test_add_repo_writes_multi_repo_config_without_duplicates(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)

    first = add_repo(paths, "HamdiMaz/Skills")
    second = add_repo(paths, "https://github.com/SomeOrg/TeamSkills.git")
    duplicate = add_repo(paths, "HamdiMaz/Skills")

    assert first.status == "added"
    assert second.status == "added"
    assert duplicate.status == "exists"
    assert load_config(paths).repos == (
        RepoConfig(id="HamdiMaz/Skills", url="https://github.com/HamdiMaz/Skills.git"),
        RepoConfig(
            id="SomeOrg/TeamSkills",
            url="https://github.com/SomeOrg/TeamSkills.git",
        ),
    )
    assert paths.config_file.read_text() == (
        '[[repos]]\n'
        'id = "HamdiMaz/Skills"\n'
        'url = "https://github.com/HamdiMaz/Skills.git"\n'
        '\n'
        '[[repos]]\n'
        'id = "SomeOrg/TeamSkills"\n'
        'url = "https://github.com/SomeOrg/TeamSkills.git"\n'
    )


def test_remove_repo_writes_remaining_repos(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    add_repo(paths, "HamdiMaz/Skills")
    add_repo(paths, "SomeOrg/TeamSkills")

    removed = remove_repo(paths, "HamdiMaz/Skills")

    assert removed.id == "HamdiMaz/Skills"
    assert load_config(paths).repos == (
        RepoConfig(
            id="SomeOrg/TeamSkills",
            url="https://github.com/SomeOrg/TeamSkills.git",
        ),
    )


def test_remove_repo_reports_missing_repo(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)

    with pytest.raises(ValueError, match="Repo 'missing/repo' is not configured"):
        remove_repo(paths, "missing/repo")
```

- [ ] **Step 2: Write failing CLI repo command tests**

Append these tests to `tests/test_cli.py`:

```python

def test_repo_add_and_list_use_multi_repo_config(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    assert handle(parse(["repo", "add", "HamdiMaz/Skills"]), cwd=project, home=home) == 0
    assert handle(parse(["repo", "add", "SomeOrg/TeamSkills"]), cwd=project, home=home) == 0
    capsys.readouterr()

    exit_code = handle(parse(["repo", "list"]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Repo" in output
    assert "URL" in output
    assert "Cache" in output
    assert "HamdiMaz/Skills" in output
    assert "https://github.com/HamdiMaz/Skills.git" in output
    assert "SomeOrg/TeamSkills" in output


def test_repo_add_reports_existing_repo(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    assert handle(parse(["repo", "add", "HamdiMaz/Skills"]), cwd=project, home=home) == 0
    capsys.readouterr()
    exit_code = handle(parse(["repo", "add", "HamdiMaz/Skills"]), cwd=project, home=home)

    assert exit_code == 0
    assert "already configured" in capsys.readouterr().out


def test_repo_remove_updates_config_without_deleting_project_skills(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    skill = project / ".pi" / "skills" / "alpha"
    skill.mkdir(parents=True)

    assert handle(parse(["repo", "add", "SomeOrg/TeamSkills"]), cwd=project, home=home) == 0
    capsys.readouterr()

    exit_code = handle(parse(["repo", "remove", "SomeOrg/TeamSkills"]), cwd=project, home=home)

    assert exit_code == 0
    assert skill.is_dir()
    assert "Removed repo SomeOrg/TeamSkills" in capsys.readouterr().out


def test_config_command_is_removed_from_parser():
    with pytest.raises(SystemExit):
        parse(["config", "show"])
```

Add `import pytest` near the top of `tests/test_cli.py` if it is not already present.

- [ ] **Step 3: Run config and CLI tests to verify failure**

Run:

```bash
uv run pytest tests/test_config.py tests/test_cli.py -v
```

Expected: FAIL because `RepoConfig`, multi-repo helpers, and `sv repo` parser commands do not exist yet.

- [ ] **Step 4: Implement multi-repo config**

Update `src/sv/config.py` with this structure. Preserve `DEFAULT_REPO` and `normalize_repo`; replace single-repo config storage with multi-repo storage:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import re
import tomllib

DEFAULT_REPO = "https://github.com/HamdiMaz/Skills.git"

_GITHUB_SHORTHAND = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GITHUB_HTTPS = re.compile(
    r"^https?://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_GITHUB_SSH = re.compile(
    r"^git@github\.com:(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$"
)
_SAFE_ID_PART = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class RepoConfig:
    id: str
    url: str


@dataclass(frozen=True)
class RepoChangeResult:
    repo: RepoConfig
    status: str


@dataclass(frozen=True)
class SvPaths:
    sv_home: Path
    config_file: Path
    sources_dir: Path

    @classmethod
    def from_home(cls, home: Path | None = None) -> "SvPaths":
        user_home = Path.home() if home is None else home
        sv_home = user_home / ".sv"
        return cls(
            sv_home=sv_home,
            config_file=sv_home / "config.toml",
            sources_dir=sv_home / "sources",
        )

    @property
    def source_repo(self) -> Path:
        return self.source_repo_for(derive_repo_id(DEFAULT_REPO))

    def source_repo_for(self, repo_id: str) -> Path:
        return self.sources_dir.joinpath(*repo_id.split("/"), "repo")


@dataclass(frozen=True)
class SvConfig:
    repos: tuple[RepoConfig, ...] = (
        RepoConfig(id="HamdiMaz/Skills", url=DEFAULT_REPO),
    )

    @property
    def repo(self) -> str:
        return self.repos[0].url


def normalize_repo(repo: str) -> str:
    value = repo.strip()
    if not value:
        raise ValueError("repo cannot be empty")
    if value.startswith(("https://", "http://", "git@", "ssh://", "file://")):
        return value
    if _GITHUB_SHORTHAND.fullmatch(value):
        return f"https://github.com/{value}.git"
    return value


def derive_repo_id(repo: str) -> str:
    value = repo.strip()
    if _GITHUB_SHORTHAND.fullmatch(value):
        return value

    normalized = normalize_repo(value)
    for pattern in (_GITHUB_HTTPS, _GITHUB_SSH):
        match = pattern.fullmatch(normalized)
        if match:
            return f"{match.group('owner')}/{match.group('repo')}"

    return _fallback_repo_id(normalized)


def load_config(paths: SvPaths) -> SvConfig:
    if not paths.config_file.exists():
        return SvConfig()

    with paths.config_file.open("rb") as file:
        data = tomllib.load(file)

    if "repos" in data:
        repos = tuple(
            RepoConfig(id=str(item["id"]), url=str(item["url"]))
            for item in data.get("repos", [])
        )
        return SvConfig(repos=repos or SvConfig().repos)

    repo = str(data.get("repo", DEFAULT_REPO))
    normalized = normalize_repo(repo)
    return SvConfig(repos=(RepoConfig(id=derive_repo_id(repo), url=normalized),))


def add_repo(paths: SvPaths, repo: str) -> RepoChangeResult:
    normalized = normalize_repo(repo)
    repo_config = RepoConfig(id=derive_repo_id(repo), url=normalized)
    config = load_config(paths)

    for existing in config.repos:
        if existing.id == repo_config.id:
            return RepoChangeResult(repo=existing, status="exists")

    _save_config(paths, SvConfig(repos=(*config.repos, repo_config)))
    return RepoChangeResult(repo=repo_config, status="added")


def remove_repo(paths: SvPaths, repo_id: str) -> RepoConfig:
    config = load_config(paths)
    remaining = tuple(repo for repo in config.repos if repo.id != repo_id)
    if len(remaining) == len(config.repos):
        raise ValueError(f"Repo '{repo_id}' is not configured")

    removed = next(repo for repo in config.repos if repo.id == repo_id)
    _save_config(paths, SvConfig(repos=remaining))
    return removed


def _save_config(paths: SvPaths, config: SvConfig) -> None:
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for index, repo in enumerate(config.repos):
        if index:
            lines.append("")
        lines.append("[[repos]]")
        lines.append(f'id = "{_toml_escape(repo.id)}"')
        lines.append(f'url = "{_toml_escape(repo.url)}"')
    paths.config_file.write_text("\n".join(lines) + "\n")


def _fallback_repo_id(value: str) -> str:
    stem = Path(value.rstrip("/")).name or "repo"
    if stem.endswith(".git"):
        stem = stem[:-4]
    safe_stem = _SAFE_ID_PART.sub("-", stem).strip("-._") or "repo"
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    return f"local-{safe_stem}-{digest}"


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
```

- [ ] **Step 5: Implement `sv repo` parser and handlers**

In `src/sv/cli.py`, change imports from config to:

```python
from sv.config import SvPaths, add_repo, load_config, remove_repo
from sv.table import format_table
```

Create `src/sv/table.py` now with a minimal implementation so repo listing can use it:

```python
from __future__ import annotations

from collections.abc import Sequence


def format_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    all_rows = [tuple(headers), *(tuple(row) for row in rows)]
    widths = [max(len(row[index]) for row in all_rows) for index in range(len(headers))]

    def format_row(row: Sequence[str]) -> str:
        return "  ".join(value.ljust(widths[index]) for index, value in enumerate(row)).rstrip()

    header = format_row(headers)
    underline = "  ".join("-" * width for width in widths).rstrip()
    body = [format_row(row) for row in rows]
    return "\n".join([header, underline, *body])
```

In `build_parser()`, replace the `config` parser block with:

```python
    repo_parser = subparsers.add_parser("repo", help="Manage global skill source repos.")
    repo_subparsers = repo_parser.add_subparsers(dest="repo_command", required=True)
    repo_add_parser = repo_subparsers.add_parser("add", help="Add a skill source repo.")
    repo_add_parser.add_argument("repo")
    repo_remove_parser = repo_subparsers.add_parser("remove", help="Remove a skill source repo.")
    repo_remove_parser.add_argument("repo_id")
    repo_subparsers.add_parser("list", help="List configured skill source repos.")
```

In `handle()`, replace the `config` branch with:

```python
        if args.command == "repo":
            return _handle_repo(args, paths=paths)
```

Add this handler in `src/sv/cli.py`:

```python
def _handle_repo(args: argparse.Namespace, paths: SvPaths) -> int:
    if args.repo_command == "add":
        try:
            result = add_repo(paths, args.repo)
        except ValueError as exc:
            raise SvError(str(exc)) from exc
        if result.status == "exists":
            print(f"Repo {result.repo.id} is already configured.")
        else:
            print(f"Added repo {result.repo.id} ({result.repo.url})")
        return 0

    if args.repo_command == "remove":
        try:
            removed = remove_repo(paths, args.repo_id)
        except ValueError as exc:
            raise SvError(str(exc)) from exc
        print(f"Removed repo {removed.id}")
        return 0

    if args.repo_command == "list":
        config = load_config(paths)
        rows = [
            [repo.id, repo.url, str(paths.source_repo_for(repo.id))]
            for repo in config.repos
        ]
        print(format_table(["Repo", "URL", "Cache"], rows))
        return 0

    raise SvError(f"Unknown repo command: {args.repo_command}")
```

Remove `_handle_config()` and all uses of `save_repo`.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
uv run pytest tests/test_config.py tests/test_cli.py -v
uv run ruff check src tests
```

Expected: PASS for config and CLI tests; ruff reports no issues.

Commit:

```bash
git add src/sv/config.py src/sv/cli.py src/sv/table.py tests/test_config.py tests/test_cli.py
git commit -m "feat: add multi-repo config commands"
```

---

### Task 2: `SKILL.md` Parsing and Source Catalog

**Files:**
- Create: `src/sv/skills.py`
- Create: `src/sv/catalog.py`
- Modify: `src/sv/source.py`
- Test: `tests/test_skills.py`
- Test: `tests/test_catalog.py`
- Test: `tests/test_source.py`

- [ ] **Step 1: Write failing `SKILL.md` parser tests**

Create `tests/test_skills.py`:

```python
from pathlib import Path

import pytest

from sv.errors import SvError
from sv.skills import SkillMetadata, parse_skill_file


def write_skill(path: Path, text: str) -> Path:
    skill_dir = path / "alpha"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(text)
    return skill_file


def test_parse_skill_file_reads_name_and_description(tmp_path: Path):
    skill_file = write_skill(
        tmp_path,
        "---\nname: alpha\ndescription: Alpha skill.\n---\n\n# Alpha\n",
    )

    metadata = parse_skill_file(skill_file, expected_folder="alpha")

    assert metadata == SkillMetadata(name="alpha", description="Alpha skill.")


def test_parse_skill_file_accepts_quoted_values(tmp_path: Path):
    skill_file = write_skill(
        tmp_path,
        '---\nname: "alpha"\ndescription: "Alpha: skill"\n---\n',
    )

    metadata = parse_skill_file(skill_file, expected_folder="alpha")

    assert metadata == SkillMetadata(name="alpha", description="Alpha: skill")


def test_parse_skill_file_requires_file(tmp_path: Path):
    with pytest.raises(SvError, match="missing SKILL.md"):
        parse_skill_file(tmp_path / "alpha" / "SKILL.md", expected_folder="alpha")


def test_parse_skill_file_requires_frontmatter_at_top(tmp_path: Path):
    skill_file = write_skill(tmp_path, "# Alpha\n---\nname: alpha\n---\n")

    with pytest.raises(SvError, match="frontmatter"):
        parse_skill_file(skill_file, expected_folder="alpha")


def test_parse_skill_file_requires_name_and_description(tmp_path: Path):
    skill_file = write_skill(tmp_path, "---\nname: alpha\n---\n")

    with pytest.raises(SvError, match="description"):
        parse_skill_file(skill_file, expected_folder="alpha")


def test_parse_skill_file_rejects_folder_name_mismatch(tmp_path: Path):
    skill_file = write_skill(
        tmp_path,
        "---\nname: beta\ndescription: Beta skill.\n---\n",
    )

    with pytest.raises(SvError, match="does not match folder"):
        parse_skill_file(skill_file, expected_folder="alpha")


def test_parse_skill_file_rejects_path_like_names(tmp_path: Path):
    skill_file = write_skill(
        tmp_path,
        "---\nname: ../alpha\ndescription: Alpha skill.\n---\n",
    )

    with pytest.raises(SvError, match="Invalid skill name"):
        parse_skill_file(skill_file, expected_folder="alpha")
```

- [ ] **Step 2: Write failing catalog tests**

Create `tests/test_catalog.py`:

```python
from pathlib import Path

from sv.catalog import SourceSkill, build_source_catalog
from sv.config import RepoConfig, SvPaths


def make_skill(repo_path: Path, name: str, description: str) -> None:
    skill_dir = repo_path / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"
    )


def test_build_source_catalog_returns_valid_skills_with_repo_context(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    make_skill(repo_path, "beta", "Beta skill.")
    make_skill(repo_path, "alpha", "Alpha skill.")

    catalog = build_source_catalog([repo], paths)

    assert catalog == [
        SourceSkill(
            name="alpha",
            description="Alpha skill.",
            repo_id="Org/Skills",
            repo_url="https://github.com/Org/Skills.git",
            repo_path=repo_path,
            source_path=repo_path / "skills" / "alpha",
        ),
        SourceSkill(
            name="beta",
            description="Beta skill.",
            repo_id="Org/Skills",
            repo_url="https://github.com/Org/Skills.git",
            repo_path=repo_path,
            source_path=repo_path / "skills" / "beta",
        ),
    ]


def test_build_source_catalog_sorts_by_skill_name_then_repo_id(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    alpha = RepoConfig(id="A/Skills", url="https://github.com/A/Skills.git")
    beta = RepoConfig(id="B/Skills", url="https://github.com/B/Skills.git")
    make_skill(paths.source_repo_for(beta.id), "same", "B skill.")
    make_skill(paths.source_repo_for(alpha.id), "same", "A skill.")

    catalog = build_source_catalog([beta, alpha], paths)

    assert [(entry.name, entry.repo_id) for entry in catalog] == [
        ("same", "A/Skills"),
        ("same", "B/Skills"),
    ]


def test_build_source_catalog_skips_invalid_skills(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    make_skill(repo_path, "valid", "Valid skill.")
    invalid = repo_path / "skills" / "invalid"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text("---\nname: other\ndescription: Bad.\n---\n")

    catalog = build_source_catalog([repo], paths)

    assert [entry.name for entry in catalog] == ["valid"]
```

- [ ] **Step 3: Write failing multi-source Git update tests**

Append this test to `tests/test_source.py`:

```python
from sv.config import RepoConfig, SvPaths
from sv.source import ensure_source_repos


def test_ensure_source_repos_clones_each_configured_repo(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repos = [
        RepoConfig(id="Org/A", url="https://github.com/Org/A.git"),
        RepoConfig(id="Org/B", url="https://github.com/Org/B.git"),
    ]
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(["git", "clone"]),
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(["git", "clone"]),
        ]
    )

    ensured = ensure_source_repos(repos, paths, runner=runner)

    assert ensured == [
        paths.source_repo_for("Org/A"),
        paths.source_repo_for("Org/B"),
    ]
    assert runner.calls == [
        (["git", "--version"], None),
        (["git", "clone", "https://github.com/Org/A.git", str(paths.source_repo_for("Org/A"))], None),
        (["git", "--version"], None),
        (["git", "clone", "https://github.com/Org/B.git", str(paths.source_repo_for("Org/B"))], None),
    ]
```

- [ ] **Step 4: Run parser/catalog/source tests to verify failure**

Run:

```bash
uv run pytest tests/test_skills.py tests/test_catalog.py tests/test_source.py -v
```

Expected: FAIL because `sv.skills`, `sv.catalog`, and `ensure_source_repos` do not exist yet.

- [ ] **Step 5: Implement `SKILL.md` parser**

Create `src/sv/skills.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sv.errors import SvError
from sv.project import normalize_skill_name


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str


def parse_skill_file(skill_file: Path, *, expected_folder: str) -> SkillMetadata:
    if not skill_file.is_file():
        raise SvError(f"Skill folder '{expected_folder}' is missing SKILL.md.")

    text = skill_file.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise SvError(f"Skill '{expected_folder}' must start with frontmatter.")

    try:
        frontmatter = text.split("\n---", 1)[0].removeprefix("---\n")
    except ValueError as exc:
        raise SvError(f"Skill '{expected_folder}' has malformed frontmatter.") from exc

    fields = _parse_frontmatter(frontmatter)
    name = fields.get("name", "").strip()
    description = fields.get("description", "").strip()

    if not name:
        raise SvError(f"Skill '{expected_folder}' frontmatter is missing name.")
    if not description:
        raise SvError(f"Skill '{expected_folder}' frontmatter is missing description.")

    normalized_name = normalize_skill_name(name)
    if normalized_name != expected_folder:
        raise SvError(
            f"Skill frontmatter name '{normalized_name}' does not match folder '{expected_folder}'."
        )

    return SkillMetadata(name=normalized_name, description=description)


def _parse_frontmatter(frontmatter: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        fields[key.strip()] = _unquote(value.strip())
    return fields


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
```

- [ ] **Step 6: Implement source catalog**

Create `src/sv/catalog.py`:

```python
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sv.config import RepoConfig, SvPaths
from sv.errors import SvError
from sv.project import normalize_skill_name
from sv.skills import parse_skill_file


@dataclass(frozen=True)
class SourceSkill:
    name: str
    description: str
    repo_id: str
    repo_url: str
    repo_path: Path
    source_path: Path

    @property
    def source_relative_path(self) -> str:
        return f"skills/{self.name}"

    @property
    def display_label(self) -> str:
        return f"{self.name}  {self.repo_id}  {self.description}"


def build_source_catalog(repos: Iterable[RepoConfig], paths: SvPaths) -> list[SourceSkill]:
    entries: list[SourceSkill] = []
    for repo in repos:
        repo_path = paths.source_repo_for(repo.id)
        skills_root = repo_path / "skills"
        if not skills_root.is_dir():
            continue
        for skill_dir in sorted(skills_root.iterdir(), key=lambda path: path.name):
            if not skill_dir.is_dir():
                continue
            try:
                metadata = parse_skill_file(skill_dir / "SKILL.md", expected_folder=skill_dir.name)
            except SvError:
                continue
            entries.append(
                SourceSkill(
                    name=metadata.name,
                    description=metadata.description,
                    repo_id=repo.id,
                    repo_url=repo.url,
                    repo_path=repo_path,
                    source_path=skill_dir,
                )
            )
    return sorted(entries, key=lambda entry: (entry.name, entry.repo_id))


def find_catalog_matches(catalog: Sequence[SourceSkill], skill: str) -> list[SourceSkill]:
    skill_name = normalize_skill_name(skill)
    return [entry for entry in catalog if entry.name == skill_name]


def find_qualified_catalog_entry(catalog: Sequence[SourceSkill], reference: str) -> SourceSkill | None:
    if ":" not in reference:
        return None
    repo_id, skill = reference.rsplit(":", 1)
    skill_name = normalize_skill_name(skill)
    for entry in catalog:
        if entry.repo_id == repo_id and entry.name == skill_name:
            return entry
    return None
```

- [ ] **Step 7: Implement multi-source Git update helper**

Add this import with the other imports at the top of `src/sv/source.py`:

```python
from sv.config import RepoConfig, SvPaths
```

Then append this helper below `ensure_source_repo()`:

```python
def ensure_source_repos(
    repos: Sequence[RepoConfig],
    paths: SvPaths,
    runner: Runner = default_runner,
    *,
    update: bool = True,
) -> list[Path]:
    repo_paths: list[Path] = []
    for repo in repos:
        repo_path = paths.source_repo_for(repo.id)
        ensure_source_repo(repo.url, repo_path, runner=runner, update=update)
        repo_paths.append(repo_path)
    return repo_paths
```

Keep `ensure_source_repo()` and `list_source_skills()` until CLI/project code no longer calls the old list helper.

- [ ] **Step 8: Run tests and commit**

Run:

```bash
uv run pytest tests/test_skills.py tests/test_catalog.py tests/test_source.py -v
uv run ruff check src tests
```

Expected: PASS for parser, catalog, and source tests; ruff reports no issues.

Commit:

```bash
git add src/sv/skills.py src/sv/catalog.py src/sv/source.py tests/test_skills.py tests/test_catalog.py tests/test_source.py
git commit -m "feat: parse skill metadata catalog"
```

---

### Task 3: Table Formatting and Selector Labels

**Files:**
- Modify: `src/sv/table.py`
- Modify: `src/sv/selector.py`
- Test: `tests/test_table.py`
- Test: `tests/test_selector.py`

- [ ] **Step 1: Write failing table tests**

Create `tests/test_table.py`:

```python
from sv.table import format_table


def test_format_table_aligns_columns():
    output = format_table(
        ["Skill", "Repo", "Description"],
        [
            ["alpha", "Org/A", "Short."],
            ["longer-skill", "Org/B", "Longer description."],
        ],
    )

    assert output.splitlines() == [
        "Skill         Repo   Description",
        "------------  -----  -------------------",
        "alpha         Org/A   Short.",
        "longer-skill  Org/B   Longer description.",
    ]


def test_format_table_handles_no_rows():
    output = format_table(["Repo", "URL"], [])

    assert output.splitlines() == [
        "Repo  URL",
        "----  ---",
    ]
```

- [ ] **Step 2: Write failing selector label test**

Append this test to `tests/test_selector.py`:

```python

def test_render_uses_custom_item_labels():
    state = SelectionState([{"name": "alpha", "repo": "Org/A"}])
    stdout = StringIO()

    _render(
        state,
        stdout,
        item_label=lambda item: f"{item['name']}  {item['repo']}",
    )

    assert "alpha  Org/A" in stdout.getvalue()
```

- [ ] **Step 3: Run table and selector tests to verify failure**

Run:

```bash
uv run pytest tests/test_table.py tests/test_selector.py -v
```

Expected: FAIL because `format_table` may not match the desired table shape and selector render does not accept `item_label`.

- [ ] **Step 4: Update table formatter**

Replace `src/sv/table.py` with:

```python
from __future__ import annotations

from collections.abc import Sequence


def format_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    if not headers:
        return ""

    normalized_rows = [tuple(str(value) for value in row) for row in rows]
    all_rows = [tuple(headers), *normalized_rows]
    widths = [
        max(len(row[index]) for row in all_rows)
        for index in range(len(headers))
    ]

    def format_row(row: Sequence[str]) -> str:
        return "  ".join(
            str(value).ljust(widths[index])
            for index, value in enumerate(row)
        ).rstrip()

    header = format_row(headers)
    underline = "  ".join("-" * width for width in widths).rstrip()
    body = [format_row(row) for row in normalized_rows]
    return "\n".join([header, underline, *body])
```

- [ ] **Step 5: Update selector to support object labels**

In `src/sv/selector.py`, import `Callable`, `Generic`, and `TypeVar`:

```python
from collections.abc import Callable, Sequence
from typing import Generic, TextIO, TypeVar

T = TypeVar("T")
```

Change `SelectionState` to be generic and keep object items:

```python
@dataclass
class SelectionState(Generic[T]):
    """State for a scrollable multi-select skill list."""

    items: Sequence[T]
    viewport_size: int = VIEWPORT_SIZE
    cursor: int = 0
    viewport_start: int = 0
    selected: set[int] = field(default_factory=set)
```

Change visible and selected methods:

```python
    def visible_items(self) -> list[tuple[int, T]]:
        return [
            (index, self.items[index])
            for index in range(self.viewport_start, self.visible_end)
        ]

    def selected_items(self) -> list[T]:
        return [self.items[index] for index in sorted(self.selected)]
```

Change `select_skills()` signature and state creation:

```python
def select_skills(
    skills: Sequence[T],
    *,
    viewport_size: int = VIEWPORT_SIZE,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    item_label: Callable[[T], str] = str,
) -> list[T]:
```

Pass `item_label` into every `_render()` call inside `select_skills()`:

```python
rendered_lines = _render(state, output_stream, item_label=item_label)
```

```python
_render(
    state,
    output_stream,
    previous_line_count=rendered_lines,
    highlight_cursor=False,
    item_label=item_label,
)
```

```python
rendered_lines = _render(
    state,
    output_stream,
    previous_line_count=rendered_lines,
    item_label=item_label,
)
```

Change `_render()` and `_format_skill_line()` signatures:

```python
def _render(
    state: SelectionState[T],
    stdout: TextIO,
    *,
    previous_line_count: int = 0,
    highlight_cursor: bool = True,
    item_label: Callable[[T], str] = str,
) -> int:
```

```python
    lines = [
        _format_skill_line(
            state,
            index,
            item_label(skill),
            highlight_cursor=highlight_cursor,
        )
        for index, skill in state.visible_items()
    ]
```

Keep `_format_skill_line()` accepting a string label:

```python
def _format_skill_line(
    state: SelectionState[T], index: int, skill: str, *, highlight_cursor: bool = True
) -> str:
```

- [ ] **Step 6: Run tests and commit**

Run:

```bash
uv run pytest tests/test_table.py tests/test_selector.py -v
uv run ruff check src tests
```

Expected: PASS for table and selector tests; ruff reports no issues.

Commit:

```bash
git add src/sv/table.py src/sv/selector.py tests/test_table.py tests/test_selector.py
git commit -m "feat: add repo-aware display helpers"
```

---

### Task 4: Project Manifest and Catalog-Based Project Operations

**Files:**
- Create: `src/sv/manifest.py`
- Modify: `src/sv/project.py`
- Test: `tests/test_manifest.py`
- Test: `tests/test_project.py`

- [ ] **Step 1: Write failing manifest tests**

Create `tests/test_manifest.py`:

```python
from pathlib import Path

from sv.manifest import ManifestEntry, load_manifest, save_manifest, upsert_manifest_entry


def test_load_manifest_returns_empty_when_file_missing(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"

    assert load_manifest(project_skills) == {}


def test_save_and_load_manifest_entries(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    entries = {
        "alpha": ManifestEntry(
            name="alpha",
            repo_id="Org/Skills",
            repo_url="https://github.com/Org/Skills.git",
            source_path="skills/alpha",
            description="Alpha skill.",
        )
    }

    save_manifest(project_skills, entries)

    assert (project_skills / ".sv-manifest.toml").read_text() == (
        '[[skills]]\n'
        'name = "alpha"\n'
        'repo_id = "Org/Skills"\n'
        'repo_url = "https://github.com/Org/Skills.git"\n'
        'source_path = "skills/alpha"\n'
        'description = "Alpha skill."\n'
    )
    assert load_manifest(project_skills) == entries


def test_upsert_manifest_entry_preserves_other_entries(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    save_manifest(
        project_skills,
        {
            "alpha": ManifestEntry(
                name="alpha",
                repo_id="Org/A",
                repo_url="https://github.com/Org/A.git",
                source_path="skills/alpha",
                description="Alpha skill.",
            )
        },
    )

    upsert_manifest_entry(
        project_skills,
        ManifestEntry(
            name="beta",
            repo_id="Org/B",
            repo_url="https://github.com/Org/B.git",
            source_path="skills/beta",
            description="Beta skill.",
        ),
    )

    assert sorted(load_manifest(project_skills)) == ["alpha", "beta"]
```

- [ ] **Step 2: Write failing project add/sync tests**

Add this helper near the top of `tests/test_project.py`:

```python
from sv.catalog import SourceSkill
from sv.manifest import load_manifest


def make_source_skill(source_root: Path, name: str, repo_id: str = "Org/Skills") -> SourceSkill:
    skill_dir = source_root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name.title()} skill.\n---\n"
    )
    (skill_dir / "notes.md").write_text(f"{name} remote\n")
    return SourceSkill(
        name=name,
        description=f"{name.title()} skill.",
        repo_id=repo_id,
        repo_url=f"https://github.com/{repo_id}.git",
        repo_path=source_root,
        source_path=skill_dir,
    )
```

Append these tests to `tests/test_project.py`:

```python

def test_add_project_skill_from_catalog_writes_manifest(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"

    result = add_project_skill(entry, project_skills)

    assert result.status == "added"
    assert result.skill == "alpha"
    assert result.repo_id == "Org/Skills"
    assert (project_skills / "alpha" / "notes.md").read_text() == "alpha remote\n"
    manifest = load_manifest(project_skills)
    assert manifest["alpha"].repo_id == "Org/Skills"
    assert manifest["alpha"].source_path == "skills/alpha"


def test_add_project_skill_existing_skill_does_not_write_manifest(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "alpha")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    existing = project_skills / "alpha"
    existing.mkdir(parents=True)
    (existing / "notes.md").write_text("local\n")

    result = add_project_skill(entry, project_skills)

    assert result.status == "exists"
    assert load_manifest(project_skills) == {}
    assert (existing / "notes.md").read_text() == "local\n"


def test_sync_project_skills_updates_manifest_tracked_origin(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "managed")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    assert add_project_skill(entry, project_skills).status == "added"
    (entry.source_path / "notes.md").write_text("managed remote v2\n")

    result = sync_project_skills([entry], project_skills)

    assert result.no_skills_dir is False
    assert result.updated == ["managed"]
    assert result.backfilled == []
    assert result.skipped == []
    assert (project_skills / "managed" / "notes.md").read_text() == "managed remote v2\n"
    assert load_manifest(project_skills)["managed"].repo_id == "Org/Skills"


def test_sync_project_skills_backfills_unique_untracked_skill(tmp_path: Path):
    entry = make_source_skill(tmp_path / "source", "legacy")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    legacy = project_skills / "legacy"
    legacy.mkdir(parents=True)
    (legacy / "notes.md").write_text("legacy local\n")

    result = sync_project_skills([entry], project_skills)

    assert result.updated == ["legacy"]
    assert result.backfilled == ["legacy"]
    assert (legacy / "notes.md").read_text() == "legacy remote\n"
    assert load_manifest(project_skills)["legacy"].repo_id == "Org/Skills"


def test_sync_project_skills_skips_ambiguous_untracked_skill(tmp_path: Path):
    first = make_source_skill(tmp_path / "source-a", "shared", repo_id="Org/A")
    second = make_source_skill(tmp_path / "source-b", "shared", repo_id="Org/B")
    project_skills = tmp_path / "project" / ".pi" / "skills"
    local = project_skills / "shared"
    local.mkdir(parents=True)
    (local / "notes.md").write_text("keep local\n")

    result = sync_project_skills([first, second], project_skills)

    assert result.updated == []
    assert result.backfilled == []
    assert [(skip.skill, skip.reason, skip.repo_ids) for skip in result.skipped] == [
        ("shared", "ambiguous", ("Org/A", "Org/B"))
    ]
    assert (local / "notes.md").read_text() == "keep local\n"
    assert load_manifest(project_skills) == {}
```

Update old project tests that call `add_project_skill("alpha", source_repo, project_skills)` so they call `make_source_skill()` and pass the `SourceSkill` entry. Update old sync tests so they call `sync_project_skills([entry], project_skills)`.

- [ ] **Step 3: Run manifest and project tests to verify failure**

Run:

```bash
uv run pytest tests/test_manifest.py tests/test_project.py -v
```

Expected: FAIL because `sv.manifest`, catalog-based add, and manifest-backed sync do not exist yet.

- [ ] **Step 4: Implement manifest helpers**

Create `src/sv/manifest.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class ManifestEntry:
    name: str
    repo_id: str
    repo_url: str
    source_path: str
    description: str


def manifest_path(project_skills_dir: Path) -> Path:
    return project_skills_dir / ".sv-manifest.toml"


def load_manifest(project_skills_dir: Path) -> dict[str, ManifestEntry]:
    path = manifest_path(project_skills_dir)
    if not path.is_file():
        return {}

    with path.open("rb") as file:
        data = tomllib.load(file)

    entries: dict[str, ManifestEntry] = {}
    for item in data.get("skills", []):
        entry = ManifestEntry(
            name=str(item["name"]),
            repo_id=str(item["repo_id"]),
            repo_url=str(item["repo_url"]),
            source_path=str(item["source_path"]),
            description=str(item.get("description", "")),
        )
        entries[entry.name] = entry
    return entries


def save_manifest(project_skills_dir: Path, entries: dict[str, ManifestEntry]) -> None:
    project_skills_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for index, entry in enumerate(entries[name] for name in sorted(entries)):
        if index:
            lines.append("")
        lines.append("[[skills]]")
        lines.append(f'name = "{_toml_escape(entry.name)}"')
        lines.append(f'repo_id = "{_toml_escape(entry.repo_id)}"')
        lines.append(f'repo_url = "{_toml_escape(entry.repo_url)}"')
        lines.append(f'source_path = "{_toml_escape(entry.source_path)}"')
        lines.append(f'description = "{_toml_escape(entry.description)}"')
    manifest_path(project_skills_dir).write_text("\n".join(lines) + "\n")


def upsert_manifest_entry(project_skills_dir: Path, entry: ManifestEntry) -> None:
    entries = load_manifest(project_skills_dir)
    entries[entry.name] = entry
    save_manifest(project_skills_dir, entries)


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
```

- [ ] **Step 5: Update project operations to use catalog entries and manifest**

In `src/sv/project.py`, import manifest helpers and define a protocol for catalog entries. Do not import `sv.catalog` from `sv.project`; `sv.catalog` imports `normalize_skill_name` from this module, so a runtime catalog import here would create a cycle.

```python
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from sv.manifest import ManifestEntry, load_manifest, upsert_manifest_entry


class ProjectSourceSkill(Protocol):
    name: str
    description: str
    repo_id: str
    repo_url: str
    source_path: Path

    @property
    def source_relative_path(self) -> str: ...
```

Update result dataclasses:

```python
@dataclass(frozen=True)
class AddSkillResult:
    """Outcome of adding a source skill to a Pi project."""

    skill: str
    target: Path
    status: str
    repo_id: str | None = None


@dataclass(frozen=True)
class SyncSkip:
    skill: str
    reason: str
    repo_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SyncResult:
    """Summary of local project skills considered during sync."""

    updated: list[str]
    skipped: list[SyncSkip]
    backfilled: list[str]
    no_skills_dir: bool = False
```

Replace `add_project_skill()` with:

```python
def add_project_skill(entry: ProjectSourceSkill, project_skills_dir: Path) -> AddSkillResult:
    skill_name = normalize_skill_name(entry.name)
    if not entry.source_path.is_dir():
        raise SvError(f"Skill '{skill_name}' was not found in source skills directory.")

    project_skills_dir.mkdir(parents=True, exist_ok=True)
    target = project_skills_dir / skill_name
    if target.exists():
        return AddSkillResult(
            skill=skill_name,
            target=target,
            status="exists",
            repo_id=entry.repo_id,
        )

    shutil.copytree(entry.source_path, target)
    upsert_manifest_entry(project_skills_dir, _manifest_entry_for(entry))
    return AddSkillResult(
        skill=skill_name,
        target=target,
        status="added",
        repo_id=entry.repo_id,
    )
```

Replace `add_all_project_skills()` with:

```python
def add_all_project_skills(
    catalog: Sequence[ProjectSourceSkill], project_skills_dir: Path
) -> AddAllSkillsResult:
    return AddAllSkillsResult(
        results=[add_project_skill(entry, project_skills_dir) for entry in catalog]
    )
```

Replace `sync_project_skills()` with:

```python
def sync_project_skills(
    catalog: Sequence[ProjectSourceSkill], project_skills_dir: Path
) -> SyncResult:
    if not project_skills_dir.is_dir():
        return SyncResult(updated=[], skipped=[], backfilled=[], no_skills_dir=True)

    by_key = {(entry.name, entry.repo_id): entry for entry in catalog}
    by_name: dict[str, list[ProjectSourceSkill]] = {}
    for entry in catalog:
        by_name.setdefault(entry.name, []).append(entry)

    manifest = load_manifest(project_skills_dir)
    updated: list[str] = []
    backfilled: list[str] = []
    skipped: list[SyncSkip] = []

    for local_skill in sorted(project_skills_dir.iterdir(), key=lambda path: path.name):
        if not local_skill.is_dir() or local_skill.name.startswith("."):
            continue

        manifest_entry = manifest.get(local_skill.name)
        if manifest_entry is not None:
            entry = by_key.get((manifest_entry.name, manifest_entry.repo_id))
            if entry is None:
                skipped.append(
                    SyncSkip(
                        skill=local_skill.name,
                        reason="source-missing",
                        repo_ids=(manifest_entry.repo_id,),
                    )
                )
                continue
            _replace_tree(entry.source_path, local_skill)
            upsert_manifest_entry(project_skills_dir, _manifest_entry_for(entry))
            updated.append(local_skill.name)
            continue

        matches = by_name.get(local_skill.name, [])
        if len(matches) == 1:
            entry = matches[0]
            _replace_tree(entry.source_path, local_skill)
            upsert_manifest_entry(project_skills_dir, _manifest_entry_for(entry))
            updated.append(local_skill.name)
            backfilled.append(local_skill.name)
        elif len(matches) > 1:
            skipped.append(
                SyncSkip(
                    skill=local_skill.name,
                    reason="ambiguous",
                    repo_ids=tuple(entry.repo_id for entry in matches),
                )
            )
        else:
            skipped.append(SyncSkip(skill=local_skill.name, reason="local-only"))

    return SyncResult(
        updated=updated,
        skipped=skipped,
        backfilled=backfilled,
        no_skills_dir=False,
    )
```

Add helper:

```python
def _manifest_entry_for(entry: ProjectSourceSkill) -> ManifestEntry:
    return ManifestEntry(
        name=entry.name,
        repo_id=entry.repo_id,
        repo_url=entry.repo_url,
        source_path=entry.source_relative_path,
        description=entry.description,
    )
```

Remove `_source_skill_names()` if no remaining code calls it.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
uv run pytest tests/test_manifest.py tests/test_project.py -v
uv run ruff check src tests
```

Expected: PASS for manifest and project tests; ruff reports no issues.

Commit:

```bash
git add src/sv/manifest.py src/sv/project.py tests/test_manifest.py tests/test_project.py
git commit -m "feat: track project skill origins"
```

---

### Task 5: Repo-Aware `list`, `add`, `add all`, and `add -l`

**Files:**
- Modify: `src/sv/cli.py`
- Modify: `tests/test_cli_source_commands.py`

- [ ] **Step 1: Update CLI source test helpers to create valid skills**

In `tests/test_cli_source_commands.py`, replace direct `notes.md` setup in `make_source_repo()` with this helper:

```python
def write_source_skill(source: Path, name: str, description: str, body: str) -> None:
    skill_dir = source / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"
    )
    (skill_dir / "notes.md").write_text(body)
```

Then update `make_source_repo()`:

```python
def make_source_repo(tmp_path: Path, name: str = "skill-source") -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is required for integration tests")

    source = tmp_path / name
    write_source_skill(source, "alpha", "Alpha skill.", "alpha v1\n")
    write_source_skill(source, "beta", "Beta skill.", "beta v1\n")

    run_git(["init"], source)
    run_git(["config", "user.email", "tests@example.com"], source)
    run_git(["config", "user.name", "sv tests"], source)
    run_git(["add", "skills"], source)
    run_git(["commit", "-m", "initial skills"], source)
    return source
```

Replace `configure_source()` with:

```python
def configure_source(source: Path, project: Path, home: Path):
    exit_code = handle(parse(["repo", "add", str(source)]), cwd=project, home=home)
    assert exit_code == 0
```

- [ ] **Step 2: Write failing repo-aware CLI tests**

Update `test_list_and_add_from_local_git_source()` expected list output:

```python
    output = capsys.readouterr().out
    assert output.splitlines()[0] == "Skill  Repo"
    assert "Description" in output.splitlines()[0]
    assert "alpha" in output
    assert "Alpha skill." in output
    assert "beta" in output
    assert "Beta skill." in output
```

Append these tests to `tests/test_cli_source_commands.py`:

```python

def test_add_qualified_skill_selects_repo_when_names_overlap(tmp_path: Path, capsys):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha from b\n")
    run_git(["add", "skills/alpha"], source_b)
    run_git(["commit", "-m", "update alpha in b"], source_b)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    capsys.readouterr()

    repo_id_b = load_config(SvPaths.from_home(home)).repos[1].id
    exit_code = handle(parse(["add", f"{repo_id_b}:alpha"]), cwd=project, home=home)

    assert exit_code == 0
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == "alpha from b\n"
    assert "Added Pi skill 'alpha'" in capsys.readouterr().out


def test_add_duplicate_skill_uses_choice_callback(tmp_path: Path, capsys):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha from b\n")
    run_git(["add", "skills/alpha"], source_b)
    run_git(["commit", "-m", "update alpha in b"], source_b)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    capsys.readouterr()
    choices = []

    def choose_skill(matches):
        choices.append([(match.name, match.repo_id) for match in matches])
        return matches[1]

    exit_code = handle(
        parse(["add", "alpha"]),
        cwd=project,
        home=home,
        skill_chooser=choose_skill,
    )

    assert exit_code == 0
    assert len(choices) == 1
    assert choices[0][0][0] == "alpha"
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == "alpha from b\n"
    output = capsys.readouterr().out
    assert "Multiple source skills match 'alpha'" in output
    assert "Alpha from B." in output


def test_add_interactive_receives_catalog_entries_and_installs_selected_origin(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()
    selector_calls = []

    def skill_selector(skills, **kwargs):
        selector_calls.append(([(skill.name, skill.repo_id) for skill in skills], kwargs))
        return [skills[1]]

    exit_code = handle(
        parse(["add", "-l"]),
        cwd=project,
        home=home,
        skill_selector=skill_selector,
    )

    assert exit_code == 0
    assert selector_calls[0][0][0][0] == "alpha"
    assert "item_label" in selector_calls[0][1]
    assert (project / ".pi" / "skills" / "beta" / "notes.md").read_text() == "beta v1\n"


def test_invalid_skill_is_not_listed_or_added_by_all(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    invalid = source / "skills" / "invalid"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text("---\nname: other\ndescription: Bad.\n---\n")
    run_git(["add", "skills/invalid"], source)
    run_git(["commit", "-m", "add invalid skill"], source)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    capsys.readouterr()

    assert handle(parse(["list"]), cwd=project, home=home) == 0
    assert "invalid" not in capsys.readouterr().out

    assert handle(parse(["add", "--all"]), cwd=project, home=home) == 0
    assert not (project / ".pi" / "skills" / "invalid").exists()
```

Add imports at the top of `tests/test_cli_source_commands.py`:

```python
from sv.config import SvPaths, load_config
```

- [ ] **Step 3: Run CLI source tests to verify failure**

Run:

```bash
uv run pytest tests/test_cli_source_commands.py -v
```

Expected: FAIL because CLI still uses a single source repo, old source skill names, and string-only add selection.

- [ ] **Step 4: Add CLI catalog helpers and chooser injection**

In `src/sv/cli.py`, update imports:

```python
from sv.catalog import SourceSkill, build_source_catalog, find_catalog_matches, find_qualified_catalog_entry
from sv.source import default_runner, ensure_source_repos
from sv.table import format_table
```

Update type aliases. The selector is used by both repo-aware add selection and existing project-skill removal, so keep it broad enough for both call sites:

```python
from typing import Any

SkillSelector = Callable[..., list[Any]]
SkillChooser = Callable[[Sequence[SourceSkill]], SourceSkill | None]
```

Update `handle()` signature:

```python
def handle(
    args: argparse.Namespace,
    cwd: Path,
    home: Path,
    git_runner=default_runner,
    process_runner=default_process_runner,
    skill_selector: SkillSelector = select_skills,
    skill_chooser: SkillChooser | None = None,
) -> int:
```

Set the default chooser after adapter creation:

```python
    chooser = _choose_skill if skill_chooser is None else skill_chooser
```

Add helpers:

```python
def _update_sources_and_catalog(paths: SvPaths, git_runner, *, update: bool = True) -> list[SourceSkill]:
    config = load_config(paths)
    ensure_source_repos(config.repos, paths, runner=git_runner, update=update)
    return build_source_catalog(config.repos, paths)


def _source_skill_rows(catalog: Sequence[SourceSkill]) -> list[list[str]]:
    return [[entry.name, entry.repo_id, entry.description] for entry in catalog]


def _source_skill_label(entry: SourceSkill) -> str:
    return f"{entry.name}  {entry.repo_id}  {entry.description}"
```

Add duplicate chooser:

```python
def _choose_skill(matches: Sequence[SourceSkill]) -> SourceSkill | None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None

    while True:
        choice = input("Choose a skill number, or q to cancel: ").strip()
        if choice.lower() == "q":
            return None
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(matches):
                return matches[index]
        print(f"Enter a number from 1 to {len(matches)}, or q to cancel.")
```

- [ ] **Step 5: Replace source-backed CLI branches**

In the `add` branch inside `handle()`, replace old `_ensure_configured_source()` use with catalog calls:

```python
        if args.command == "add":
            if args.interactive:
                if args.all or args.skill is not None:
                    raise SvError("Use -l by itself, or provide a skill name/--all.")
                catalog = _update_sources_and_catalog(paths, git_runner, update=True)
                return _handle_add_interactive(
                    catalog,
                    cwd=cwd,
                    adapter=adapter,
                    skill_selector=skill_selector,
                )

            if args.all and args.skill not in {None, "all"}:
                raise SvError("Use either a skill name or --all, not both.")
            catalog = _update_sources_and_catalog(paths, git_runner, update=True)
            if args.all or args.skill == "all":
                return _handle_add_all(catalog, cwd=cwd, adapter=adapter)
            if args.skill is None:
                raise SvError("Specify a skill name or use --all.")

            return _handle_add(args.skill, catalog, cwd=cwd, adapter=adapter, skill_chooser=chooser)
```

Replace source-backed list/sync dispatcher section with:

```python
        if args.command == "list":
            catalog = _update_sources_and_catalog(paths, git_runner, update=True)
            return _handle_list(catalog)

        if args.command == "sync":
            catalog = _update_sources_and_catalog(paths, git_runner, update=True)
            return _handle_sync(catalog, cwd=cwd, adapter=adapter)
```

Remove `_ensure_configured_source()` because multi-source commands now use `_update_sources_and_catalog()`.

Replace `_handle_list()`:

```python
def _handle_list(catalog: Sequence[SourceSkill]) -> int:
    if not catalog:
        print("No valid skills found in configured source repos.")
        return 0

    print(format_table(["Skill", "Repo", "Description"], _source_skill_rows(catalog)))
    return 0
```

Replace `_handle_add()`:

```python
def _handle_add(
    skill_reference: str,
    catalog: Sequence[SourceSkill],
    cwd: Path,
    adapter: PiAdapter,
    skill_chooser: SkillChooser,
) -> int:
    qualified = find_qualified_catalog_entry(catalog, skill_reference)
    if qualified is not None:
        result = add_project_skill(qualified, adapter.project_skill_dir(cwd))
        _print_add_result(result)
        return 0

    if ":" in skill_reference:
        raise SvError(f"Skill '{skill_reference}' was not found in configured source repos.")

    matches = find_catalog_matches(catalog, skill_reference)
    if not matches:
        raise SvError(f"Skill '{skill_reference}' was not found in configured source repos.")
    if len(matches) == 1:
        result = add_project_skill(matches[0], adapter.project_skill_dir(cwd))
        _print_add_result(result)
        return 0

    print(f"Multiple source skills match '{skill_reference}':")
    rows = [
        [str(index), entry.name, entry.repo_id, entry.description]
        for index, entry in enumerate(matches, start=1)
    ]
    print(format_table(["#", "Skill", "Repo", "Description"], rows))
    chosen = skill_chooser(matches)
    if chosen is None:
        print(f"No skill selected. Rerun with {matches[0].repo_id}:{matches[0].name} to choose explicitly.")
        return 0

    result = add_project_skill(chosen, adapter.project_skill_dir(cwd))
    _print_add_result(result)
    return 0
```

Replace `_handle_add_all()`:

```python
def _handle_add_all(
    catalog: Sequence[SourceSkill], cwd: Path, adapter: PiAdapter
) -> int:
    result = add_all_project_skills(catalog, adapter.project_skill_dir(cwd))
    if not result.results:
        print("No valid skills found in configured source repos.")
        return 0

    for skill_result in result.results:
        _print_add_result(skill_result)
    return 0
```

Replace `_handle_add_interactive()`:

```python
def _handle_add_interactive(
    catalog: Sequence[SourceSkill],
    cwd: Path,
    adapter: PiAdapter,
    skill_selector: SkillSelector,
) -> int:
    if not catalog:
        print("No valid skills found in configured source repos.")
        return 0

    selected_skills = skill_selector(catalog, item_label=_source_skill_label)
    if not selected_skills:
        print("No skills selected.")
        return 0

    for entry in selected_skills:
        result = add_project_skill(entry, adapter.project_skill_dir(cwd))
        _print_add_result(result)
    return 0
```

Update `_print_add_result()`:

```python
def _print_add_result(result: AddSkillResult) -> None:
    source = f" from {result.repo_id}" if result.repo_id else ""
    if result.status == "exists":
        print(f"Pi skill '{result.skill}' already exists at {result.target}")
        return

    print(f"Added Pi skill '{result.skill}'{source} to {result.target}")
```

- [ ] **Step 6: Run CLI source tests and commit**

Run:

```bash
uv run pytest tests/test_cli_source_commands.py tests/test_cli.py -v
uv run ruff check src tests
```

Expected: PASS for CLI source and CLI tests; ruff reports no issues.

Commit:

```bash
git add src/sv/cli.py tests/test_cli_source_commands.py
git commit -m "feat: add repo-aware skill commands"
```

---

### Task 6: Multi-Source `sync` and `update`

**Files:**
- Modify: `src/sv/cli.py`
- Modify: `tests/test_cli_source_commands.py`

- [ ] **Step 1: Write failing CLI `sync` and `update` tests**

Append these tests to `tests/test_cli_source_commands.py`:

```python

def test_sync_uses_manifest_origin_when_multiple_repos_have_same_skill(tmp_path: Path, capsys):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha b v1\n")
    run_git(["add", "skills/alpha"], source_b)
    run_git(["commit", "-m", "add alpha b"], source_b)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    repo_id_b = load_config(SvPaths.from_home(home)).repos[1].id
    assert handle(parse(["add", f"{repo_id_b}:alpha"]), cwd=project, home=home) == 0
    capsys.readouterr()

    write_source_skill(source_a, "alpha", "Alpha from A.", "alpha a v2\n")
    run_git(["add", "skills/alpha"], source_a)
    run_git(["commit", "-m", "update alpha a"], source_a)
    write_source_skill(source_b, "alpha", "Alpha from B.", "alpha b v2\n")
    run_git(["add", "skills/alpha"], source_b)
    run_git(["commit", "-m", "update alpha b"], source_b)

    exit_code = handle(parse(["sync"]), cwd=project, home=home)

    assert exit_code == 0
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == "alpha b v2\n"
    assert "Synced Pi skill 'alpha'." in capsys.readouterr().out


def test_sync_backfills_unique_legacy_skill(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    legacy = project / ".pi" / "skills" / "alpha"
    legacy.mkdir(parents=True)
    (legacy / "notes.md").write_text("legacy local\n")
    configure_source(source, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["sync"]), cwd=project, home=home)

    assert exit_code == 0
    assert (legacy / "notes.md").read_text() == "alpha v1\n"
    output = capsys.readouterr().out
    assert "Synced Pi skill 'alpha'." in output
    assert "Recorded origin for legacy Pi skill 'alpha'." in output


def test_sync_skips_ambiguous_legacy_skill(tmp_path: Path, capsys):
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    home = tmp_path / "home"
    project = tmp_path / "project"
    legacy = project / ".pi" / "skills" / "alpha"
    legacy.mkdir(parents=True)
    (legacy / "notes.md").write_text("legacy local\n")
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)
    capsys.readouterr()

    exit_code = handle(parse(["sync"]), cwd=project, home=home)

    assert exit_code == 0
    assert (legacy / "notes.md").read_text() == "legacy local\n"
    output = capsys.readouterr().out
    assert "Skipped local Pi skill 'alpha': multiple source repos match" in output


def test_update_pulls_sources_then_syncs_project_skills(tmp_path: Path, capsys):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    assert handle(parse(["add", "alpha"]), cwd=project, home=home) == 0
    capsys.readouterr()

    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)

    exit_code = handle(parse(["update"]), cwd=project, home=home)

    assert exit_code == 0
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == "alpha v2\n"
    output = capsys.readouterr().out
    assert "Updating source repos..." in output
    assert "Syncing project skills..." in output
    assert "Synced Pi skill 'alpha'." in output
```

- [ ] **Step 2: Run sync/update tests to verify failure**

Run:

```bash
uv run pytest tests/test_cli_source_commands.py -v
```

Expected: FAIL because `sv update` parser command does not exist and `_handle_sync()` does not print backfill/ambiguous details.

- [ ] **Step 3: Add `update` parser command**

In `src/sv/cli.py`, add parser setup next to `sync`:

```python
    subparsers.add_parser("update", help="Update source caches and sync project skills.")
```

In `handle()`, add a branch after `sync`:

```python
        if args.command == "update":
            return _handle_update(cwd=cwd, paths=paths, adapter=adapter, git_runner=git_runner)
```

- [ ] **Step 4: Replace sync output and add update handler**

Replace `_handle_sync()` with:

```python
def _handle_sync(catalog: Sequence[SourceSkill], cwd: Path, adapter: PiAdapter) -> int:
    result = sync_project_skills(catalog, adapter.project_skill_dir(cwd))
    _print_sync_result(result)
    return 0
```

Add:

```python
def _handle_update(cwd: Path, paths: SvPaths, adapter: PiAdapter, git_runner) -> int:
    print("Updating source repos...")
    catalog = _update_sources_and_catalog(paths, git_runner, update=True)
    print("Syncing project skills...")
    result = sync_project_skills(catalog, adapter.project_skill_dir(cwd))
    _print_sync_result(result)
    return 0


def _print_sync_result(result) -> None:
    if result.no_skills_dir:
        print("No Pi skills found to sync.")
        return

    if result.updated:
        for skill in result.updated:
            print(f"Synced Pi skill '{skill}'.")
    else:
        print("No matching Pi skills found to sync.")

    for skill in result.backfilled:
        print(f"Recorded origin for legacy Pi skill '{skill}'.")

    for skip in result.skipped:
        if skip.reason == "ambiguous":
            repos = ", ".join(skip.repo_ids)
            print(
                f"Skipped local Pi skill '{skip.skill}': multiple source repos match ({repos})."
            )
        elif skip.reason == "source-missing":
            repos = ", ".join(skip.repo_ids)
            print(
                f"Skipped local Pi skill '{skip.skill}': recorded source is missing ({repos})."
            )
        else:
            print(f"Skipped local Pi skill '{skip.skill}'.")
```

The untyped `result` parameter is acceptable here if importing `SyncResult` creates an import cycle. If there is no cycle, import and type it:

```python
from sv.project import SyncResult
```

and use:

```python
def _print_sync_result(result: SyncResult) -> None:
```

- [ ] **Step 5: Run sync/update tests and commit**

Run:

```bash
uv run pytest tests/test_cli_source_commands.py tests/test_project.py -v
uv run ruff check src tests
```

Expected: PASS for source command and project tests; ruff reports no issues.

Commit:

```bash
git add src/sv/cli.py tests/test_cli_source_commands.py
git commit -m "feat: add source update command"
```

---

### Task 7: Documentation, Changelog, and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Test: all tests

- [ ] **Step 1: Update README command docs**

In `README.md`, replace the single-source config section with multi-repo usage:

```markdown
## Commands

Manage source repos:

```bash
sv repo add HamdiMaz/Skills
sv repo add SomeOrg/TeamSkills
sv repo list
sv repo remove SomeOrg/TeamSkills
```

List valid skills available in configured source repos:

```bash
sv list
```

`sv list` shows the skill name, source repo, and parsed description from `SKILL.md`.

Add a skill to the current project:

```bash
sv add github-release
```

If multiple repos provide the same skill name, `sv` shows matching repos and lets you choose. You can skip the prompt with a qualified name:

```bash
sv add HamdiMaz/Skills:github-release
```

Choose one or more skills from an interactive repo-aware list:

```bash
sv add -l
```

Add every valid skill from every configured source repo:

```bash
sv add all
sv add --all
```

Update project skills from their recorded source repos:

```bash
sv sync
```

Update source repo caches and then sync project skills:

```bash
sv update
```
```

Also update the source layout section to say each skill must include valid `SKILL.md` frontmatter:

```markdown
sv expects skills to be immediate folders under `skills/` and requires each skill folder to contain `SKILL.md` with frontmatter:

```md
---
name: github-release
description: Use when creating or publishing GitHub releases.
---
```

The frontmatter `name` must match the folder name.
```

Add a short manifest note:

```markdown
## Project manifest

When `sv` installs or syncs a skill, it records the source repo in `.pi/skills/.sv-manifest.toml`. This lets `sv sync` and `sv update` refresh skills from the repo they came from, even when multiple repos contain the same skill name.
```

- [ ] **Step 2: Update changelog**

Add these bullets under `## Unreleased` in `CHANGELOG.md`:

```markdown
- Added `sv update` to refresh configured source repo caches and sync project skills in one command.
- Added `sv repo add`, `sv repo remove`, and `sv repo list` for multiple source repositories.
- Changed `sv list` and `sv add -l` to show repo-aware skill metadata from parsed `SKILL.md` files.
- Added project skill origin tracking in `.pi/skills/.sv-manifest.toml` for reliable multi-repo sync.
- Added validation for source skills requiring `SKILL.md` frontmatter with matching `name` and non-empty `description`.
```

- [ ] **Step 3: Run the full test suite**

Run:

```bash
uv run pytest -v
```

Expected: PASS for all tests.

- [ ] **Step 4: Run static checks**

Run:

```bash
uv run ruff check src tests
uv run ty check src
```

Expected: both commands exit 0.

- [ ] **Step 5: Inspect final diff**

Run:

```bash
git diff --check
git diff --stat
```

Expected: `git diff --check` prints no whitespace errors; diff stat contains only source, tests, README, and changelog changes for this feature.

- [ ] **Step 6: Commit docs and verification updates**

Commit:

```bash
git add README.md CHANGELOG.md
git commit -m "docs: document multi-source skill updates"
```

---

## Final Verification Before Handoff

After all tasks are committed, run:

```bash
uv run pytest -v
uv run ruff check src tests
uv run ty check src
git status --short
```

Expected:

- pytest passes
- ruff exits 0
- ty exits 0
- git status shows a clean worktree

If any command fails, keep the failure output, fix the specific issue, rerun the failed command, then rerun the full verification block.

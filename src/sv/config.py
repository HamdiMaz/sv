from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast
import hashlib
import re
import tomllib

from sv.errors import SvError

DEFAULT_REPO = "https://github.com/HamdiMaz/Skills.git"

_GITHUB_SHORTHAND = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GITHUB_HTTPS = re.compile(
    r"^https?://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_GITHUB_SSH = re.compile(
    r"^git@github\.com:(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$"
)
_GITHUB_SSH_URL = re.compile(
    r"^ssh://git@github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_SAFE_ID_PART = re.compile(r"[^A-Za-z0-9_.-]+")
_REPO_ID_ALLOWED = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")


@dataclass(frozen=True)
class RepoConfig:
    id: str
    url: str
    aliases: tuple[str, ...] = ()


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
        _validate_repo_id(repo_id, "repo id")
        return self.sources_dir.joinpath(*repo_id.split("/"), "repo")


@dataclass(frozen=True)
class SvConfig:
    repos: tuple[RepoConfig, ...] = (
        RepoConfig(id="HamdiMaz/Skills", url=DEFAULT_REPO),
    )

    @property
    def repo(self) -> str | None:
        if not self.repos:
            return None
        return self.repos[0].url


def normalize_repo(repo: str) -> str:
    value = repo.strip()
    if not value:
        raise ValueError("repo cannot be empty")
    if value.startswith("-"):
        raise ValueError("repo cannot start with '-'")
    if _contains_control_character(value):
        raise ValueError("repo cannot contain control characters")
    if value.startswith(("https://", "http://", "git@", "ssh://", "file://")):
        return value
    local_path = _resolve_local_repo_path(value)
    if local_path is not None and _looks_like_local_path(value):
        return str(local_path)
    if _GITHUB_SHORTHAND.fullmatch(value):
        return f"https://github.com/{value}.git"
    if local_path is not None:
        return str(local_path)
    return value


def derive_repo_id(repo: str) -> str:
    value = repo.strip()
    if _GITHUB_SHORTHAND.fullmatch(value) and not _looks_like_local_path(value):
        return value

    normalized = normalize_repo(value)
    for pattern in (_GITHUB_HTTPS, _GITHUB_SSH, _GITHUB_SSH_URL):
        match = pattern.fullmatch(normalized)
        if match:
            return f"{match.group('owner')}/{match.group('repo')}"

    return _fallback_repo_id(normalized)


def load_config(paths: SvPaths) -> SvConfig:
    if not paths.config_file.exists():
        return SvConfig()

    try:
        with paths.config_file.open("rb") as file:
            data = tomllib.load(file)
    except tomllib.TOMLDecodeError as exc:
        raise SvError(
            f"Failed to read sv config at {paths.config_file}: {exc}"
        ) from exc
    except OSError as exc:
        raise SvError(
            f"Failed to read sv config at {paths.config_file}: {exc}"
        ) from exc

    try:
        if "repos" in data:
            return SvConfig(repos=_parse_repo_entries(data["repos"], paths))

        repo = _expect_string(data.get("repo", DEFAULT_REPO), "repo", paths)
        normalized = normalize_repo(repo)
    except (TypeError, ValueError) as exc:
        raise SvError(f"Invalid sv config at {paths.config_file}: {exc}") from exc

    repo_id = derive_repo_id(normalized)
    _validate_repo_id(repo_id, "repo")
    return SvConfig(repos=(RepoConfig(id=repo_id, url=normalized),))


def add_repo(paths: SvPaths, repo: str) -> RepoChangeResult:
    normalized = normalize_repo(repo)
    repo_config = RepoConfig(id=derive_repo_id(normalized), url=normalized)
    _validate_repo_id(repo_config.id, "repo id")
    config = load_config(paths)
    if not paths.config_file.exists():
        config = SvConfig(repos=())

    repo_key = repo_source_key(repo_config.url)
    for existing in config.repos:
        if existing.id == repo_config.id or repo_source_key(existing.url) == repo_key:
            return RepoChangeResult(repo=existing, status="exists")

    _save_config(paths, SvConfig(repos=(*config.repos, repo_config)))
    return RepoChangeResult(repo=repo_config, status="added")


def remove_repo(paths: SvPaths, repo_id: str) -> RepoConfig:
    config = load_config(paths)
    remaining = tuple(repo for repo in config.repos if repo.id != repo_id)
    if len(remaining) == len(config.repos):
        raise ValueError(f"Repo {repo_id!r} is not configured")

    removed = next(repo for repo in config.repos if repo.id == repo_id)
    _save_config(paths, SvConfig(repos=remaining))
    return removed


def _parse_repo_entries(raw_repos: Any, paths: SvPaths) -> tuple[RepoConfig, ...]:
    if not isinstance(raw_repos, list):
        raise SvError(
            f"Invalid sv config at {paths.config_file}: repos must be a list."
        )

    repos: list[RepoConfig] = []
    seen_by_id: dict[str, RepoConfig] = {}
    seen_by_source: dict[str, int] = {}
    for index, item in enumerate(raw_repos, start=1):
        if not isinstance(item, dict):
            raise SvError(
                f"Invalid sv config at {paths.config_file}: repos[{index}] must be a table."
            )
        repo_item = cast("dict[str, Any]", item)
        try:
            repo_id = _expect_string(
                repo_item["id"], f"repo entry {index} field 'id'", paths
            )
            repo_url = normalize_repo(
                _expect_string(
                    repo_item["url"], f"repo entry {index} field 'url'", paths
                )
            )
        except KeyError as exc:
            raise SvError(
                f"Invalid sv config at {paths.config_file}: repo entry {index} is missing {exc.args[0]!r}."
            ) from exc
        _validate_repo_id(repo_id, f"repo entry {index} field 'id'")
        aliases = _parse_repo_aliases(repo_item.get("aliases", []), index, paths)
        repo_config = RepoConfig(id=repo_id, url=repo_url, aliases=aliases)
        source_key = repo_source_key(repo_url)
        existing = seen_by_id.get(repo_id)
        if existing is not None:
            if existing.url == repo_url or repo_source_key(existing.url) == source_key:
                continue
            raise SvError(
                f"Invalid sv config at {paths.config_file}: repo id {repo_id!r} is listed more than once with different URLs."
            )
        seen_by_id[repo_id] = repo_config
        existing_source_index = seen_by_source.get(source_key)
        if existing_source_index is not None:
            existing_source = repos[existing_source_index]
            repos[existing_source_index] = replace(
                existing_source,
                aliases=(*existing_source.aliases, repo_id, *repo_config.aliases),
            )
            continue
        seen_by_source[source_key] = len(repos)
        repos.append(repo_config)
    return tuple(repos)


def _parse_repo_aliases(raw_aliases: Any, repo_index: int, paths: SvPaths) -> tuple[str, ...]:
    if raw_aliases == []:
        return ()
    if not isinstance(raw_aliases, list):
        raise SvError(
            f"Invalid sv config at {paths.config_file}: repo entry {repo_index} field 'aliases' must be a list."
        )
    aliases: list[str] = []
    for alias_index, raw_alias in enumerate(raw_aliases, start=1):
        alias = _expect_string(
            raw_alias,
            f"repo entry {repo_index} alias {alias_index}",
            paths,
        )
        _validate_repo_id(alias, f"repo entry {repo_index} alias {alias_index}")
        if alias not in aliases:
            aliases.append(alias)
    return tuple(aliases)


def repo_source_key(repo: str) -> str:
    """Return a stable identity for duplicate source detection."""
    normalized = normalize_repo(repo)
    for pattern in (_GITHUB_HTTPS, _GITHUB_SSH, _GITHUB_SSH_URL):
        match = pattern.fullmatch(normalized)
        if match:
            owner = match.group("owner").lower()
            repo_name = match.group("repo").lower()
            return f"github:{owner}/{repo_name}"
    return normalized


def _expect_string(value: Any, field: str, paths: SvPaths) -> str:
    if not isinstance(value, str):
        raise SvError(
            f"Invalid sv config at {paths.config_file}: {field} must be a string."
        )
    return value


def _save_config(paths: SvPaths, config: SvConfig) -> None:
    try:
        paths.config_file.parent.mkdir(parents=True, exist_ok=True)
        if not config.repos:
            paths.config_file.write_text("repos = []\n")
            return

        lines: list[str] = []
        for index, repo in enumerate(config.repos):
            if index:
                lines.append("")
            lines.append("[[repos]]")
            lines.append(f'id = "{_toml_escape(repo.id)}"')
            lines.append(f'url = "{_toml_escape(repo.url)}"')
            if repo.aliases:
                aliases = ", ".join(f'"{_toml_escape(alias)}"' for alias in repo.aliases)
                lines.append(f"aliases = [{aliases}]")
        paths.config_file.write_text("\n".join(lines) + "\n")
    except OSError as exc:
        raise SvError(
            f"Failed to write sv config at {paths.config_file}: {exc}"
        ) from exc


def _validate_repo_id(repo_id: str, field: str) -> None:
    parts = repo_id.split("/")
    if (
        not repo_id
        or repo_id.startswith(("/", "\\"))
        or "\\" in repo_id
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise SvError(f"Invalid sv config: {field} contains unsafe path components.")
    if _REPO_ID_ALLOWED.fullmatch(repo_id) is None:
        raise SvError(f"Invalid sv config: {field} contains unsupported characters.")


def _contains_control_character(value: str) -> bool:
    return any(ord(char) < 0x20 or 0x7F <= ord(char) < 0xA0 for char in value)


def _looks_like_local_path(value: str) -> bool:
    return value.startswith((".", "~", "/"))


def _resolve_local_repo_path(value: str) -> Path | None:
    try:
        expanded = Path(value).expanduser()
    except RuntimeError as exc:
        raise ValueError(f"Could not resolve home directory in repo path {value!r}") from exc
    if expanded.exists() or _looks_like_local_path(value):
        return expanded.resolve()
    return None


def _fallback_repo_id(value: str) -> str:
    stem = Path(value.rstrip("/")).name or "repo"
    if stem.endswith(".git"):
        stem = stem[:-4]
    safe_stem = _SAFE_ID_PART.sub("-", stem).strip("-._") or "repo"
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    return f"local-{safe_stem}-{digest}"


def _toml_escape(value: str) -> str:
    escaped: list[str] = []
    replacements = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
    for char in value:
        replacement = replacements.get(char)
        if replacement is not None:
            escaped.append(replacement)
        elif ord(char) < 0x20:
            escaped.append(f"\\u{ord(char):04x}")
        else:
            escaped.append(char)
    return "".join(escaped)

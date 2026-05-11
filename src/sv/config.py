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
    if not paths.config_file.exists():
        config = SvConfig(repos=())

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

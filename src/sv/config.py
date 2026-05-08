from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib

DEFAULT_REPO = "https://github.com/HamdiMaz/Skills.git"

_GITHUB_SHORTHAND = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class SvPaths:
    sv_home: Path
    config_file: Path
    source_repo: Path

    @classmethod
    def from_home(cls, home: Path | None = None) -> "SvPaths":
        user_home = Path.home() if home is None else home
        sv_home = user_home / ".sv"
        return cls(
            sv_home=sv_home,
            config_file=sv_home / "config.toml",
            source_repo=sv_home / "sources" / "default" / "repo",
        )


@dataclass(frozen=True)
class SvConfig:
    repo: str = DEFAULT_REPO


def normalize_repo(repo: str) -> str:
    value = repo.strip()
    if not value:
        raise ValueError("repo cannot be empty")
    if value.startswith(("https://", "http://", "git@", "ssh://", "file://")):
        return value
    if _GITHUB_SHORTHAND.fullmatch(value):
        return f"https://github.com/{value}.git"
    return value


def load_config(paths: SvPaths) -> SvConfig:
    if not paths.config_file.exists():
        return SvConfig()

    with paths.config_file.open("rb") as file:
        data = tomllib.load(file)

    repo = data.get("repo", DEFAULT_REPO)
    return SvConfig(repo=str(repo))


def save_repo(paths: SvPaths, repo: str) -> str:
    normalized = normalize_repo(repo)
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text(f'repo = "{_toml_escape(normalized)}"\n')
    return normalized


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')

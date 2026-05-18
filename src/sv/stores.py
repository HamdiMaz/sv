from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sv.config import (
    RepoChangeResult,
    RepoConfig,
    SvConfig,
    SvPaths,
    add_repo as add_repo_config,
    load_config as load_sv_config,
    remove_repo as remove_repo_config,
)
from sv.manifest import (
    GlobalSourceState,
    ManifestEntry,
    load_global_manifest,
    load_manifest,
    save_global_manifest,
    save_manifest,
)


@dataclass(frozen=True)
class ProjectManifestStore:
    skills_dir: Path

    def load(self) -> dict[str, ManifestEntry]:
        return load_manifest(self.skills_dir)

    def save(self, entries: dict[str, ManifestEntry]) -> None:
        save_manifest(self.skills_dir, entries)


@dataclass(frozen=True)
class GlobalManifestStore:
    paths: SvPaths

    def load(self) -> dict[str, GlobalSourceState]:
        return load_global_manifest(self.paths)

    def save(self, states: dict[str, GlobalSourceState]) -> None:
        save_global_manifest(self.paths, states)


@dataclass(frozen=True)
class ConfigStore:
    paths: SvPaths

    def load(self) -> SvConfig:
        return load_sv_config(self.paths)

    def add_repo(self, repo: str, *, skills_paths: Sequence[str] = ()) -> RepoChangeResult:
        return add_repo_config(self.paths, repo, skills_paths=skills_paths)

    def remove_repo(self, repo_id: str) -> RepoConfig:
        return remove_repo_config(self.paths, repo_id)

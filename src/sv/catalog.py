from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sv.config import RepoConfig, SvPaths, repo_source_key
from sv.errors import SvError
from sv.project import normalize_skill_name
from sv.skills import InvalidSkillError, parse_skill_file
from sv.source import reject_symlinked_source_cache_path


@dataclass(frozen=True)
class SourceSkill:
    name: str
    description: str
    repo_id: str
    repo_url: str
    repo_path: Path
    source_path: Path
    repo_aliases: tuple[str, ...] = ()

    @property
    def source_relative_path(self) -> str:
        return f"skills/{self.name}"

    @property
    def display_label(self) -> str:
        return f"{self.name}  {self.repo_id}  {self.description}"


def build_source_catalog(
    repos: Iterable[RepoConfig], paths: SvPaths
) -> list[SourceSkill]:
    entries: list[SourceSkill] = []
    unique_repos: list[RepoConfig] = []
    aliases_by_source: dict[str, tuple[str, ...]] = {}
    seen_repos: dict[str, str] = {}
    seen_sources: set[str] = set()
    for repo in repos:
        source_key = repo_source_key(repo.url)
        existing_url = seen_repos.get(repo.id)
        if existing_url is not None:
            if existing_url == repo.url or repo_source_key(existing_url) == source_key:
                continue
            raise SvError(
                f"Configured source repo id {repo.id!r} is listed more than once with different URLs."
            )
        seen_repos[repo.id] = repo.url
        if source_key in seen_sources:
            aliases_by_source[source_key] = (
                *aliases_by_source[source_key],
                repo.id,
                *repo.aliases,
            )
            continue
        seen_sources.add(source_key)
        aliases_by_source[source_key] = repo.aliases
        unique_repos.append(repo)

    for repo in unique_repos:
        source_key = repo_source_key(repo.url)
        repo_path = paths.source_repo_for(repo.id)
        reject_symlinked_source_cache_path(repo_path, paths.sources_dir)
        skills_root = repo_path / "skills"
        _reject_symlinked_source_path(skills_root, "Source skills path")
        if not skills_root.is_dir():
            continue
        try:
            skill_dirs = sorted(skills_root.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise SvError(
                f"Failed to list source skills in {skills_root}: {exc}"
            ) from exc

        for skill_dir in skill_dirs:
            if skill_dir.is_symlink() or not skill_dir.is_dir():
                continue
            try:
                metadata = parse_skill_file(
                    skill_dir / "SKILL.md", expected_folder=skill_dir.name
                )
            except InvalidSkillError:
                continue
            entries.append(
                SourceSkill(
                    name=metadata.name,
                    description=metadata.description,
                    repo_id=repo.id,
                    repo_url=repo.url,
                    repo_path=repo_path,
                    source_path=skill_dir,
                    repo_aliases=aliases_by_source[source_key],
                )
            )
    return sorted(entries, key=lambda entry: (entry.name, entry.repo_id))


def _reject_symlinked_source_path(path: Path, label: str) -> None:
    try:
        if path.is_symlink():
            raise SvError(f"{label} must not be a symlink: {path}.")
    except OSError as exc:
        raise SvError(f"Failed to inspect source path {path}: {exc}") from exc


def find_catalog_matches(
    catalog: Sequence[SourceSkill], skill: str
) -> list[SourceSkill]:
    skill_name = normalize_skill_name(skill)
    return [entry for entry in catalog if entry.name == skill_name]


def find_qualified_catalog_entry(
    catalog: Sequence[SourceSkill], reference: str
) -> SourceSkill | None:
    if ":" not in reference:
        return None
    repo_id, skill = reference.rsplit(":", 1)
    skill_name = normalize_skill_name(skill)
    for entry in catalog:
        if (
            entry.name == skill_name
            and (entry.repo_id == repo_id or repo_id in entry.repo_aliases)
        ):
            return entry
    return None

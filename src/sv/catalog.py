from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sv.config import RepoConfig, SvPaths
from sv.errors import SvError
from sv.project import normalize_skill_name
from sv.skills import InvalidSkillError, parse_skill_file


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
        try:
            skill_dirs = sorted(skills_root.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise SvError(f"Failed to list source skills in {skills_root}: {exc}") from exc

        for skill_dir in skill_dirs:
            if not skill_dir.is_dir():
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
                )
            )
    return sorted(entries, key=lambda entry: (entry.name, entry.repo_id))


def find_catalog_matches(catalog: Sequence[SourceSkill], skill: str) -> list[SourceSkill]:
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
        if entry.repo_id == repo_id and entry.name == skill_name:
            return entry
    return None

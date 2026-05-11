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

    frontmatter_end = text.find("\n---", 4)
    if frontmatter_end == -1:
        raise SvError(f"Skill '{expected_folder}' has malformed frontmatter.")

    frontmatter = text[4:frontmatter_end]
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

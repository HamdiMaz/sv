from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sv.errors import SvError
from sv.project import normalize_skill_name
from sv.terminal import escape_terminal_controls


class InvalidSkillError(SvError):
    """Raised when a source skill exists but does not match sv's skill format."""


MAX_SKILL_FILE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str


def parse_skill_file(skill_file: Path, *, expected_folder: str) -> SkillMetadata:
    if skill_file.is_symlink():
        raise InvalidSkillError(
            f"Skill '{expected_folder}' SKILL.md must not be a symlink."
        )
    if not skill_file.is_file():
        raise InvalidSkillError(
            f"Skill folder '{expected_folder}' is missing SKILL.md."
        )

    text = _read_skill_file_text(skill_file, expected_folder=expected_folder)

    return parse_skill_text(text, expected_folder=expected_folder)


def _read_skill_file_text(skill_file: Path, *, expected_folder: str) -> str:
    try:
        size = skill_file.stat().st_size
        if size > MAX_SKILL_FILE_BYTES:
            raise InvalidSkillError(
                f"Skill '{expected_folder}' SKILL.md exceeds size limit "
                f"({MAX_SKILL_FILE_BYTES} bytes)."
            )
        with skill_file.open("rb") as file:
            content = file.read(MAX_SKILL_FILE_BYTES + 1)
    except InvalidSkillError:
        raise
    except OSError as exc:
        raise SvError(
            f"Failed to read SKILL.md for skill '{expected_folder}': {exc}"
        ) from exc

    if len(content) > MAX_SKILL_FILE_BYTES:
        raise InvalidSkillError(
            f"Skill '{expected_folder}' SKILL.md exceeds size limit "
            f"({MAX_SKILL_FILE_BYTES} bytes)."
        )

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SvError(
            f"Failed to read SKILL.md for skill '{expected_folder}': {exc}"
        ) from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_skill_text(text: str, *, expected_folder: str) -> SkillMetadata:
    if not text.startswith("---\n"):
        raise InvalidSkillError(
            f"Skill '{expected_folder}' must start with frontmatter."
        )

    frontmatter_end = _find_closing_frontmatter_delimiter(text)
    if frontmatter_end == -1:
        raise InvalidSkillError(f"Skill '{expected_folder}' has malformed frontmatter.")

    frontmatter = text[4:frontmatter_end]
    fields = _parse_frontmatter(frontmatter)
    name = fields.get("name", "").strip()
    description = _escape_control_characters(fields.get("description", "").strip())

    if not name:
        raise InvalidSkillError(
            f"Skill '{expected_folder}' frontmatter is missing name."
        )
    if not description:
        raise InvalidSkillError(
            f"Skill '{expected_folder}' frontmatter is missing description."
        )

    try:
        normalized_name = normalize_skill_name(name)
    except SvError as exc:
        raise InvalidSkillError(str(exc)) from exc
    if normalized_name != expected_folder:
        raise InvalidSkillError(
            f"Skill frontmatter name '{normalized_name}' does not match folder '{expected_folder}'."
        )

    return SkillMetadata(name=normalized_name, description=description)


def _find_closing_frontmatter_delimiter(text: str) -> int:
    offset = 4
    for line in text[4:].splitlines(keepends=True):
        if line.rstrip("\r\n").strip() == "---":
            return offset
        offset += len(line)
    return -1


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


def _escape_control_characters(value: str) -> str:
    return escape_terminal_controls(value)

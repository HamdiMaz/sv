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


def test_parse_skill_file_reports_unreadable_text_as_sv_error(tmp_path: Path):
    skill_dir = tmp_path / "alpha"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(SvError, match="Failed to read SKILL.md"):
        parse_skill_file(skill_file, expected_folder="alpha")


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

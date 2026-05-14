from pathlib import Path
import os

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


def test_parse_skill_file_accepts_crlf_frontmatter(tmp_path: Path):
    skill_dir = tmp_path / "alpha"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_bytes(
        b"---\r\nname: alpha\r\ndescription: Alpha skill.\r\n---\r\n"
    )

    metadata = parse_skill_file(skill_file, expected_folder="alpha")

    assert metadata == SkillMetadata(name="alpha", description="Alpha skill.")


@pytest.mark.parametrize(
    ("frontmatter", "expected_description"),
    [
        (
            "# leading comment\nname: alpha\ndescription: Alpha skill.",
            "Alpha skill.",
        ),
        (
            "  name  :  alpha  \n  description  :  Alpha skill.  ",
            "Alpha skill.",
        ),
        (
            "name: alpha\ndescription: Alpha: a skill with a colon",
            "Alpha: a skill with a colon",
        ),
        (
            "name: 'alpha'\ndescription: 'Alpha: single-quoted colon'",
            "Alpha: single-quoted colon",
        ),
    ],
)
def test_parse_skill_file_accepts_frontmatter_variations(
    tmp_path: Path, frontmatter: str, expected_description: str
):
    skill_file = write_skill(tmp_path, f"---\n{frontmatter}\n---\n")

    metadata = parse_skill_file(skill_file, expected_folder="alpha")

    assert metadata == SkillMetadata(name="alpha", description=expected_description)


def test_parse_skill_file_uses_last_duplicate_key_value(tmp_path: Path):
    skill_file = write_skill(
        tmp_path,
        "---\n"
        "name: beta\n"
        "description: Wrong description.\n"
        "name: alpha\n"
        "description: Alpha skill.\n"
        "---\n",
    )

    metadata = parse_skill_file(skill_file, expected_folder="alpha")

    assert metadata == SkillMetadata(name="alpha", description="Alpha skill.")


def test_parse_skill_file_ignores_body_delimiters_after_frontmatter(tmp_path: Path):
    skill_file = write_skill(
        tmp_path,
        "---\nname: alpha\ndescription: Alpha skill.\n---\n"
        "\n# Alpha\n\n---\nnot frontmatter\n---\n",
    )

    metadata = parse_skill_file(skill_file, expected_folder="alpha")

    assert metadata == SkillMetadata(name="alpha", description="Alpha skill.")


def test_parse_skill_file_escapes_description_control_characters(tmp_path: Path):
    skill_file = write_skill(
        tmp_path,
        "---\nname: alpha\ndescription: Alpha \x1b]52;bad\x07 skill.\n---\n",
    )

    metadata = parse_skill_file(skill_file, expected_folder="alpha")

    assert metadata == SkillMetadata(
        name="alpha", description="Alpha \\x1b]52;bad\\x07 skill."
    )


def test_parse_skill_file_escapes_description_tabs(tmp_path: Path):
    skill_file = write_skill(
        tmp_path,
        "---\nname: alpha\ndescription: Alpha\tskill.\n---\n",
    )

    metadata = parse_skill_file(skill_file, expected_folder="alpha")

    assert metadata == SkillMetadata(name="alpha", description="Alpha\\x09skill.")


def test_parse_skill_file_requires_file(tmp_path: Path):
    with pytest.raises(SvError, match="missing SKILL.md"):
        parse_skill_file(tmp_path / "alpha" / "SKILL.md", expected_folder="alpha")


def test_parse_skill_file_rejects_symlinked_skill_file(tmp_path: Path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support is required")
    skill_dir = tmp_path / "alpha"
    skill_dir.mkdir(parents=True)
    real_file = tmp_path / "real-SKILL.md"
    real_file.write_text("---\nname: alpha\ndescription: Alpha skill.\n---\n")
    os.symlink(real_file, skill_dir / "SKILL.md")

    with pytest.raises(SvError, match="SKILL.md must not be a symlink"):
        parse_skill_file(skill_dir / "SKILL.md", expected_folder="alpha")


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


@pytest.mark.parametrize(
    ("frontmatter", "expected_message"),
    [
        ("description: Alpha skill.", "name"),
        ("name:\ndescription: Alpha skill.", "name"),
        ('name: ""\ndescription: Alpha skill.', "name"),
        ("name: alpha", "description"),
        ("name: alpha\ndescription:", "description"),
        ('name: alpha\ndescription: ""', "description"),
    ],
)
def test_parse_skill_file_requires_non_empty_name_and_description(
    tmp_path: Path, frontmatter: str, expected_message: str
):
    skill_file = write_skill(tmp_path, f"---\n{frontmatter}\n---\n")

    with pytest.raises(SvError, match=expected_message):
        parse_skill_file(skill_file, expected_folder="alpha")


def test_parse_skill_file_requires_closing_frontmatter_delimiter_line(tmp_path: Path):
    skill_file = write_skill(
        tmp_path,
        "---\nname: alpha\ndescription: Alpha skill.\n---not-a-delimiter\n",
    )

    with pytest.raises(SvError, match="malformed frontmatter"):
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

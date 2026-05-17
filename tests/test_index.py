from pathlib import Path
from typing import Any, cast
import os
import tomllib

import pytest

from sv.errors import SvError
import sv.index as index_module
from sv.index import (
    INDEX_SCHEMA_VERSION,
    IndexDocument,
    IndexSkillEntry,
    index_path,
    load_index,
    readme_skill_table_is_fresh,
    save_index,
    scan_repo_for_index,
    update_readme_skill_table,
)


def _write_skill(skill_dir: Path, name: str, description: str = "Alpha skill.") -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    (skill_dir / "notes.md").write_text(f"{name}\n", encoding="utf-8")


def test_scan_repo_for_index_recursively_includes_valid_skills_and_skips_build_dirs(
    tmp_path: Path,
):
    _write_skill(tmp_path / "skills" / "alpha", "alpha", "Root alpha.")
    _write_skill(tmp_path / "team" / "skills" / "alpha", "alpha", "Team alpha.")
    _write_skill(tmp_path / ".pi" / "skills" / "pi-skill", "pi-skill", "Pi skill.")
    _write_skill(tmp_path / "tools" / "custom" / "manual", "manual", "Manual skill.")
    _write_skill(tmp_path / "build" / "skills" / "ignored", "ignored", "Ignored.")
    _write_skill(tmp_path / ".sv" / "skills" / "ignored-sv", "ignored-sv", "Ignored.")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "SKILL.md").write_text(
        "---\nname: broken\n---\n",
        encoding="utf-8",
    )
    warnings: list[str] = []

    document = scan_repo_for_index(
        tmp_path,
        kind="project-index",
        generated_at="2026-05-15T00:00:00Z",
        warn=warnings.append,
    )

    assert document.kind == "project-index"
    assert document.generated_by == "sv"
    assert document.generated_at == "2026-05-15T00:00:00Z"
    assert [(entry.name, entry.source_path) for entry in document.skills] == [
        ("pi-skill", ".pi/skills/pi-skill"),
        ("alpha", "skills/alpha"),
        ("alpha", "team/skills/alpha"),
        ("manual", "tools/custom/manual"),
    ]
    assert all(entry.content_hash.startswith("sha256:") for entry in document.skills)
    assert all(entry.skill_file_hash.startswith("sha256:") for entry in document.skills)
    assert warnings and "broken" in warnings[0]
    assert "description" in warnings[0]


def test_scan_repo_for_index_honors_include_and_exclude_paths(tmp_path: Path):
    _write_skill(tmp_path / "skills" / "alpha", "alpha", "Alpha skill.")
    _write_skill(tmp_path / "team" / "skills" / "beta", "beta", "Beta skill.")
    _write_skill(tmp_path / "team" / "skills" / "draft", "draft", "Draft skill.")
    _write_skill(tmp_path / "other" / "gamma", "gamma", "Gamma skill.")

    document = scan_repo_for_index(
        tmp_path,
        generated_at="2026-05-15T00:00:00Z",
        include_paths=("skills", "team/skills"),
        exclude_paths=("team/skills/draft",),
    )

    assert [(entry.name, entry.source_path) for entry in document.skills] == [
        ("alpha", "skills/alpha"),
        ("beta", "team/skills/beta"),
    ]


def test_scan_repo_for_index_deduplicates_overlapping_include_paths(tmp_path: Path):
    _write_skill(tmp_path / "skills" / "alpha", "alpha", "Alpha skill.")

    document = scan_repo_for_index(
        tmp_path,
        generated_at="2026-05-15T00:00:00Z",
        include_paths=("skills", "skills/alpha"),
    )

    assert [(entry.name, entry.source_path) for entry in document.skills] == [
        ("alpha", "skills/alpha"),
    ]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_scan_repo_for_index_does_not_follow_symlinked_directories(tmp_path: Path):
    outside = tmp_path.with_name(f"{tmp_path.name}-outside")
    _write_skill(outside / "skills" / "external", "external", "External skill.")
    (tmp_path / "linked-skills").symlink_to(outside / "skills", target_is_directory=True)

    document = scan_repo_for_index(tmp_path, generated_at="2026-05-15T00:00:00Z")

    assert document.skills == ()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
@pytest.mark.parametrize("include_path", ["linked-skills", "linked-skills/external"])
def test_scan_repo_for_index_rejects_symlinked_include_paths_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, include_path: str
):
    outside = tmp_path.with_name(f"{tmp_path.name}-outside")
    _write_skill(outside / "skills" / "external", "external", "External skill.")
    (tmp_path / "linked-skills").symlink_to(outside / "skills", target_is_directory=True)

    def fail_parse(*_args: Any, **_kwargs: Any):
        raise AssertionError("scan should reject the include path before parsing")

    monkeypatch.setattr("sv.index.parse_skill_file", fail_parse)

    with pytest.raises(SvError, match="symlinked include path"):
        scan_repo_for_index(
            tmp_path,
            include_paths=(include_path,),
            generated_at="2026-05-15T00:00:00Z",
        )


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_scan_repo_for_index_warns_and_skips_skill_trees_containing_symlinks(
    tmp_path: Path,
):
    _write_skill(tmp_path / "skills" / "alpha", "alpha", "Alpha skill.")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (tmp_path / "skills" / "alpha" / "outside.txt").symlink_to(outside)
    warnings: list[str] = []

    document = scan_repo_for_index(
        tmp_path,
        generated_at="2026-05-15T00:00:00Z",
        warn=warnings.append,
    )

    assert document.skills == ()
    assert warnings and "contains a symlink" in warnings[0]


def test_load_index_parses_skill_vault_file(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    path.parent.mkdir()
    path.write_text(
        "schema_version = 1\n"
        'kind = "skill-vault"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "\n"
        "[[skills]]\n"
        'name = "find-docs"\n'
        'description = "Retrieves docs."\n'
        'source_path = "skills/find-docs"\n'
        'content_hash = "sha256:content"\n'
        'skill_file_hash = "sha256:skill-file"\n'
    )

    assert load_index(path) == IndexDocument(
        schema_version=INDEX_SCHEMA_VERSION,
        kind="skill-vault",
        generated_by="sv",
        generated_at="2026-05-15T00:00:00Z",
        skills=(
            IndexSkillEntry(
                name="find-docs",
                description="Retrieves docs.",
                source_path="skills/find-docs",
                content_hash="sha256:content",
                skill_file_hash="sha256:skill-file",
            ),
        ),
    )


def test_load_index_parses_project_index_file(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    path.parent.mkdir()
    path.write_text(
        "schema_version = 1\n"
        'kind = "project-index"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "skills = []\n"
    )

    assert load_index(path).kind == "project-index"
    assert load_index(path).skills == ()


def test_load_index_allows_duplicate_skill_names_with_different_paths(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    path.parent.mkdir()
    path.write_text(
        "schema_version = 1\n"
        'kind = "skill-vault"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "\n"
        "[[skills]]\n"
        'name = "alpha"\n'
        'description = "Root alpha."\n'
        'source_path = "skills/alpha"\n'
        'content_hash = "sha256:root"\n'
        'skill_file_hash = "sha256:root-file"\n'
        "\n"
        "[[skills]]\n"
        'name = "alpha"\n'
        'description = "Team alpha."\n'
        'source_path = "team/skills/alpha"\n'
        'content_hash = "sha256:team"\n'
        'skill_file_hash = "sha256:team-file"\n'
    )

    index = load_index(path)

    assert [(entry.name, entry.source_path) for entry in index.skills] == [
        ("alpha", "skills/alpha"),
        ("alpha", "team/skills/alpha"),
    ]


def test_load_index_rejects_future_schema_with_update_message(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    path.parent.mkdir()
    path.write_text(
        "schema_version = 99\n"
        'kind = "skill-vault"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "skills = []\n"
    )

    with pytest.raises(SvError) as exc_info:
        load_index(path)

    message = str(exc_info.value)
    assert "Unsupported sv index schema_version 99" in message
    assert "update sv" in message.lower()
    assert str(path) in message


def test_load_index_rejects_unsupported_kind(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    path.parent.mkdir()
    path.write_text(
        "schema_version = 1\n"
        'kind = "unknown"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "skills = []\n"
    )

    with pytest.raises(SvError, match="kind must be 'skill-vault' or 'project-index'"):
        load_index(path)


def test_load_index_rejects_unsafe_source_path(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    path.parent.mkdir()
    path.write_text(
        "schema_version = 1\n"
        'kind = "skill-vault"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "\n"
        "[[skills]]\n"
        'name = "alpha"\n'
        'description = "Alpha."\n'
        'source_path = "../alpha"\n'
        'content_hash = "sha256:content"\n'
        'skill_file_hash = "sha256:skill-file"\n'
    )

    with pytest.raises(SvError, match="source_path"):
        load_index(path)


def test_load_index_rejects_name_source_path_mismatch(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    path.parent.mkdir()
    path.write_text(
        "schema_version = 1\n"
        'kind = "skill-vault"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "\n"
        "[[skills]]\n"
        'name = "alpha"\n'
        'description = "Alpha."\n'
        'source_path = "skills/beta"\n'
        'content_hash = "sha256:content"\n'
        'skill_file_hash = "sha256:skill-file"\n'
    )

    with pytest.raises(
        SvError,
        match="source_path 'skills/beta' does not match skill name 'alpha'",
    ):
        load_index(path)


def test_save_index_rejects_name_source_path_mismatch(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    document = IndexDocument(
        kind="skill-vault",
        generated_by="sv",
        generated_at="2026-05-15T00:00:00Z",
        skills=(
            IndexSkillEntry(
                name="alpha",
                description="Alpha.",
                source_path="skills/beta",
                content_hash="sha256:content",
                skill_file_hash="sha256:skill-file",
            ),
        ),
    )

    with pytest.raises(
        SvError,
        match="source_path 'skills/beta' does not match skill name 'alpha'",
    ):
        save_index(path, document)


def test_save_index_writes_deterministic_toml_and_round_trips(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    document = IndexDocument(
        kind="skill-vault",
        generated_by="sv",
        generated_at="2026-05-15T00:00:00Z",
        skills=(
            IndexSkillEntry(
                name="zeta",
                description="Zeta \"quoted\" skill.",
                source_path="skills/zeta",
                content_hash="sha256:zeta-content",
                skill_file_hash="sha256:zeta-file",
            ),
            IndexSkillEntry(
                name="alpha",
                description="Alpha skill.\nSecond line.",
                source_path="team/skills/alpha",
                content_hash="sha256:team-content",
                skill_file_hash="sha256:team-file",
            ),
            IndexSkillEntry(
                name="alpha",
                description="Root alpha.",
                source_path="skills/alpha",
                content_hash="sha256:root-content",
                skill_file_hash="sha256:root-file",
            ),
        ),
    )

    save_index(path, document)

    assert path.read_text() == (
        "schema_version = 1\n"
        'kind = "skill-vault"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "\n"
        "[[skills]]\n"
        'name = "alpha"\n'
        'description = "Root alpha."\n'
        'source_path = "skills/alpha"\n'
        'content_hash = "sha256:root-content"\n'
        'skill_file_hash = "sha256:root-file"\n'
        "\n"
        "[[skills]]\n"
        'name = "alpha"\n'
        'description = "Alpha skill.\\nSecond line."\n'
        'source_path = "team/skills/alpha"\n'
        'content_hash = "sha256:team-content"\n'
        'skill_file_hash = "sha256:team-file"\n'
        "\n"
        "[[skills]]\n"
        'name = "zeta"\n'
        'description = "Zeta \\"quoted\\" skill."\n'
        'source_path = "skills/zeta"\n'
        'content_hash = "sha256:zeta-content"\n'
        'skill_file_hash = "sha256:zeta-file"\n'
    )
    assert tomllib.loads(path.read_text())["skills"][0]["source_path"] == "skills/alpha"
    assert load_index(path) == IndexDocument(
        kind="skill-vault",
        generated_by="sv",
        generated_at="2026-05-15T00:00:00Z",
        skills=(
            IndexSkillEntry(
                name="alpha",
                description="Root alpha.",
                source_path="skills/alpha",
                content_hash="sha256:root-content",
                skill_file_hash="sha256:root-file",
            ),
            IndexSkillEntry(
                name="alpha",
                description="Alpha skill.\nSecond line.",
                source_path="team/skills/alpha",
                content_hash="sha256:team-content",
                skill_file_hash="sha256:team-file",
            ),
            IndexSkillEntry(
                name="zeta",
                description="Zeta \"quoted\" skill.",
                source_path="skills/zeta",
                content_hash="sha256:zeta-content",
                skill_file_hash="sha256:zeta-file",
            ),
        ),
    )


def test_load_index_requires_schema_version(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    path.parent.mkdir()
    path.write_text(
        'kind = "skill-vault"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "skills = []\n"
    )

    with pytest.raises(SvError, match="missing 'schema_version'"):
        load_index(path)


def test_load_index_rejects_non_list_skills(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    path.parent.mkdir()
    path.write_text(
        "schema_version = 1\n"
        'kind = "skill-vault"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "[skills]\n"
    )

    with pytest.raises(SvError, match="skills must be a list"):
        load_index(path)


def test_load_index_rejects_non_table_skill_entries(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    path.parent.mkdir()
    path.write_text(
        "schema_version = 1\n"
        'kind = "skill-vault"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        'skills = ["alpha"]\n'
    )

    with pytest.raises(SvError, match=r"skills\[1\] must be a table"):
        load_index(path)


def test_load_index_rejects_non_string_fields(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    path.parent.mkdir()
    path.write_text(
        "schema_version = 1\n"
        'kind = "skill-vault"\n'
        "generated_by = 1\n"
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "skills = []\n"
    )

    with pytest.raises(SvError, match="field 'generated_by' must be a string"):
        load_index(path)


def test_load_index_rejects_missing_skill_fields(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    path.parent.mkdir()
    path.write_text(
        "schema_version = 1\n"
        'kind = "skill-vault"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "\n"
        "[[skills]]\n"
        'name = "alpha"\n'
        'description = "Alpha."\n'
        'source_path = "skills/alpha"\n'
        'content_hash = "sha256:content"\n'
    )

    with pytest.raises(
        SvError, match="skill entry 1 field 'skill_file_hash' must be a string"
    ):
        load_index(path)


def test_save_index_rejects_invalid_schema_version(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    document = IndexDocument(
        schema_version=2,
        kind="skill-vault",
        generated_by="sv",
        generated_at="2026-05-15T00:00:00Z",
    )

    with pytest.raises(SvError, match="schema_version must be 1"):
        save_index(path, document)


def test_save_index_rejects_invalid_source_path(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    document = IndexDocument(
        kind="skill-vault",
        generated_by="sv",
        generated_at="2026-05-15T00:00:00Z",
        skills=(
            IndexSkillEntry(
                name="alpha",
                description="Alpha.",
                source_path="/alpha",
                content_hash="sha256:content",
                skill_file_hash="sha256:skill-file",
            ),
        ),
    )

    with pytest.raises(SvError, match="source_path"):
        save_index(path, document)


def test_save_index_writes_canonical_source_paths(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    document = IndexDocument(
        kind="skill-vault",
        generated_by="sv",
        generated_at="2026-05-15T00:00:00Z",
        skills=(
            IndexSkillEntry(
                name="alpha",
                description="Alpha.",
                source_path="skills/./alpha",
                content_hash="sha256:content",
                skill_file_hash="sha256:skill-file",
            ),
        ),
    )

    save_index(path, document)

    assert 'source_path = "skills/alpha"' in path.read_text()
    assert load_index(path).skills[0].source_path == "skills/alpha"


def test_save_index_rejects_non_string_document_fields(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    invalid_generated_by: Any = 1
    document = IndexDocument(
        kind="skill-vault",
        generated_by=invalid_generated_by,
        generated_at="2026-05-15T00:00:00Z",
    )

    with pytest.raises(SvError, match="field 'generated_by' must be a string"):
        save_index(path, document)


def test_save_index_rejects_non_string_skill_fields(tmp_path: Path):
    path = tmp_path / ".sv" / "index.toml"
    invalid_content_hash: Any = 1
    document = IndexDocument(
        kind="skill-vault",
        generated_by="sv",
        generated_at="2026-05-15T00:00:00Z",
        skills=(
            IndexSkillEntry(
                name="alpha",
                description="Alpha.",
                source_path="skills/alpha",
                content_hash=invalid_content_hash,
                skill_file_hash="sha256:skill-file",
            ),
        ),
    )

    with pytest.raises(
        SvError, match="skill entry 1 field 'content_hash' must be a string"
    ):
        save_index(path, document)


def test_save_index_refuses_symlinked_sv_directory(tmp_path: Path):
    if not hasattr(Path, "symlink_to"):
        pytest.skip("symlink support is required")
    outside = tmp_path / "outside-sv"
    outside.mkdir()
    (tmp_path / ".sv").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SvError, match="symlinked sv index directory"):
        save_index(
            tmp_path / ".sv" / "index.toml",
            IndexDocument(
                kind="project-index",
                generated_by="sv",
                generated_at="2026-05-15T00:00:00Z",
            ),
        )

    assert not (outside / "index.toml").exists()


def test_save_index_reports_directory_inspection_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / ".sv" / "index.toml"
    original_is_symlink = Path.is_symlink

    def fail_is_symlink(candidate: Path):
        if candidate == path.parent:
            raise OSError("cannot inspect")
        return original_is_symlink(candidate)

    monkeypatch.setattr(Path, "is_symlink", fail_is_symlink)

    with pytest.raises(SvError, match="Failed to inspect sv index directory"):
        save_index(
            path,
            IndexDocument(
                kind="project-index",
                generated_by="sv",
                generated_at="2026-05-15T00:00:00Z",
            ),
        )


def test_index_path_points_to_sv_index_file(tmp_path: Path):
    assert index_path(tmp_path) == tmp_path / ".sv" / "index.toml"


def test_update_readme_skill_table_preserves_user_content_outside_markers(
    tmp_path: Path,
):
    readme = tmp_path / "README.md"
    readme.write_text(
        "# My Skill Vault\n"
        "\n"
        "Intro text.\n"
        "<!-- sv:skills:start -->\n"
        "old generated content\n"
        "<!-- sv:skills:end -->\n"
        "\n"
        "Footer text.\n",
        encoding="utf-8",
    )
    document = IndexDocument(
        kind="skill-vault",
        generated_by="sv",
        generated_at="2026-05-15T00:00:00Z",
        skills=(
            IndexSkillEntry(
                name="beta",
                description="Beta | skill.\nSecond line.",
                source_path="skills/beta",
                content_hash="sha256:beta",
                skill_file_hash="sha256:beta-file",
            ),
            IndexSkillEntry(
                name="alpha",
                description="Alpha skill.",
                source_path="skills/alpha",
                content_hash="sha256:alpha",
                skill_file_hash="sha256:alpha-file",
            ),
        ),
    )

    update_readme_skill_table(readme, document)

    assert readme.read_text(encoding="utf-8") == (
        "# My Skill Vault\n"
        "\n"
        "Intro text.\n"
        "<!-- sv:skills:start -->\n"
        "| Skill | Description |\n"
        "| --- | --- |\n"
        "| alpha | Alpha skill. |\n"
        "| beta | Beta \\| skill. Second line. |\n"
        "<!-- sv:skills:end -->\n"
        "\n"
        "Footer text.\n"
    )


def test_update_readme_skill_table_escapes_marker_text_in_skill_descriptions(
    tmp_path: Path,
):
    readme = tmp_path / "README.md"
    document = IndexDocument(
        kind="skill-vault",
        generated_by="sv",
        generated_at="2026-05-15T00:00:00Z",
        skills=(
            IndexSkillEntry(
                name="alpha",
                description="Contains <!-- sv:skills:start --> marker.",
                source_path="skills/alpha",
                content_hash="sha256:alpha",
                skill_file_hash="sha256:alpha-file",
            ),
        ),
    )

    update_readme_skill_table(readme, document)
    update_readme_skill_table(readme, document)

    assert readme.read_text(encoding="utf-8") == (
        "<!-- sv:skills:start -->\n"
        "| Skill | Description |\n"
        "| --- | --- |\n"
        "| alpha | Contains &lt;!-- sv:skills:start --&gt; marker. |\n"
        "<!-- sv:skills:end -->\n"
    )


def test_update_readme_skill_table_creates_missing_markers(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("# My Skill Vault\n\nIntro text.\n", encoding="utf-8")
    document = IndexDocument(
        kind="skill-vault",
        generated_by="sv",
        generated_at="2026-05-15T00:00:00Z",
        skills=(),
    )

    assert readme_skill_table_is_fresh(readme, document) is False

    update_readme_skill_table(readme, document)

    assert readme.read_text(encoding="utf-8") == (
        "# My Skill Vault\n"
        "\n"
        "Intro text.\n"
        "\n"
        "<!-- sv:skills:start -->\n"
        "| Skill | Description |\n"
        "| --- | --- |\n"
        "<!-- sv:skills:end -->\n"
    )
    assert readme_skill_table_is_fresh(readme, document) is True


def test_readme_skill_table_helpers_handle_project_kind_and_marker_errors(tmp_path: Path):
    path = tmp_path / "README.md"
    project_doc = IndexDocument(kind="project-index", generated_by="sv", generated_at="now")
    assert update_readme_skill_table(path, project_doc) is None
    assert not path.exists()

    vault_doc = IndexDocument(
        kind="skill-vault",
        generated_by="sv",
        generated_at="now",
        skills=(IndexSkillEntry("a|b", "Line\n<&>", "skills/a", "sha256:1", "sha256:2"),),
    )
    path.write_text("# Title\n", encoding="utf-8")
    update_readme_skill_table(path, vault_doc)
    text = path.read_text(encoding="utf-8")
    assert "| a\\|b | Line &lt;&amp;&gt; |" in text
    assert "<!-- sv:skills:start -->" in text
    assert update_readme_skill_table(path, vault_doc) is None

    path.write_text("<!-- sv:skills:end -->\n<!-- sv:skills:start -->\n", encoding="utf-8")
    with pytest.raises(SvError, match="end marker appears before start"):
        update_readme_skill_table(path, vault_doc)

    path.write_text("<!-- sv:skills:start -->\n<!-- sv:skills:start -->\n<!-- sv:skills:end -->\n", encoding="utf-8")
    with pytest.raises(SvError, match="exactly one"):
        update_readme_skill_table(path, vault_doc)


def test_index_scan_config_validation_and_dedupes_paths(tmp_path: Path):
    from sv.index import load_index_scan_config

    config_dir = tmp_path / ".sv"
    config_dir.mkdir()
    config_path = config_dir / "index-config.toml"
    config_path.write_text(
        'schema_version = 1\ninclude_paths = ["skills", "skills"]\nexclude_paths = ["drafts"]\n',
        encoding="utf-8",
    )
    config = load_index_scan_config(tmp_path)
    assert config.include_paths == ("skills",)
    assert config.exclude_paths == ("drafts",)

    config_path.write_text('schema_version = 1\ninclude_paths = "skills"\n', encoding="utf-8")
    with pytest.raises(SvError, match="include_paths must be a list"):
        load_index_scan_config(tmp_path)

    config_path.write_text('schema_version = 1\ninclude_paths = [1]\n', encoding="utf-8")
    with pytest.raises(SvError, match=r"include_paths\[1\] must be a string"):
        load_index_scan_config(tmp_path)

    config_path.write_text('schema_version = 1\ninclude_paths = ["../bad"]\n', encoding="utf-8")
    with pytest.raises(SvError, match=r"include_paths\[1\] is invalid"):
        load_index_scan_config(tmp_path)


def test_load_index_scan_config_rejects_symlinked_config_file(tmp_path: Path):
    from sv.index import load_index_scan_config

    config_dir = tmp_path / ".sv"
    config_dir.mkdir()
    outside_config = tmp_path / "outside-index-config.toml"
    outside_config.write_text("schema_version = 1\n", encoding="utf-8")
    (config_dir / "index-config.toml").symlink_to(outside_config)

    with pytest.raises(SvError, match="symlinked sv index scan config"):
        load_index_scan_config(tmp_path)


def test_load_index_scan_config_rejects_broken_symlinked_config_file(
    tmp_path: Path,
):
    from sv.index import load_index_scan_config

    config_dir = tmp_path / ".sv"
    config_dir.mkdir()
    (config_dir / "index-config.toml").symlink_to(tmp_path / "missing.toml")

    with pytest.raises(SvError, match="symlinked sv index scan config"):
        load_index_scan_config(tmp_path)


def test_load_index_bytes_and_validation_errors_are_actionable(tmp_path: Path):
    from sv.index import load_index_bytes

    path = tmp_path / ".sv" / "index.toml"
    path.parent.mkdir()
    with pytest.raises(SvError, match="not valid UTF-8"):
        load_index_bytes(b"\xff", path)
    with pytest.raises(SvError, match="Failed to read sv index"):
        load_index_bytes(b"skills = [\n", path)
    with pytest.raises(SvError, match="missing 'schema_version'"):
        load_index_bytes(b'kind = "skill-vault"\n', path)

    base = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "kind": "skill-vault",
        "generated_by": "sv",
        "generated_at": "now",
        "skills": [],
    }
    for field in ("kind", "generated_by", "generated_at"):
        bad = dict(base)
        bad[field] = 1
        with pytest.raises(SvError, match=field):
            save_index(
                path,
                IndexDocument(
                    kind=cast(Any, bad.get("kind")),
                    generated_by=cast(Any, bad.get("generated_by")),
                    generated_at=cast(Any, bad.get("generated_at")),
                ),
            )

    with pytest.raises(SvError, match="schema_version must be"):
        save_index(path, IndexDocument(kind="skill-vault", generated_by="sv", generated_at="now", schema_version=99))
    with pytest.raises(SvError, match="source_path"):
        save_index(
            path,
            IndexDocument(
                kind="skill-vault",
                generated_by="sv",
                generated_at="now",
                skills=(IndexSkillEntry("alpha", "desc", "../bad", "sha256:1", "sha256:2"),),
            ),
        )


def test_load_index_bytes_rejects_too_many_skill_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from sv.index import load_index_bytes

    monkeypatch.setattr(index_module, "_MAX_INDEX_SKILL_ENTRIES", 0, raising=False)
    path = tmp_path / ".sv" / "index.toml"
    content = b"""
    schema_version = 1
    kind = "skill-vault"
    generated_by = "sv"
    generated_at = "now"

    [[skills]]
    name = "alpha"
    description = "Alpha."
    source_path = "skills/alpha"
    content_hash = "sha256:alpha"
    skill_file_hash = "sha256:alpha-skill"
    """

    with pytest.raises(SvError, match="skill entry limit"):
        load_index_bytes(content, path)


def test_load_index_bytes_rejects_oversized_skill_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from sv.index import load_index_bytes

    monkeypatch.setattr(index_module, "_MAX_INDEX_FIELD_LENGTH", 4, raising=False)
    path = tmp_path / ".sv" / "index.toml"
    content = b"""
    schema_version = 1
    kind = "skill-vault"
    generated_by = "sv"
    generated_at = "now"

    [[skills]]
    name = "a"
    description = "Alpha."
    source_path = "a"
    content_hash = "h"
    skill_file_hash = "s"
    """

    with pytest.raises(SvError, match="description.*exceeds"):
        load_index_bytes(content, path)


def test_save_index_rejects_too_many_skill_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(index_module, "_MAX_INDEX_SKILL_ENTRIES", 0, raising=False)
    path = tmp_path / ".sv" / "index.toml"

    with pytest.raises(SvError, match="skill entry limit"):
        save_index(
            path,
            IndexDocument(
                kind="skill-vault",
                generated_by="sv",
                generated_at="now",
                skills=(
                    IndexSkillEntry(
                        "alpha",
                        "Alpha.",
                        "skills/alpha",
                        "sha256:alpha",
                        "sha256:alpha-skill",
                    ),
                ),
            ),
        )


def test_scan_repo_for_index_with_file_include_dedupes_and_reports_filesystem_errors(tmp_path: Path, monkeypatch):
    _write_skill(tmp_path / "skills" / "alpha", "alpha", "Alpha skill.")
    document = scan_repo_for_index(
        tmp_path,
        include_paths=("skills/alpha/SKILL.md", "skills"),
        generated_at="now",
    )
    assert [entry.source_path for entry in document.skills] == ["skills/alpha"]

    def fail_scandir(_path):
        raise OSError("scan failed")

    monkeypatch.setattr(os, "scandir", fail_scandir)
    with pytest.raises(SvError, match="Failed to scan directory"):
        scan_repo_for_index(tmp_path)

from pathlib import Path

import pytest

from sv.errors import SvError
from sv.manifest import (
    ManifestEntry,
    load_manifest,
    save_manifest,
    upsert_manifest_entry,
)


def test_load_manifest_returns_empty_when_file_missing(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"

    assert load_manifest(project_skills) == {}


def test_save_and_load_manifest_entries(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    entries = {
        "alpha": ManifestEntry(
            name="alpha",
            repo_id="Org/Skills",
            repo_url="https://github.com/Org/Skills.git",
            source_path="skills/alpha",
            description="Alpha skill.",
        )
    }

    save_manifest(project_skills, entries)

    assert (project_skills / ".sv-manifest.toml").read_text() == (
        "[[skills]]\n"
        'name = "alpha"\n'
        'repo_id = "Org/Skills"\n'
        'repo_url = "https://github.com/Org/Skills.git"\n'
        'source_path = "skills/alpha"\n'
        'description = "Alpha skill."\n'
    )
    assert load_manifest(project_skills) == entries


def test_save_manifest_escapes_control_characters(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    entries = {
        "alpha": ManifestEntry(
            name="alpha",
            repo_id="Org/Skills",
            repo_url="https://github.com/Org/Skills.git",
            source_path="skills/alpha",
            description="Line one\nLine two\tTabbed",
        )
    }

    save_manifest(project_skills, entries)

    manifest_text = (project_skills / ".sv-manifest.toml").read_text()
    assert "Line one\\nLine two\\tTabbed" in manifest_text
    assert load_manifest(project_skills) == entries


def test_load_manifest_reports_malformed_toml_as_sv_error(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    (project_skills / ".sv-manifest.toml").write_text("skills = [\n")

    with pytest.raises(SvError, match="Failed to read sv manifest"):
        load_manifest(project_skills)


def test_load_manifest_reports_invalid_entries_as_sv_error(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    (project_skills / ".sv-manifest.toml").write_text('[[skills]]\nname = "alpha"\n')

    with pytest.raises(SvError, match="Invalid sv manifest"):
        load_manifest(project_skills)


def test_load_manifest_rejects_non_string_fields(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    (project_skills / ".sv-manifest.toml").write_text(
        "[[skills]]\n"
        'name = "alpha"\n'
        "repo_id = []\n"
        'repo_url = "https://github.com/Org/Skills.git"\n'
        'source_path = "skills/alpha"\n'
    )

    with pytest.raises(SvError, match="skill entry 1 field 'repo_id' must be a string"):
        load_manifest(project_skills)


def test_save_manifest_reports_write_failures(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    project_skills.parent.mkdir(parents=True)
    project_skills.write_text("not a directory\n")

    with pytest.raises(SvError, match="Failed to write sv manifest"):
        save_manifest(project_skills, {})


def test_save_manifest_preserves_existing_manifest_when_temp_write_fails(
    tmp_path: Path, monkeypatch
):
    project_skills = tmp_path / ".pi" / "skills"
    original = {
        "alpha": ManifestEntry(
            name="alpha",
            repo_id="Org/A",
            repo_url="https://github.com/Org/A.git",
            source_path="skills/alpha",
            description="Alpha skill.",
        )
    }
    save_manifest(project_skills, original)
    original_text = (project_skills / ".sv-manifest.toml").read_text()
    real_write_text = Path.write_text

    def fail_temp_write(path, text, *args, **kwargs):
        if path.name == ".sv-manifest.toml.tmp":
            real_write_text(path, "partial\n", *args, **kwargs)
            raise OSError("write failed")
        return real_write_text(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_temp_write)

    with pytest.raises(SvError, match="Failed to write sv manifest"):
        save_manifest(
            project_skills,
            {
                "beta": ManifestEntry(
                    name="beta",
                    repo_id="Org/B",
                    repo_url="https://github.com/Org/B.git",
                    source_path="skills/beta",
                    description="Beta skill.",
                )
            },
        )

    assert (project_skills / ".sv-manifest.toml").read_text() == original_text
    assert not (project_skills / ".sv-manifest.toml.tmp").exists()


def test_upsert_manifest_entry_preserves_other_entries(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    save_manifest(
        project_skills,
        {
            "alpha": ManifestEntry(
                name="alpha",
                repo_id="Org/A",
                repo_url="https://github.com/Org/A.git",
                source_path="skills/alpha",
                description="Alpha skill.",
            )
        },
    )

    upsert_manifest_entry(
        project_skills,
        ManifestEntry(
            name="beta",
            repo_id="Org/B",
            repo_url="https://github.com/Org/B.git",
            source_path="skills/beta",
            description="Beta skill.",
        ),
    )

    assert sorted(load_manifest(project_skills)) == ["alpha", "beta"]

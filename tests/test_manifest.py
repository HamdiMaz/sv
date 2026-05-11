from pathlib import Path

from sv.manifest import ManifestEntry, load_manifest, save_manifest, upsert_manifest_entry


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
        '[[skills]]\n'
        'name = "alpha"\n'
        'repo_id = "Org/Skills"\n'
        'repo_url = "https://github.com/Org/Skills.git"\n'
        'source_path = "skills/alpha"\n'
        'description = "Alpha skill."\n'
    )
    assert load_manifest(project_skills) == entries


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

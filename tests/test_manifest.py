from pathlib import Path
import os
import tomllib

import pytest

from sv import manifest as manifest_module
from sv import tomlutil as tomlutil_module
from sv.config import SvPaths
from sv.errors import SvError
from sv.manifest import (
    GlobalSourceState,
    ManifestEntry,
    load_global_manifest,
    load_manifest,
    remove_manifest_entry,
    save_global_manifest,
    save_manifest,
    upsert_manifest_entry,
)


def _valid_hash(seed: str) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(seed.encode()).hexdigest()}"


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
            target_kind="project-agent",
            target_agent="pi",
            target_path=".pi/skills/alpha",
            source_backend="git",
            source_commit="abc123",
            source_tree="tree123",
            source_content_hash="sha256:41cf6794ba4200b839c53531555f0f3998df4cbb01a4d5cb0b94e3ca5e23947d",
            source_skill_file_hash="sha256:fac608e8879fb4f49e6adeb8a35e48d431e8e3d9fbd3c50416a805cf70403926",
            installed_content_hash="sha256:a51a6c19a1ffc7416827e89adf20749d23ad42452c396cf7e627409f2896922c",
            local_content_hash="sha256:25bf8e1a2393f1108d37029b3df5593236c755742ec93465bbafa9b290bddcf6",
            orphan=True,
            modified=True,
            update_available=True,
        )
    }

    save_manifest(project_skills, entries)

    assert (
        manifest_module.manifest_path(project_skills)
        == tmp_path / ".sv" / "manifest.toml"
    )
    assert manifest_module.manifest_path(project_skills).read_text() == (
        "schema_version = 1\n"
        "\n"
        "[[skills]]\n"
        'name = "alpha"\n'
        'target_kind = "project-agent"\n'
        'target_agent = "pi"\n'
        'target_path = ".pi/skills/alpha"\n'
        'source_repo_id = "Org/Skills"\n'
        'source_repo_url = "https://github.com/Org/Skills.git"\n'
        'source_path = "skills/alpha"\n'
        'description = "Alpha skill."\n'
        'source_backend = "git"\n'
        'source_commit = "abc123"\n'
        'source_tree = "tree123"\n'
        'source_content_hash = "sha256:41cf6794ba4200b839c53531555f0f3998df4cbb01a4d5cb0b94e3ca5e23947d"\n'
        'source_skill_file_hash = "sha256:fac608e8879fb4f49e6adeb8a35e48d431e8e3d9fbd3c50416a805cf70403926"\n'
        'installed_content_hash = "sha256:a51a6c19a1ffc7416827e89adf20749d23ad42452c396cf7e627409f2896922c"\n'
        'local_content_hash = "sha256:25bf8e1a2393f1108d37029b3df5593236c755742ec93465bbafa9b290bddcf6"\n'
        "orphan = true\n"
        "modified = true\n"
        "update_available = true\n"
    )
    assert load_manifest(project_skills) == entries


def test_save_manifest_writes_schema_only_when_entries_empty(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    save_manifest(
        project_skills,
        {
            "alpha": ManifestEntry(
                name="alpha",
                repo_id="Org/Skills",
                repo_url="https://github.com/Org/Skills.git",
                source_path="skills/alpha",
                description="Alpha skill.",
            )
        },
    )

    save_manifest(project_skills, {})

    assert (
        manifest_module.manifest_path(project_skills).read_text()
        == "schema_version = 1\n"
    )
    assert load_manifest(project_skills) == {}


def test_save_manifest_orders_entries_deterministically(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    save_manifest(
        project_skills,
        {
            "zeta": ManifestEntry(
                name="zeta",
                repo_id="Org/Z",
                repo_url="https://github.com/Org/Z.git",
                source_path="skills/zeta",
                description="Zeta skill.",
            ),
            "alpha": ManifestEntry(
                name="alpha",
                repo_id="Org/A",
                repo_url="https://github.com/Org/A.git",
                source_path="skills/alpha",
                description="Alpha skill.",
            ),
            "beta": ManifestEntry(
                name="beta",
                repo_id="Org/B",
                repo_url="https://github.com/Org/B.git",
                source_path="skills/beta",
                description="Beta skill.",
            ),
        },
    )

    manifest_data = tomllib.loads(
        manifest_module.manifest_path(project_skills).read_text()
    )
    assert [entry["name"] for entry in manifest_data["skills"]] == [
        "alpha",
        "beta",
        "zeta",
    ]


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

    manifest_text = manifest_module.manifest_path(project_skills).read_text()
    assert "Line one\\nLine two\\tTabbed" in manifest_text
    assert load_manifest(project_skills) == entries


def test_load_manifest_reports_malformed_toml_as_sv_error(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    (project_skills / ".sv-manifest.toml").write_text("skills = [\n")

    with pytest.raises(SvError, match="Failed to read sv manifest"):
        load_manifest(project_skills)


def test_load_manifest_rejects_missing_schema_version_in_canonical_manifest(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    manifest_module.manifest_path(project_skills).parent.mkdir(parents=True)
    manifest_module.manifest_path(project_skills).write_text("skills = []\n")

    with pytest.raises(SvError, match="missing 'schema_version'"):
        load_manifest(project_skills)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_load_manifest_rejects_symlinked_canonical_manifest(tmp_path: Path):
    project_skills = tmp_path / "project" / ".pi" / "skills"
    outside = tmp_path / "outside-manifest.toml"
    outside.write_text("schema_version = 1\nskills = []\n", encoding="utf-8")
    path = manifest_module.manifest_path(project_skills)
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)

    with pytest.raises(SvError, match="Refusing to read symlinked sv project manifest"):
        load_manifest(project_skills)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_load_manifest_rejects_symlinked_legacy_manifest(tmp_path: Path):
    project_skills = tmp_path / "project" / ".pi" / "skills"
    outside = tmp_path / "outside-legacy-manifest.toml"
    outside.write_text("[[skills]]\n", encoding="utf-8")
    path = manifest_module.legacy_manifest_path(project_skills)
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)

    with pytest.raises(SvError, match="Refusing to read symlinked legacy sv manifest"):
        load_manifest(project_skills)


def test_load_manifest_rejects_future_schema_with_update_message(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    manifest_module.manifest_path(project_skills).parent.mkdir(parents=True)
    manifest_module.manifest_path(project_skills).write_text(
        "schema_version = 99\nskills = []\n"
    )

    with pytest.raises(SvError) as exc_info:
        load_manifest(project_skills)

    message = str(exc_info.value)
    assert "Unsupported sv project manifest schema_version 99" in message
    assert "update sv" in message.lower()


@pytest.mark.parametrize(
    "field",
    [
        "source_content_hash",
        "source_skill_file_hash",
        "installed_content_hash",
        "local_content_hash",
    ],
)
def test_load_manifest_rejects_malformed_hash_fields(tmp_path: Path, field: str):
    project_skills = tmp_path / ".pi" / "skills"
    path = manifest_module.manifest_path(project_skills)
    path.parent.mkdir(parents=True)
    hashes = {
        "source_content_hash": _valid_hash("source"),
        "source_skill_file_hash": _valid_hash("source-skill-file"),
        "installed_content_hash": _valid_hash("installed"),
        "local_content_hash": _valid_hash("local"),
    }
    hashes[field] = "sha256:nothex"
    path.write_text(
        "schema_version = 1\n"
        "\n"
        "[[skills]]\n"
        'name = "alpha"\n'
        'source_repo_id = "Org/Skills"\n'
        'source_repo_url = "https://github.com/Org/Skills.git"\n'
        'source_path = "skills/alpha"\n'
        'description = "Alpha skill."\n'
        f'source_content_hash = "{hashes["source_content_hash"]}"\n'
        f'source_skill_file_hash = "{hashes["source_skill_file_hash"]}"\n'
        f'installed_content_hash = "{hashes["installed_content_hash"]}"\n'
        f'local_content_hash = "{hashes["local_content_hash"]}"\n'
    )

    with pytest.raises(SvError, match=f"field '{field}' must be a sha256 digest"):
        load_manifest(project_skills)


def test_save_manifest_rejects_malformed_hash_fields(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"

    with pytest.raises(
        SvError, match="field 'source_content_hash' must be a sha256 digest"
    ):
        save_manifest(
            project_skills,
            {
                "alpha": ManifestEntry(
                    name="alpha",
                    repo_id="Org/Skills",
                    repo_url="https://github.com/Org/Skills.git",
                    source_path="skills/alpha",
                    description="Alpha skill.",
                    source_content_hash="sha256:nothex",
                )
            },
        )


@pytest.mark.parametrize("field", ["index_hash", "catalog_hash"])
def test_load_global_manifest_rejects_malformed_hash_fields(
    tmp_path: Path, field: str
):
    paths = SvPaths.from_home(tmp_path)
    hashes = {
        "index_hash": _valid_hash("index"),
        "catalog_hash": _valid_hash("catalog"),
    }
    hashes[field] = "sha256:nothex"
    paths.global_manifest_file.parent.mkdir(parents=True)
    paths.global_manifest_file.write_text(
        "schema_version = 1\n"
        "\n"
        "[[sources]]\n"
        'repo_id = "Org/Skills"\n'
        'repo_url = "https://github.com/Org/Skills.git"\n'
        f'index_hash = "{hashes["index_hash"]}"\n'
        f'catalog_hash = "{hashes["catalog_hash"]}"\n'
    )

    with pytest.raises(SvError, match=f"field '{field}' must be a sha256 digest"):
        load_global_manifest(paths)


def test_save_global_manifest_rejects_malformed_hash_fields(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)

    with pytest.raises(SvError, match="field 'index_hash' must be a sha256 digest"):
        save_global_manifest(
            paths,
            {
                "Org/Skills": GlobalSourceState(
                    repo_id="Org/Skills",
                    repo_url="https://github.com/Org/Skills.git",
                    index_hash="sha256:nothex",
                )
            },
        )


def test_load_manifest_reads_legacy_manifest_when_canonical_is_missing(
    tmp_path: Path,
):
    project_skills = tmp_path / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    (project_skills / ".sv-manifest.toml").write_text(
        "[[skills]]\n"
        'name = "alpha"\n'
        'repo_id = "Org/Skills"\n'
        'repo_url = "https://github.com/Org/Skills.git"\n'
        'description = "Alpha skill."\n'
    )

    assert load_manifest(project_skills)["alpha"] == ManifestEntry(
        name="alpha",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        source_path="skills/alpha",
        description="Alpha skill.",
        target_kind="project-agent",
        target_agent="pi",
        target_path=".pi/skills/alpha",
    )


def test_save_manifest_migrates_legacy_manifest_to_canonical_path(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    (project_skills / ".sv-manifest.toml").write_text(
        "[[skills]]\n"
        'name = "alpha"\n'
        'repo_id = "Org/Skills"\n'
        'repo_url = "https://github.com/Org/Skills.git"\n'
        'description = "Alpha skill."\n'
    )

    upsert_manifest_entry(
        project_skills,
        ManifestEntry(
            name="beta",
            repo_id="Org/Skills",
            repo_url="https://github.com/Org/Skills.git",
            source_path="skills/beta",
            description="Beta skill.",
        ),
    )

    assert manifest_module.manifest_path(project_skills).is_file()
    assert sorted(load_manifest(project_skills)) == ["alpha", "beta"]


def test_load_manifest_reads_legacy_manifest_from_project_root_argument(
    tmp_path: Path,
):
    project_skills = tmp_path / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    (project_skills / ".sv-manifest.toml").write_text(
        "[[skills]]\n"
        'name = "alpha"\n'
        'repo_id = "Org/Skills"\n'
        'repo_url = "https://github.com/Org/Skills.git"\n'
        'description = "Alpha skill."\n'
    )

    assert sorted(load_manifest(tmp_path)) == ["alpha"]


def test_load_manifest_rejects_canonical_invalid_entries_as_sv_error(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    manifest_module.manifest_path(project_skills).parent.mkdir(parents=True)
    manifest_module.manifest_path(project_skills).write_text('schema_version = 1\nskills = ["alpha"]\n')

    with pytest.raises(
        SvError, match=r"sv project manifest.*skills\[1\] must be a table"
    ):
        load_manifest(project_skills)


def test_load_manifest_defaults_missing_source_path_to_simple_skill_path(
    tmp_path: Path,
):
    project_skills = tmp_path / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    (project_skills / ".sv-manifest.toml").write_text(
        "[[skills]]\n"
        'name = "alpha"\n'
        'repo_id = "Org/Skills"\n'
        'repo_url = "https://github.com/Org/Skills.git"\n'
        'description = "Alpha skill."\n'
    )

    manifest = load_manifest(project_skills)

    assert manifest["alpha"] == ManifestEntry(
        name="alpha",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        source_path="skills/alpha",
        description="Alpha skill.",
    )


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
    (tmp_path / ".sv").write_text("not a directory\n")

    with pytest.raises(SvError, match="Failed to write sv manifest"):
        save_manifest(project_skills, {})


def test_save_manifest_refuses_symlinked_temp_file_without_writing_target(
    tmp_path: Path,
):
    project_skills = tmp_path / ".pi" / "skills"
    manifest_path = manifest_module.manifest_path(project_skills)
    manifest_path.parent.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("do not overwrite\n")
    (manifest_path.parent / "manifest.toml.tmp").symlink_to(outside)

    with pytest.raises(SvError, match="temporary manifest path"):
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

    assert outside.read_text() == "do not overwrite\n"
    assert not manifest_path.exists()


def test_save_manifest_refuses_symlinked_sv_directory(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    outside = tmp_path / "outside-sv"
    outside.mkdir()
    (tmp_path / ".sv").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SvError, match="symlinked sv manifest directory"):
        save_manifest(project_skills, {"alpha": _manifest_entry("alpha")})

    assert not (outside / "manifest.toml").exists()


def test_save_manifest_empty_fails_for_invalid_parent_path(tmp_path: Path, monkeypatch):
    project_skills = tmp_path / ".pi" / "skills"
    save_manifest(
        project_skills,
        {
            "alpha": ManifestEntry(
                name="alpha",
                repo_id="Org/Skills",
                repo_url="https://github.com/Org/Skills.git",
                source_path="skills/alpha",
                description="Alpha skill.",
            )
        },
    )

    expected_manifest = manifest_module.manifest_path(project_skills)
    original_manifest_text = expected_manifest.read_text()
    invalid_parent = tmp_path / "invalid-parent"
    invalid_parent.write_text("not a directory\n")
    invalid_manifest_path = invalid_parent / ".sv" / "manifest.toml"

    with monkeypatch.context() as m:
        m.setattr(manifest_module, "manifest_path", lambda _path: invalid_manifest_path)
        with pytest.raises(SvError, match="Failed to write sv manifest"):
            save_manifest(project_skills, {})

    assert expected_manifest.read_text() == original_manifest_text
    assert not (expected_manifest.parent / "manifest.toml.tmp").exists()
    assert not invalid_parent.joinpath(".sv", "manifest.toml.tmp").exists()


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
    manifest_path = manifest_module.manifest_path(project_skills)
    original_text = manifest_path.read_text()
    real_close = tomlutil_module.os.close

    class BrokenFile:
        def __init__(self, fd: int):
            self.fd = fd

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def write(self, text: str) -> None:
            real_close(self.fd)
            raise OSError("write failed")

    monkeypatch.setattr(
        tomlutil_module.os,
        "fdopen",
        lambda fd, *args, **kwargs: BrokenFile(fd),
    )

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

    assert manifest_path.read_text() == original_text
    assert not (manifest_path.parent / "manifest.toml.tmp").exists()


def test_load_manifest_rejects_skills_value_that_is_not_a_list(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    (project_skills / ".sv-manifest.toml").write_text("[skills]\n")

    with pytest.raises(SvError, match="skills must be a list"):
        load_manifest(project_skills)


def test_load_manifest_rejects_non_table_skill_entries(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    (project_skills / ".sv-manifest.toml").write_text('skills = ["alpha"]\n')

    with pytest.raises(SvError, match=r"skills\[1\] must be a table"):
        load_manifest(project_skills)


def test_remove_manifest_entry_missing_skill_keeps_manifest(tmp_path: Path):
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
    original = manifest_module.manifest_path(project_skills).read_text()

    remove_manifest_entry(project_skills, "missing")

    assert manifest_module.manifest_path(project_skills).read_text() == original
    assert sorted(load_manifest(project_skills)) == ["alpha"]


def test_save_manifest_replaces_stale_regular_temp_file(tmp_path: Path):
    project_skills = tmp_path / ".pi" / "skills"
    temp_path = (
        manifest_module.manifest_path(project_skills).parent / "manifest.toml.tmp"
    )
    temp_path.parent.mkdir(parents=True)
    temp_path.write_text("stale\n")

    save_manifest(project_skills, {"alpha": _manifest_entry("alpha")})

    assert load_manifest(project_skills)["alpha"].repo_id == "Org/A"
    assert not temp_path.exists()


def test_save_manifest_removes_temp_file_when_low_level_write_fails(
    tmp_path: Path, monkeypatch
):
    project_skills = tmp_path / ".pi" / "skills"
    temp_path = (
        manifest_module.manifest_path(project_skills).parent / "manifest.toml.tmp"
    )
    real_close = tomlutil_module.os.close

    class BrokenFile:
        def __init__(self, fd: int):
            self.fd = fd

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def write(self, text: str) -> None:
            real_close(self.fd)
            raise OSError("write failed")

    monkeypatch.setattr(
        tomlutil_module.os,
        "fdopen",
        lambda fd, *args, **kwargs: BrokenFile(fd),
    )

    with pytest.raises(SvError, match="Failed to write sv manifest"):
        save_manifest(project_skills, {"alpha": _manifest_entry("alpha")})

    assert not temp_path.exists()


def _manifest_entry(name: str) -> ManifestEntry:
    return ManifestEntry(
        name=name,
        repo_id="Org/A",
        repo_url="https://github.com/Org/A.git",
        source_path=f"skills/{name}",
        description=f"{name.title()} skill.",
    )


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

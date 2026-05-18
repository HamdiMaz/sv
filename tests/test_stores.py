from __future__ import annotations

from pathlib import Path

from sv.config import SvPaths
from sv.manifest import GlobalSourceState, ManifestEntry
from sv.stores import ConfigStore, GlobalManifestStore, ProjectManifestStore


def test_project_manifest_store_round_trips_entries(tmp_path: Path):
    skills_dir = tmp_path / ".pi" / "skills"
    store = ProjectManifestStore(skills_dir)
    entry = ManifestEntry(
        name="alpha",
        repo_id="Org/Repo",
        repo_url="https://github.com/Org/Repo.git",
        source_path="skills/alpha",
        description="Alpha skill.",
        installed_content_hash="sha256:" + "a" * 64,
    )

    store.save({"alpha": entry})

    assert store.load()["alpha"] == entry


def test_global_manifest_store_round_trips_source_state(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    store = GlobalManifestStore(paths)
    state = GlobalSourceState(
        repo_id="Org/Repo",
        repo_url="https://github.com/Org/Repo.git",
        backend="github-https-api",
    )

    store.save({"Org/Repo": state})

    assert store.load()["Org/Repo"] == state


def test_config_store_delegates_to_existing_config_persistence(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    store = ConfigStore(paths)

    result = store.add_repo("Org/Repo")

    assert result.repo.id == "Org/Repo"
    assert store.load().repos[0].id == "Org/Repo"

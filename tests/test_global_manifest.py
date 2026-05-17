from pathlib import Path
import hashlib
import shutil

import pytest

from sv.config import SvPaths
from sv.errors import SvError
from sv.manifest import (
    GlobalSourceState,
    ManifestEntry,
    load_global_manifest,
    save_global_manifest,
    save_manifest,
)
from sv.cli import _update_sources_and_catalog_from_repos
from sv.config import RepoConfig
from sv.source import FakeSourceBackend, default_runner
from tests.helpers import configure_source, make_source_repo


def test_global_manifest_path_lives_under_sv_home(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)

    assert paths.global_manifest_file == tmp_path / ".sv" / "manifest.toml"


def test_load_global_manifest_returns_empty_when_missing(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)

    assert load_global_manifest(paths) == {}


def test_save_and_load_global_source_state_deterministically(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    states = {
        "Org/B": GlobalSourceState(
            repo_id="Org/B",
            repo_url="https://github.com/Org/B.git",
            backend="github-api",
            last_refresh_started_at="2026-05-15T00:00:00Z",
            last_refresh_finished_at="2026-05-15T00:00:02Z",
            last_refresh_status="ok",
            index_hash="sha256:921900bd3d9be61b88f5c38962d1e011e59a730c0cbf9dd910c4db3acda91a80",
            catalog_hash="sha256:65e9d6f275b5d83438804bef52cbd7e7611ec467ab4b4a864659240746ca5634",
            catalog_skill_count=3,
            health_status="ok",
            health_details="catalog refreshed",
        ),
        "Org/A": GlobalSourceState(
            repo_id="Org/A",
            repo_url="https://github.com/Org/A.git",
            backend="git",
            last_refresh_status="error",
            last_refresh_error="rate limited",
        ),
    }

    save_global_manifest(paths, states)

    assert paths.global_manifest_file.read_text() == (
        "schema_version = 1\n"
        "\n"
        "[[sources]]\n"
        'repo_id = "Org/A"\n'
        'repo_url = "https://github.com/Org/A.git"\n'
        'backend = "git"\n'
        'last_refresh_status = "error"\n'
        'last_refresh_error = "rate limited"\n'
        "\n"
        "[[sources]]\n"
        'repo_id = "Org/B"\n'
        'repo_url = "https://github.com/Org/B.git"\n'
        'backend = "github-api"\n'
        'last_refresh_started_at = "2026-05-15T00:00:00Z"\n'
        'last_refresh_finished_at = "2026-05-15T00:00:02Z"\n'
        'last_refresh_status = "ok"\n'
        'index_hash = "sha256:921900bd3d9be61b88f5c38962d1e011e59a730c0cbf9dd910c4db3acda91a80"\n'
        'catalog_hash = "sha256:65e9d6f275b5d83438804bef52cbd7e7611ec467ab4b4a864659240746ca5634"\n'
        "catalog_skill_count = 3\n"
        'health_status = "ok"\n'
        'health_details = "catalog refreshed"\n'
    )
    assert load_global_manifest(paths) == states


def test_global_manifest_does_not_read_project_managed_skill_state(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    paths = SvPaths.from_home(home)
    (project / ".sv").mkdir(parents=True)
    (project / ".sv" / "manifest.toml").write_text(
        "schema_version = 1\n"
        "\n"
        "[[skills]]\n"
        'name = "alpha"\n'
        'source_repo_id = "Org/Skills"\n'
        'source_repo_url = "https://github.com/Org/Skills.git"\n'
        'description = "Alpha skill."\n'
    )

    assert load_global_manifest(paths) == {}
    assert not paths.global_manifest_file.exists()


def test_load_global_manifest_rejects_missing_schema_version(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.global_manifest_file.parent.mkdir(parents=True)
    paths.global_manifest_file.write_text("sources = []\n")

    with pytest.raises(SvError, match="missing 'schema_version'"):
        load_global_manifest(paths)


def test_load_global_manifest_rejects_future_schema_with_update_message(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.global_manifest_file.parent.mkdir(parents=True)
    paths.global_manifest_file.write_text("schema_version = 99\nsources = []\n")

    with pytest.raises(SvError) as exc_info:
        load_global_manifest(paths)

    message = str(exc_info.value)
    assert "Unsupported sv global manifest schema_version 99" in message
    assert "update sv" in message.lower()


def test_list_refresh_records_global_source_state(
    tmp_path: Path, run_sv
) -> None:
    source = make_source_repo(tmp_path)
    project = tmp_path / "project"
    home = tmp_path / "home"
    configure_source(source, project, home)

    result = run_sv(["list"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    paths = SvPaths.from_home(home)
    states = load_global_manifest(paths)
    repo_id = next(iter(states))
    state = states[repo_id]
    assert state.repo_id == repo_id
    assert state.repo_url == str(source)
    assert state.backend == "git-local-source"
    assert state.last_refresh_started_at is not None
    assert state.last_refresh_status == "ok"
    assert state.last_refresh_finished_at is not None
    assert state.source_commit is not None
    assert state.source_tree is not None
    assert state.catalog_hash is not None
    assert state.catalog_hash.startswith("sha256:")
    assert state.catalog_skill_count == 2
    assert state.health_status == "ok"
    assert state.health_details == "catalog contains 2 skills"


def test_lightweight_refresh_records_backend_and_index_hash(
    tmp_path: Path, monkeypatch
) -> None:
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    paths = SvPaths.from_home(tmp_path / "home")
    index_content = (
        b"schema_version = 1\n"
        b"kind = \"skill-vault\"\n"
        b"generated_by = \"sv\"\n"
        b"generated_at = \"2026-05-15T00:00:00Z\"\n"
        b"\n"
        b"[[skills]]\n"
        b"name = \"alpha\"\n"
        b"description = \"Alpha skill.\"\n"
        b"source_path = \"skills/alpha\"\n"
        b"content_hash = \"sha256:ed7002b439e9ac845f22357d822bac1444730fbdb6016d3ec9432297b9ec9f73\"\n"
        b"skill_file_hash = \"sha256:fac608e8879fb4f49e6adeb8a35e48d431e8e3d9fbd3c50416a805cf70403926\"\n"
    )

    class GitHubApiBackend(FakeSourceBackend):
        name = "github-api"

    def fake_backends_for_repo(repo_config, paths_arg, *, runner, update):
        assert repo_config == repo
        assert paths_arg == paths
        assert update is True
        return (GitHubApiBackend({".sv/index.toml": index_content}),)

    monkeypatch.setattr("sv.cli.source_backends_for_repo", fake_backends_for_repo)

    _update_sources_and_catalog_from_repos(
        [repo],
        paths,
        default_runner,
        update=True,
        record_global_source_state=True,
        lightweight_discovery=True,
    )

    state = load_global_manifest(paths)[repo.id]
    assert state.backend == "github-api"
    assert state.index_hash == f"sha256:{hashlib.sha256(index_content).hexdigest()}"


def test_lightweight_refresh_records_backend_and_index_hash_for_equivalent_sources(
    tmp_path: Path, monkeypatch
) -> None:
    primary = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    alias = RepoConfig(id="Alias/Skills", url="https://github.com/Org/Skills")
    paths = SvPaths.from_home(tmp_path / "home")
    index_content = (
        b"schema_version = 1\n"
        b"kind = \"skill-vault\"\n"
        b"generated_by = \"sv\"\n"
        b"generated_at = \"2026-05-15T00:00:00Z\"\n"
        b"\n"
        b"[[skills]]\n"
        b"name = \"alpha\"\n"
        b"description = \"Alpha skill.\"\n"
        b"source_path = \"skills/alpha\"\n"
        b"content_hash = \"sha256:ed7002b439e9ac845f22357d822bac1444730fbdb6016d3ec9432297b9ec9f73\"\n"
        b"skill_file_hash = \"sha256:fac608e8879fb4f49e6adeb8a35e48d431e8e3d9fbd3c50416a805cf70403926\"\n"
    )

    class GitHubApiBackend(FakeSourceBackend):
        name = "github-api"

    def fake_backends_for_repo(repo_config, paths_arg, *, runner, update):
        assert paths_arg == paths
        assert update is True
        return (GitHubApiBackend({".sv/index.toml": index_content}),)

    monkeypatch.setattr("sv.cli.source_backends_for_repo", fake_backends_for_repo)

    _update_sources_and_catalog_from_repos(
        [primary, alias],
        paths,
        default_runner,
        update=True,
        record_global_source_state=True,
        lightweight_discovery=True,
    )

    states = load_global_manifest(paths)
    expected_hash = f"sha256:{hashlib.sha256(index_content).hexdigest()}"
    assert states[primary.id].backend == "github-api"
    assert states[primary.id].index_hash == expected_hash
    assert states[alias.id].backend == "github-api"
    assert states[alias.id].index_hash == expected_hash
    assert states[alias.id].catalog_skill_count == 1
    assert states[alias.id].catalog_hash == states[primary.id].catalog_hash
    assert states[alias.id].health_details == "catalog contains 1 skills"


def test_list_from_home_without_project_manifest_records_global_source_state(
    tmp_path: Path, run_sv
) -> None:
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    configure_source(source, home, home)

    result = run_sv(["list"], cwd=home, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    states = load_global_manifest(SvPaths.from_home(home))
    assert len(states) == 1
    state = next(iter(states.values()))
    assert state.repo_url == str(source)
    assert state.last_refresh_status == "ok"
    assert state.catalog_skill_count == 2


def test_failed_refresh_records_global_source_health(
    tmp_path: Path, run_sv
) -> None:
    source = make_source_repo(tmp_path)
    project = tmp_path / "project"
    home = tmp_path / "home"
    configure_source(source, project, home)
    first_result = run_sv(["list"], cwd=project, home=home, git_runner=default_runner)
    assert first_result.exit_code == 0

    shutil.rmtree(source / ".git")

    result = run_sv(["list"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 1
    state = next(iter(load_global_manifest(SvPaths.from_home(home)).values()))
    assert state.last_refresh_started_at is not None
    assert state.last_refresh_finished_at is not None
    assert state.last_refresh_status == "error"
    assert state.last_refresh_error is not None
    assert "not a Git working tree" in state.last_refresh_error
    assert state.health_status == "error"
    assert state.health_details is not None
    assert "not a Git working tree" in state.health_details


def test_repo_remove_prunes_global_source_state(tmp_path: Path, run_sv) -> None:
    source = make_source_repo(tmp_path)
    project = tmp_path / "project"
    home = tmp_path / "home"
    configure_source(source, project, home)
    list_result = run_sv(["list"], cwd=project, home=home, git_runner=default_runner)
    assert list_result.exit_code == 0
    paths = SvPaths.from_home(home)
    repo_id = next(iter(load_global_manifest(paths)))

    result = run_sv(["repo", "remove", repo_id], cwd=project, home=home)

    assert result.exit_code == 0
    assert load_global_manifest(paths) == {}


def test_list_in_empty_home_project_does_not_overwrite_project_manifest(
    tmp_path: Path, run_sv
) -> None:
    home = tmp_path / "home"
    source = make_source_repo(tmp_path)
    configure_source(source, home, home)
    save_manifest(home / ".pi" / "skills", {})
    project_manifest_text = (home / ".sv" / "manifest.toml").read_text()

    result = run_sv(["list"], cwd=home, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert (home / ".sv" / "manifest.toml").read_text() == project_manifest_text


def test_list_in_home_project_does_not_overwrite_project_manifest(
    tmp_path: Path, run_sv
) -> None:
    home = tmp_path / "home"
    source = make_source_repo(tmp_path)
    configure_source(source, home, home)
    project_skills = home / ".pi" / "skills"
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
    project_manifest_text = (home / ".sv" / "manifest.toml").read_text()

    result = run_sv(["list"], cwd=home, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert (home / ".sv" / "manifest.toml").read_text() == project_manifest_text


def test_list_from_other_project_does_not_treat_home_project_manifest_as_global(
    tmp_path: Path, run_sv
) -> None:
    home = tmp_path / "home"
    other_project = tmp_path / "other-project"
    source = make_source_repo(tmp_path)
    configure_source(source, other_project, home)
    save_manifest(
        home / ".pi" / "skills",
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
    project_manifest_text = (home / ".sv" / "manifest.toml").read_text()

    result = run_sv(
        ["list"], cwd=other_project, home=home, git_runner=default_runner
    )

    assert result.exit_code == 0
    assert (home / ".sv" / "manifest.toml").read_text() == project_manifest_text


def test_load_global_manifest_rejects_project_skill_entries(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.global_manifest_file.parent.mkdir(parents=True)
    paths.global_manifest_file.write_text(
        "schema_version = 1\n"
        "\n"
        "[[skills]]\n"
        'name = "alpha"\n'
        'source_repo_id = "Org/Skills"\n'
    )

    with pytest.raises(SvError, match="must not contain project skill state"):
        load_global_manifest(paths)

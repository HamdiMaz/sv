from pathlib import Path

import pytest

from sv.index import load_index
from sv.source import default_runner
from tests.helpers import configure_source, make_source_repo, run_git, write_source_skill


pytestmark = pytest.mark.integration


def _write_index(repo: Path, kind: str) -> None:
    (repo / ".sv").mkdir(parents=True, exist_ok=True)
    (repo / ".sv" / "index.toml").write_text(
        "schema_version = 1\n"
        f'kind = "{kind}"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n',
        encoding="utf-8",
    )


def _make_pi_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".pi").mkdir()
    return project


def test_normal_project_add_without_existing_index_does_not_create_index(
    tmp_path: Path, run_sv
) -> None:
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = _make_pi_project(tmp_path)
    configure_source(source, project, home)

    result = run_sv(["add", "alpha"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert (project / ".pi" / "skills" / "alpha" / "SKILL.md").is_file()
    assert not (project / ".sv" / "index.toml").exists()


def test_normal_project_add_with_existing_index_refreshes_index(
    tmp_path: Path, run_sv
) -> None:
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = _make_pi_project(tmp_path)
    _write_index(project, "project-index")
    configure_source(source, project, home)

    result = run_sv(["add", "alpha"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    index = load_index(project / ".sv" / "index.toml")
    assert index.kind == "project-index"
    assert [(entry.name, entry.source_path) for entry in index.skills] == [
        ("alpha", ".pi/skills/alpha")
    ]
    assert not (project / "README.md").exists()


def test_normal_project_index_refresh_honors_persistent_scan_config(
    tmp_path: Path, run_sv
) -> None:
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = _make_pi_project(tmp_path)
    _write_index(project, "project-index")
    (project / ".sv" / "index-config.toml").write_text(
        "schema_version = 1\n"
        'include_paths = ["published"]\n',
        encoding="utf-8",
    )
    published = project / "published" / "manual"
    published.mkdir(parents=True)
    (published / "SKILL.md").write_text(
        "---\nname: manual\ndescription: Manual skill.\n---\n",
        encoding="utf-8",
    )
    configure_source(source, project, home)

    result = run_sv(["add", "alpha"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    index = load_index(project / ".sv" / "index.toml")
    assert [(entry.name, entry.source_path) for entry in index.skills] == [
        ("manual", "published/manual")
    ]
    assert (project / ".pi" / "skills" / "alpha" / "SKILL.md").is_file()


def test_add_validates_persistent_scan_config_before_mutating_project(
    tmp_path: Path, run_sv
) -> None:
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = _make_pi_project(tmp_path)
    _write_index(project, "project-index")
    (project / ".sv" / "index-config.toml").write_text(
        "schema_version = 1\n"
        'include_paths = ["../outside"]\n',
        encoding="utf-8",
    )
    configure_source(source, project, home)

    result = run_sv(["add", "alpha"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 1
    assert "include_paths[1] is invalid" in result.stderr
    assert not (project / ".pi" / "skills" / "alpha").exists()


def test_add_rejects_symlinked_persistent_include_before_mutating_project(
    tmp_path: Path, run_sv
) -> None:
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = _make_pi_project(tmp_path)
    _write_index(project, "project-index")
    outside = tmp_path / "outside-skills"
    outside.mkdir()
    (project / "linked-skills").symlink_to(outside, target_is_directory=True)
    (project / ".sv" / "index-config.toml").write_text(
        "schema_version = 1\n"
        'include_paths = ["linked-skills"]\n',
        encoding="utf-8",
    )
    configure_source(source, project, home)

    result = run_sv(["add", "alpha"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 1
    assert "Refusing to scan symlinked include path" in result.stderr
    assert not (project / ".pi" / "skills" / "alpha").exists()


def test_remove_validates_persistent_scan_config_before_mutating_project(
    tmp_path: Path, run_sv
) -> None:
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = _make_pi_project(tmp_path)
    _write_index(project, "project-index")
    configure_source(source, project, home)
    add_result = run_sv(["add", "alpha"], cwd=project, home=home, git_runner=default_runner)
    assert add_result.exit_code == 0
    (project / ".sv" / "index-config.toml").write_text(
        "schema_version = 1\n"
        'exclude_paths = ["../outside"]\n',
        encoding="utf-8",
    )

    result = run_sv(["remove", "alpha"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 1
    assert "exclude_paths[1] is invalid" in result.stderr
    assert (project / ".pi" / "skills" / "alpha" / "SKILL.md").is_file()


def test_add_all_validates_persistent_scan_config_before_mutating_project(
    tmp_path: Path, run_sv
) -> None:
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = _make_pi_project(tmp_path)
    _write_index(project, "project-index")
    (project / ".sv" / "index-config.toml").write_text(
        "schema_version = 1\n"
        'include_paths = ["../outside"]\n',
        encoding="utf-8",
    )
    configure_source(source, project, home)

    result = run_sv(["add", "--all"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 1
    assert "include_paths[1] is invalid" in result.stderr
    assert not (project / ".pi" / "skills" / "alpha").exists()
    assert not (project / ".pi" / "skills" / "beta").exists()


def test_remove_all_validates_persistent_scan_config_before_mutating_project(
    tmp_path: Path, run_sv
) -> None:
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = _make_pi_project(tmp_path)
    _write_index(project, "project-index")
    configure_source(source, project, home)
    add_result = run_sv(["add", "--all"], cwd=project, home=home, git_runner=default_runner)
    assert add_result.exit_code == 0
    (project / ".sv" / "index-config.toml").write_text(
        "schema_version = 1\n"
        'exclude_paths = ["../outside"]\n',
        encoding="utf-8",
    )

    result = run_sv(
        ["remove", "--all", "--yes"], cwd=project, home=home, git_runner=default_runner
    )

    assert result.exit_code == 1
    assert "exclude_paths[1] is invalid" in result.stderr
    assert (project / ".pi" / "skills" / "alpha" / "SKILL.md").is_file()
    assert (project / ".pi" / "skills" / "beta" / "SKILL.md").is_file()


@pytest.mark.parametrize("command", ["sync", "update"])
def test_sync_and_update_validate_persistent_scan_config_before_mutating_project(
    tmp_path: Path, run_sv, command: str
) -> None:
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = _make_pi_project(tmp_path)
    _write_index(project, "project-index")
    configure_source(source, project, home)
    add_result = run_sv(["add", "alpha"], cwd=project, home=home, git_runner=default_runner)
    assert add_result.exit_code == 0
    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)
    (project / ".sv" / "index-config.toml").write_text(
        "schema_version = 1\n"
        'exclude_paths = ["../outside"]\n',
        encoding="utf-8",
    )

    result = run_sv([command], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 1
    assert "exclude_paths[1] is invalid" in result.stderr
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == "alpha v1\n"


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".git").mkdir()
    _write_index(vault, "skill-vault")
    (vault / "README.md").write_text(
        "# Vault\n\n"
        "custom intro\n\n"
        "<!-- sv:skills:start -->\n"
        "stale generated text\n"
        "<!-- sv:skills:end -->\n"
        "\ncustom outro\n",
        encoding="utf-8",
    )
    return vault


def test_vault_add_refreshes_index_and_readme_generated_block(
    tmp_path: Path, run_sv
) -> None:
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_vault(tmp_path)
    configure_source(source, vault, home)

    result = run_sv(["add", "alpha"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    index = load_index(vault / ".sv" / "index.toml")
    assert index.kind == "skill-vault"
    assert [(entry.name, entry.source_path) for entry in index.skills] == [
        ("alpha", "skills/alpha")
    ]
    readme = (vault / "README.md").read_text(encoding="utf-8")
    assert "custom intro" in readme
    assert "custom outro" in readme
    assert "stale generated text" not in readme
    assert "| alpha | Alpha skill. |" in readme


def test_vault_remove_refreshes_index_and_readme_generated_block(
    tmp_path: Path, run_sv
) -> None:
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_vault(tmp_path)
    configure_source(source, vault, home)
    add_result = run_sv(["add", "alpha"], cwd=vault, home=home, git_runner=default_runner)
    assert add_result.exit_code == 0

    result = run_sv(["remove", "alpha"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    index = load_index(vault / ".sv" / "index.toml")
    assert index.kind == "skill-vault"
    assert index.skills == ()
    readme = (vault / "README.md").read_text(encoding="utf-8")
    assert "custom intro" in readme
    assert "custom outro" in readme
    assert "| alpha | Alpha skill. |" not in readme


@pytest.mark.parametrize("command", ["sync", "update"])
def test_vault_sync_and_update_refresh_index_and_readme_generated_block(
    tmp_path: Path, run_sv, command: str
) -> None:
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_vault(tmp_path)
    configure_source(source, vault, home)
    add_result = run_sv(["add", "alpha"], cwd=vault, home=home, git_runner=default_runner)
    assert add_result.exit_code == 0
    write_source_skill(source, "alpha", "Updated alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)

    result = run_sv([command], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    index = load_index(vault / ".sv" / "index.toml")
    assert [(entry.name, entry.description, entry.source_path) for entry in index.skills] == [
        ("alpha", "Updated alpha skill.", "skills/alpha")
    ]
    readme = (vault / "README.md").read_text(encoding="utf-8")
    assert "| alpha | Updated alpha skill. |" in readme

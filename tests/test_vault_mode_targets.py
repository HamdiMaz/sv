from pathlib import Path
import os
import sys

import pytest

from sv.config import SvPaths, load_config
from sv.manifest import ManifestEntry, load_manifest, save_manifest
from sv.source import default_runner
from tests.helpers import (
    assert_no_raw_control_characters,
    assert_no_traceback,
    configure_source,
    make_source_repo,
    run_git,
    write_source_skill,
)


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


def _make_git_repo(path: Path) -> Path:
    (path / ".git").mkdir(parents=True)
    return path


def _assert_clean_output(result) -> None:
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stdout)
    assert_no_raw_control_characters(result.stderr)


def test_vault_add_from_subdirectory_installs_to_repo_root_skills(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_git_repo(tmp_path / "vault")
    _write_index(vault, "skill-vault")
    nested = vault / "nested" / "deeper"
    nested.mkdir(parents=True)
    configure_source(source, vault, home)

    result = run_sv(["add", "alpha"], cwd=nested, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    _assert_clean_output(result)
    target = vault / "skills" / "alpha"
    assert (target / "notes.md").read_text() == "alpha v1\n"
    assert not (nested / ".pi" / "skills" / "alpha").exists()
    assert not (vault / ".pi" / "skills" / "alpha").exists()
    assert "Added vault skill 'alpha'" in result.stdout
    assert f"to {target}" in result.stdout
    manifest = load_manifest(vault / "skills")
    assert manifest["alpha"].target_kind == "skill-vault"
    assert manifest["alpha"].target_agent is None
    assert manifest["alpha"].target_path == "skills/alpha"


def test_vault_add_all_installs_all_skills_to_repo_root(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_git_repo(tmp_path / "vault")
    _write_index(vault, "skill-vault")
    configure_source(source, vault, home)

    result = run_sv(["add", "--all"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    _assert_clean_output(result)
    assert (vault / "skills" / "alpha" / "notes.md").read_text() == "alpha v1\n"
    assert (vault / "skills" / "beta" / "notes.md").read_text() == "beta v1\n"
    assert "Added vault skill 'alpha'" in result.stdout
    assert "Added vault skill 'beta'" in result.stdout
    assert not (vault / ".pi" / "skills").exists()


@pytest.mark.integration
def test_non_tty_vault_add_refuses_existing_target_without_replace(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_git_repo(tmp_path / "vault")
    _write_index(vault, "skill-vault")
    configure_source(source, vault, home)
    first = run_sv(["add", "alpha"], cwd=vault, home=home, git_runner=default_runner)
    assert first.exit_code == 0
    target = vault / "skills" / "alpha"
    (target / "notes.md").write_text("local vault edit\n")

    result = run_sv(["add", "alpha"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 1
    _assert_clean_output(result)
    assert "Use --replace to replace it" in result.stderr
    assert (target / "notes.md").read_text() == "local vault edit\n"


@pytest.mark.integration
def test_non_tty_vault_add_replace_warns_before_replacing_local_edits(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_git_repo(tmp_path / "vault")
    _write_index(vault, "skill-vault")
    configure_source(source, vault, home)
    first = run_sv(["add", "alpha"], cwd=vault, home=home, git_runner=default_runner)
    assert first.exit_code == 0
    target = vault / "skills" / "alpha"
    (target / "notes.md").write_text("local vault edit\n")

    result = run_sv(["add", "alpha", "--replace"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    _assert_clean_output(result)
    assert "warning:" in result.stderr
    assert "local edits" in result.stderr
    assert "Replaced vault skill 'alpha'" in result.stdout
    assert (target / "notes.md").read_text() == "alpha v1\n"


@pytest.mark.integration
def test_vault_add_replace_warns_for_unmanaged_existing_target(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_git_repo(tmp_path / "vault")
    _write_index(vault, "skill-vault")
    target = vault / "skills" / "alpha"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Local alpha.\n---\n", encoding="utf-8"
    )
    (target / "notes.md").write_text("unmanaged local alpha\n")
    configure_source(source, vault, home)

    result = run_sv(["add", "alpha", "--replace"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    _assert_clean_output(result)
    assert "warning:" in result.stderr
    assert "local edits" in result.stderr
    assert "Replaced vault skill 'alpha'" in result.stdout
    assert (target / "notes.md").read_text() == "alpha v1\n"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
@pytest.mark.integration
def test_tty_vault_add_rejects_symlinked_skills_dir_before_prompting(
    tmp_path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_git_repo(tmp_path / "vault")
    _write_index(vault, "skill-vault")
    outside_skills = tmp_path / "outside-skills"
    outside_alpha = outside_skills / "alpha"
    outside_alpha.mkdir(parents=True)
    (outside_alpha / "notes.md").write_text("outside alpha\n")
    (vault / "skills").symlink_to(outside_skills, target_is_directory=True)
    configure_source(source, vault, home)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    def fail_if_prompted(prompt):
        raise AssertionError(f"unexpected replacement prompt: {prompt}")

    monkeypatch.setattr("builtins.input", fail_if_prompted)

    result = run_sv(["add", "alpha"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 1
    _assert_clean_output(result)
    assert "Refusing to use symlinked vault skills path" in result.stderr
    assert (outside_alpha / "notes.md").read_text() == "outside alpha\n"


@pytest.mark.integration
def test_tty_vault_add_prompts_before_replacing_existing_target(
    tmp_path, run_sv, monkeypatch
):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_git_repo(tmp_path / "vault")
    _write_index(vault, "skill-vault")
    configure_source(source, vault, home)
    first = run_sv(["add", "alpha"], cwd=vault, home=home, git_runner=default_runner)
    assert first.exit_code == 0
    target = vault / "skills" / "alpha"
    (target / "notes.md").write_text("local vault edit\n")
    prompts = []
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    def confirm_replace(prompt):
        prompts.append(prompt)
        assert (target / "notes.md").read_text() == "local vault edit\n"
        return "y"

    monkeypatch.setattr("builtins.input", confirm_replace)

    result = run_sv(["add", "alpha"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    _assert_clean_output(result)
    assert prompts == ["Replace existing vault skill 'alpha'? [y/N]: "]
    assert "Vault skill 'alpha' already exists" in result.stdout
    assert "Replaced vault skill 'alpha'" in result.stdout
    assert "local edits" in result.stderr
    assert (target / "notes.md").read_text() == "alpha v1\n"


def test_project_index_add_preserves_pi_project_target(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = _make_git_repo(tmp_path / "project")
    _write_index(project, "project-index")
    configure_source(source, project, home)

    result = run_sv(["add", "alpha"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    _assert_clean_output(result)
    target = project / ".pi" / "skills" / "alpha"
    assert (target / "notes.md").read_text() == "alpha v1\n"
    assert not (project / "skills" / "alpha").exists()
    assert "Added Pi skill 'alpha'" in result.stdout
    assert f"to {target}" in result.stdout


def test_vault_remove_preserves_same_name_canonical_pi_manifest_entry(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_git_repo(tmp_path / "vault")
    _write_index(vault, "skill-vault")
    root_skill = vault / "skills" / "alpha"
    root_skill.mkdir(parents=True)
    (root_skill / "notes.md").write_text("local vault alpha\n")
    pi_skills = vault / ".pi" / "skills"
    save_manifest(
        pi_skills,
        {
            "alpha": ManifestEntry(
                name="alpha",
                repo_id="canonical-pi-source",
                repo_url=str(source),
                source_path="skills/alpha",
                description="Alpha skill.",
            )
        },
    )

    result = run_sv(["remove", "alpha"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    _assert_clean_output(result)
    assert not root_skill.exists()
    manifest = load_manifest(pi_skills)
    assert manifest["alpha"].target_kind == "project-agent"
    assert manifest["alpha"].repo_id == "canonical-pi-source"


def test_vault_remove_removes_root_skill_without_touching_pi_skills(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_git_repo(tmp_path / "vault")
    _write_index(vault, "skill-vault")
    pi_skill = vault / ".pi" / "skills" / "alpha"
    pi_skill.mkdir(parents=True)
    (pi_skill / "notes.md").write_text("pi alpha\n")
    configure_source(source, vault, home)
    add_result = run_sv(["add", "alpha"], cwd=vault, home=home, git_runner=default_runner)
    assert add_result.exit_code == 0

    result = run_sv(["remove", "alpha"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    _assert_clean_output(result)
    assert not (vault / "skills" / "alpha").exists()
    assert (pi_skill / "notes.md").read_text() == "pi alpha\n"
    assert "Removed vault skill 'alpha'" in result.stdout


def test_vault_sync_updates_root_skill_target(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_git_repo(tmp_path / "vault")
    _write_index(vault, "skill-vault")
    configure_source(source, vault, home)
    repo_id = load_config(SvPaths.from_home(home)).repos[0].id
    add_result = run_sv(
        ["add", f"{repo_id}:alpha"], cwd=vault, home=home, git_runner=default_runner
    )
    assert add_result.exit_code == 0

    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)

    result = run_sv(["sync"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    _assert_clean_output(result)
    assert (vault / "skills" / "alpha" / "notes.md").read_text() == "alpha v2\n"
    assert "Synced vault skill 'alpha'." in result.stdout


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_context_detection_rejects_symlinked_index(tmp_path, run_sv):
    home = tmp_path / "home"
    project = _make_git_repo(tmp_path / "project")
    outside_index = tmp_path / "outside-index.toml"
    (project / ".sv").mkdir()
    outside_index.write_text("", encoding="utf-8")
    (project / ".sv" / "index.toml").symlink_to(outside_index)

    def forbid_git(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    result = run_sv(["remove", "alpha"], cwd=project, home=home, git_runner=forbid_git)

    assert result.exit_code == 1
    _assert_clean_output(result)
    assert "Refusing to use symlinked sv index" in result.stderr


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_vault_add_rejects_symlinked_sv_dir_before_mutating_target(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_git_repo(tmp_path / "vault")
    outside_sv = tmp_path / "outside-sv"
    _write_index(outside_sv.parent / outside_sv.name, "skill-vault")
    (vault / ".sv").symlink_to(outside_sv, target_is_directory=True)
    configure_source(source, vault, home)

    result = run_sv(["add", "alpha"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 1
    _assert_clean_output(result)
    assert "Refusing to use symlinked sv metadata directory" in result.stderr
    assert not (vault / "skills" / "alpha").exists()


def test_vault_add_preserves_same_name_canonical_pi_manifest_entry(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_git_repo(tmp_path / "vault")
    _write_index(vault, "skill-vault")
    pi_skills = vault / ".pi" / "skills"
    save_manifest(
        pi_skills,
        {
            "alpha": ManifestEntry(
                name="alpha",
                repo_id="canonical-pi-source",
                repo_url=str(source),
                source_path="skills/alpha",
                description="Alpha skill.",
            )
        },
    )
    configure_source(source, vault, home)
    repo_id = load_config(SvPaths.from_home(home)).repos[0].id

    result = run_sv(["add", "alpha"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    _assert_clean_output(result)
    manifest = load_manifest(pi_skills)
    entries = sorted(
        (entry.target_kind, entry.target_path, entry.repo_id)
        for entry in manifest.values()
        if entry.name == "alpha"
    )
    assert entries == [
        ("project-agent", ".pi/skills/alpha", "canonical-pi-source"),
        ("skill-vault", "skills/alpha", repo_id),
    ]


def test_vault_sync_and_remove_use_same_name_vault_manifest_entry(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_git_repo(tmp_path / "vault")
    _write_index(vault, "skill-vault")
    pi_skills = vault / ".pi" / "skills"
    save_manifest(
        pi_skills,
        {
            "alpha": ManifestEntry(
                name="alpha",
                repo_id="canonical-pi-source",
                repo_url=str(source),
                source_path="skills/alpha",
                description="Alpha skill.",
            )
        },
    )
    configure_source(source, vault, home)
    repo_id = load_config(SvPaths.from_home(home)).repos[0].id
    add_result = run_sv(["add", "alpha"], cwd=vault, home=home, git_runner=default_runner)
    assert add_result.exit_code == 0

    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)

    sync_result = run_sv(["sync"], cwd=vault, home=home, git_runner=default_runner)

    assert sync_result.exit_code == 0
    _assert_clean_output(sync_result)
    assert (vault / "skills" / "alpha" / "notes.md").read_text() == "alpha v2\n"
    assert "Synced vault skill 'alpha'." in sync_result.stdout

    remove_result = run_sv(["remove", "alpha"], cwd=vault, home=home, git_runner=default_runner)

    assert remove_result.exit_code == 0
    _assert_clean_output(remove_result)
    assert not (vault / "skills" / "alpha").exists()
    manifest_entries = [entry for entry in load_manifest(pi_skills).values() if entry.name == "alpha"]
    assert [(entry.target_kind, entry.target_path, entry.repo_id) for entry in manifest_entries] == [
        ("project-agent", ".pi/skills/alpha", "canonical-pi-source")
    ]
    assert repo_id not in {entry.repo_id for entry in manifest_entries}


def test_vault_sync_ignores_canonical_pi_manifest_entries(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_git_repo(tmp_path / "vault")
    _write_index(vault, "skill-vault")
    root_skill = vault / "skills" / "alpha"
    root_skill.mkdir(parents=True)
    (root_skill / "notes.md").write_text("local vault alpha\n")
    pi_skills = vault / ".pi" / "skills"
    save_manifest(
        pi_skills,
        {
            "alpha": ManifestEntry(
                name="alpha",
                repo_id="canonical-pi-source",
                repo_url=str(source),
                source_path="skills/alpha",
                description="Alpha skill.",
            )
        },
    )
    configure_source(source, vault, home)

    result = run_sv(["sync"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    _assert_clean_output(result)
    assert (root_skill / "notes.md").read_text() == "local vault alpha\n"
    assert "Skipped local vault skill 'alpha'." in result.stdout
    manifest = load_manifest(pi_skills)
    assert manifest["alpha"].target_kind == "project-agent"
    assert manifest["alpha"].target_path == ".pi/skills/alpha"


def test_vault_sync_ignores_legacy_pi_manifest_entries(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_git_repo(tmp_path / "vault")
    _write_index(vault, "skill-vault")
    root_skill = vault / "skills" / "alpha"
    root_skill.mkdir(parents=True)
    (root_skill / "notes.md").write_text("local vault alpha\n")
    legacy_pi_skills = vault / ".pi" / "skills"
    save_manifest(
        legacy_pi_skills,
        {
            "alpha": ManifestEntry(
                name="alpha",
                repo_id="legacy-pi-source",
                repo_url=str(source),
                source_path="skills/alpha",
                description="Alpha skill.",
            )
        },
    )
    legacy_manifest = vault / ".sv" / "manifest.toml"
    legacy_text = legacy_manifest.read_text(encoding="utf-8")
    legacy_manifest.unlink()
    legacy_pi_skills.mkdir(parents=True)
    (legacy_pi_skills / ".sv-manifest.toml").write_text(
        legacy_text, encoding="utf-8"
    )
    configure_source(source, vault, home)

    result = run_sv(["sync"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    _assert_clean_output(result)
    assert (root_skill / "notes.md").read_text() == "local vault alpha\n"
    assert "Skipped local vault skill 'alpha'." in result.stdout


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_non_git_add_rejects_symlinked_sv_dir_before_mutating_target(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    outside_sv = tmp_path / "outside-sv"
    outside_sv.mkdir()
    (project / ".sv").symlink_to(outside_sv, target_is_directory=True)
    configure_source(source, project, home)

    result = run_sv(["add", "alpha"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 1
    _assert_clean_output(result)
    assert "Refusing to use symlinked sv metadata directory" in result.stderr
    assert not (project / ".pi").exists()
    assert not (project / "skills" / "alpha").exists()


def test_skill_vault_index_without_git_root_preserves_pi_project_target(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    _write_index(project, "skill-vault")
    configure_source(source, project, home)

    result = run_sv(["add", "alpha"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    _assert_clean_output(result)
    assert (project / ".pi" / "skills" / "alpha" / "notes.md").read_text() == "alpha v1\n"
    assert not (project / "skills" / "alpha").exists()
    assert "Added Pi skill 'alpha'" in result.stdout


def test_vault_update_routes_update_to_root_skill_target(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    vault = _make_git_repo(tmp_path / "vault")
    _write_index(vault, "skill-vault")
    configure_source(source, vault, home)
    add_result = run_sv(["add", "alpha"], cwd=vault, home=home, git_runner=default_runner)
    assert add_result.exit_code == 0

    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)

    result = run_sv(["update"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    _assert_clean_output(result)
    assert "Updating vault skills..." in result.stdout
    assert "Updated vault skill 'alpha'." in result.stdout
    assert (vault / "skills" / "alpha" / "notes.md").read_text() == "alpha v2\n"

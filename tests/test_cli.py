from argparse import Namespace
from pathlib import Path
import os
import subprocess

import pytest

import sv.cli as cli_module
from sv.catalog import SourceSkill
from sv.cli import _print_add_result, _print_sync_result, _print_wrapped, handle
from sv.errors import SvError
from sv.project import AddSkillResult, SyncResult, SyncSkip
from tests.helpers import (
    assert_no_raw_control_characters,
    assert_no_traceback,
    display_width,
    parse_sv,
)


def parse(argv):
    return parse_sv(argv)


def _existing_repo_git_runner(args, cwd=None):
    if list(args) == ["git", "rev-parse", "--is-inside-work-tree"]:
        return subprocess.CompletedProcess(list(args), 0, "true\n", "")
    raise AssertionError(f"unexpected git call: {args}")


def _write_skill(skill_dir: Path, name: str, description: str = "Alpha skill.") -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def test_index_command_uses_cli_and_configured_scan_paths(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    (project / ".sv").mkdir()
    (project / ".sv" / "index-config.toml").write_text(
        'schema_version = 1\ninclude_paths = ["skills", "team/skills"]\n',
        encoding="utf-8",
    )
    _write_skill(project / "skills" / "alpha", "alpha", "Alpha skill.")
    _write_skill(project / "team" / "skills" / "beta", "beta", "Beta skill.")
    _write_skill(project / "team" / "skills" / "draft", "draft", "Draft skill.")
    _write_skill(project / "other" / "gamma", "gamma", "Gamma skill.")

    result = run_sv(
        parse(["index", "--exclude", "team/skills/draft"]),
        cwd=project,
        home=home,
    )

    assert result.exit_code == 0
    index_text = (project / ".sv" / "index.toml").read_text(encoding="utf-8")
    assert 'source_path = "skills/alpha"' in index_text
    assert 'source_path = "team/skills/beta"' in index_text
    assert "team/skills/draft" not in index_text
    assert "other/gamma" not in index_text


def test_init_command_scaffolds_current_directory_as_empty_skill_vault(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "vault"
    project.mkdir()
    git_calls = []

    def git_runner(args, cwd=None):
        git_calls.append((list(args), cwd))
        if list(args) == ["git", "rev-parse", "--is-inside-work-tree"]:
            return subprocess.CompletedProcess(list(args), 1, "false\n", "")
        if list(args) == ["git", "init"]:
            assert cwd is not None
            (cwd / ".git").mkdir()
            return subprocess.CompletedProcess(list(args), 0, "", "")
        raise AssertionError(f"unexpected git call: {args}")

    result = run_sv(parse(["init"]), cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 0
    assert git_calls == [(["git", "init"], project)]
    assert (project / ".git").is_dir()
    assert (project / "skills").is_dir()
    assert list((project / "skills").iterdir()) == []
    index_text = (project / ".sv" / "index.toml").read_text(encoding="utf-8")
    assert 'kind = "skill-vault"' in index_text
    assert "[[skills]]" not in index_text
    assert (project / ".sv" / "manifest.toml").read_text(encoding="utf-8") == (
        "schema_version = 1\n"
    )
    assert (project / "README.md").read_text(encoding="utf-8") == (
        "<!-- sv:skills:start -->\n"
        "| Skill | Description |\n"
        "| --- | --- |\n"
        "<!-- sv:skills:end -->\n"
    )
    assert "Initialized skill-vault repo" in result.stdout


def test_init_command_scaffolds_named_folder(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "team-skills"
    git_calls = []

    def git_runner(args, cwd=None):
        git_calls.append((list(args), cwd))
        if list(args) == ["git", "rev-parse", "--is-inside-work-tree"]:
            return subprocess.CompletedProcess(list(args), 1, "false\n", "")
        if list(args) == ["git", "init"]:
            assert cwd is not None
            (cwd / ".git").mkdir()
            return subprocess.CompletedProcess(list(args), 0, "", "")
        raise AssertionError(f"unexpected git call: {args}")

    result = run_sv(
        parse(["init", "team-skills"]), cwd=workspace, home=home, git_runner=git_runner
    )

    assert result.exit_code == 0
    assert target.is_dir()
    assert (target / ".git").is_dir()
    assert (target / "skills").is_dir()
    assert (target / ".sv" / "index.toml").is_file()
    assert 'kind = "skill-vault"' in (target / ".sv" / "index.toml").read_text(
        encoding="utf-8"
    )
    assert git_calls == [(["git", "init"], target)]


def test_init_command_does_not_reinitialize_existing_git_repo(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "existing"
    (project / ".git").mkdir(parents=True)
    git_calls = []

    def git_runner(args, cwd=None):
        git_calls.append((list(args), cwd))
        return _existing_repo_git_runner(args, cwd)

    result = run_sv(parse(["init"]), cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 0
    assert git_calls == [(["git", "rev-parse", "--is-inside-work-tree"], project)]
    assert (project / ".git").is_dir()
    assert (project / "skills").is_dir()
    assert (project / ".sv" / "index.toml").is_file()


def test_init_command_initializes_nested_folder_inside_existing_worktree(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    target = project / "nested"
    target.mkdir(parents=True)
    git_calls = []

    def git_runner(args, cwd=None):
        git_calls.append((list(args), cwd))
        if list(args) == ["git", "init"]:
            assert cwd is not None
            (cwd / ".git").mkdir()
            return subprocess.CompletedProcess(list(args), 0, "", "")
        raise AssertionError(f"unexpected git call: {args}")

    result = run_sv(parse(["init", "nested"]), cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 0
    assert git_calls == [(["git", "init"], target)]
    assert (target / ".git").exists()
    assert (target / "skills").is_dir()


def test_init_command_does_not_overwrite_existing_manifest(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    manifest = project / ".sv" / "manifest.toml"
    manifest.parent.mkdir()
    original_manifest = (
        "schema_version = 1\n"
        "\n"
        "[[skills]]\n"
        'name = "alpha"\n'
        'source_repo_id = "Org/Skills"\n'
        'source_repo_url = "https://github.com/Org/Skills.git"\n'
        'source_path = "skills/alpha"\n'
        'description = "Alpha skill."\n'
    )
    manifest.write_text(original_manifest, encoding="utf-8")

    result = run_sv(
        parse(["init"]),
        cwd=project,
        home=home,
        git_runner=_existing_repo_git_runner,
    )

    assert result.exit_code == 0
    assert manifest.read_text(encoding="utf-8") == original_manifest


def test_init_command_rejects_manifest_directory(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    (project / ".sv" / "manifest.toml").mkdir(parents=True)

    result = run_sv(
        parse(["init"]),
        cwd=project,
        home=home,
        git_runner=_existing_repo_git_runner,
    )

    assert result.exit_code == 1
    assert "Sv manifest path" in result.stderr
    assert "exists but is not a file" in result.stderr


def test_init_command_does_not_overwrite_existing_skill_vault_index(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    index_file = project / ".sv" / "index.toml"
    index_file.parent.mkdir()
    original_index = (
        "schema_version = 1\n"
        'kind = "skill-vault"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "skills = []\n"
    )
    index_file.write_text(original_index, encoding="utf-8")

    result = run_sv(
        parse(["init"]),
        cwd=project,
        home=home,
        git_runner=_existing_repo_git_runner,
    )

    assert result.exit_code == 0
    assert index_file.read_text(encoding="utf-8") == original_index


def test_init_command_rejects_existing_non_vault_index(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    index_file = project / ".sv" / "index.toml"
    index_file.parent.mkdir()
    index_file.write_text(
        "schema_version = 1\n"
        'kind = "project-index"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "skills = []\n",
        encoding="utf-8",
    )

    result = run_sv(
        parse(["init"]),
        cwd=project,
        home=home,
        git_runner=_existing_repo_git_runner,
    )

    assert result.exit_code == 1
    assert "is not a skill-vault index" in result.stderr


def test_init_command_reports_target_file_without_git_call(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "vault").write_text("not a directory\n", encoding="utf-8")

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    result = run_sv(parse(["init", "vault"]), cwd=workspace, home=home, git_runner=git_runner)

    assert result.exit_code == 1
    assert "Failed to create target folder" in result.stderr


def test_init_command_reports_existing_skills_file(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    (project / "skills").write_text("not a directory\n", encoding="utf-8")

    result = run_sv(
        parse(["init"]),
        cwd=project,
        home=home,
        git_runner=_existing_repo_git_runner,
    )

    assert result.exit_code == 1
    assert "Failed to create skills directory" in result.stderr


def test_init_command_rejects_invalid_existing_git_metadata(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)

    def git_runner(args, cwd=None):
        if list(args) == ["git", "rev-parse", "--is-inside-work-tree"]:
            return subprocess.CompletedProcess(list(args), 1, "", "invalid git")
        raise AssertionError(f"unexpected git call: {args}")

    result = run_sv(parse(["init"]), cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 1
    assert "Existing Git metadata could not be validated" in result.stderr


def test_init_command_reports_git_init_failure_with_escaped_output(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def git_runner(args, cwd=None):
        return subprocess.CompletedProcess(list(args), 1, "", "denied\x1b[2J")

    result = run_sv(parse(["init"]), cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 1
    assert "Failed to initialize Git repo" in result.stderr
    assert "denied\\x1b[2J" in result.stderr
    assert "\x1b" not in result.stderr


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_init_command_rejects_symlinked_target(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    real_target = tmp_path / "real-target"
    workspace.mkdir()
    real_target.mkdir()
    (workspace / "vault").symlink_to(real_target, target_is_directory=True)

    result = run_sv(parse(["init", "vault"]), cwd=workspace, home=home)

    assert result.exit_code == 1
    assert "Refusing to initialize symlinked target folder" in result.stderr


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_init_command_rejects_symlinked_git_metadata(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    outside_git = tmp_path / "outside-git"
    project.mkdir()
    outside_git.mkdir()
    (project / ".git").symlink_to(outside_git, target_is_directory=True)

    result = run_sv(parse(["init"]), cwd=project, home=home)

    assert result.exit_code == 1
    assert "Refusing to use symlinked Git metadata path" in result.stderr


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_init_command_rejects_symlinked_skills_directory(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    outside_skills = tmp_path / "outside-skills"
    (project / ".git").mkdir(parents=True)
    outside_skills.mkdir()
    (project / "skills").symlink_to(outside_skills, target_is_directory=True)

    result = run_sv(
        parse(["init"]),
        cwd=project,
        home=home,
        git_runner=_existing_repo_git_runner,
    )

    assert result.exit_code == 1
    assert "Refusing to use symlinked skills directory" in result.stderr


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_init_command_rejects_symlinked_sv_directory(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    outside_sv = tmp_path / "outside-sv"
    (project / ".git").mkdir(parents=True)
    outside_sv.mkdir()
    (outside_sv / "index.toml").write_text(
        "schema_version = 1\n"
        'kind = "skill-vault"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "skills = []\n",
        encoding="utf-8",
    )
    (outside_sv / "manifest.toml").write_text("schema_version = 1\n", encoding="utf-8")
    (project / ".sv").symlink_to(outside_sv, target_is_directory=True)

    result = run_sv(
        parse(["init"]),
        cwd=project,
        home=home,
        git_runner=_existing_repo_git_runner,
    )

    assert result.exit_code == 1
    assert "Refusing to use symlinked sv metadata directory" in result.stderr


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_init_command_rejects_symlinked_index(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    outside_index = tmp_path / "outside-index.toml"
    (project / ".git").mkdir(parents=True)
    (project / ".sv").mkdir()
    outside_index.write_text("", encoding="utf-8")
    (project / ".sv" / "index.toml").symlink_to(outside_index)

    result = run_sv(
        parse(["init"]),
        cwd=project,
        home=home,
        git_runner=_existing_repo_git_runner,
    )

    assert result.exit_code == 1
    assert "Refusing to use symlinked sv index" in result.stderr


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support is required")
def test_init_command_rejects_symlinked_manifest(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    outside_manifest = tmp_path / "outside-manifest.toml"
    (project / ".git").mkdir(parents=True)
    (project / ".sv").mkdir()
    outside_manifest.write_text("", encoding="utf-8")
    (project / ".sv" / "manifest.toml").symlink_to(outside_manifest)

    result = run_sv(
        parse(["init"]),
        cwd=project,
        home=home,
        git_runner=_existing_repo_git_runner,
    )

    assert result.exit_code == 1
    assert "Refusing to use symlinked sv manifest" in result.stderr


def test_init_command_preserves_existing_readme_content(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    readme = project / "README.md"
    readme.write_text("# Team Skills\n\nHuman notes.\n", encoding="utf-8")

    result = run_sv(
        parse(["init"]),
        cwd=project,
        home=home,
        git_runner=_existing_repo_git_runner,
    )

    assert result.exit_code == 0
    assert readme.read_text(encoding="utf-8") == (
        "# Team Skills\n"
        "\n"
        "Human notes.\n"
        "\n"
        "<!-- sv:skills:start -->\n"
        "| Skill | Description |\n"
        "| --- | --- |\n"
        "<!-- sv:skills:end -->\n"
    )


def test_init_command_appends_readme_block_after_double_newline(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    readme = project / "README.md"
    readme.write_text("# Team Skills\n\n", encoding="utf-8")

    result = run_sv(
        parse(["init"]),
        cwd=project,
        home=home,
        git_runner=_existing_repo_git_runner,
    )

    assert result.exit_code == 0
    assert readme.read_text(encoding="utf-8").startswith(
        "# Team Skills\n\n<!-- sv:skills:start -->\n"
    )


def test_init_command_reports_malformed_readme_markers(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    (project / "README.md").write_text(
        "# Team\n<!-- sv:skills:start -->\nstale\n", encoding="utf-8"
    )

    result = run_sv(
        parse(["init"]),
        cwd=project,
        home=home,
        git_runner=_existing_repo_git_runner,
    )

    assert result.exit_code == 1
    assert "README must contain exactly one sv skill table start marker" in result.stderr
    assert not (project / ".sv" / "index.toml").exists()
    assert not (project / ".sv" / "manifest.toml").exists()


def test_init_command_reports_reversed_readme_markers(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    (project / "README.md").write_text(
        "# Team\n"
        "<!-- sv:skills:end -->\n"
        "stale\n"
        "<!-- sv:skills:start -->\n",
        encoding="utf-8",
    )

    result = run_sv(
        parse(["init"]),
        cwd=project,
        home=home,
        git_runner=_existing_repo_git_runner,
    )

    assert result.exit_code == 1
    assert "README sv skill table end marker appears before start marker" in result.stderr
    assert not (project / ".sv" / "index.toml").exists()
    assert not (project / ".sv" / "manifest.toml").exists()


def test_index_command_rejects_existing_symlinked_index_before_reading_kind(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".sv").mkdir(parents=True)
    outside_index = tmp_path / "outside-index.toml"
    outside_index.write_text(
        "schema_version = 1\n"
        'kind = "skill-vault"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n',
        encoding="utf-8",
    )
    (project / ".sv" / "index.toml").symlink_to(outside_index)
    readme = project / "README.md"
    readme.write_text("# Project\n", encoding="utf-8")

    result = run_sv(parse(["index"]), cwd=project, home=home)

    assert result.exit_code == 1
    assert "Refusing to use symlinked sv index" in result.stderr
    assert outside_index.read_text(encoding="utf-8").startswith("schema_version = 1\n")
    assert readme.read_text(encoding="utf-8") == "# Project\n"


def test_index_command_writes_project_index_and_warns_for_invalid_skills(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    valid = project / ".pi" / "skills" / "alpha"
    valid.mkdir(parents=True)
    (valid / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill.\n---\n",
        encoding="utf-8",
    )
    invalid = project / "skills" / "broken"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text(
        "---\nname: broken\n---\n",
        encoding="utf-8",
    )

    result = run_sv(parse(["index"]), cwd=project, home=home)

    assert result.exit_code == 0
    index_file = project / ".sv" / "index.toml"
    assert index_file.is_file()
    text = index_file.read_text(encoding="utf-8")
    assert 'kind = "project-index"' in text
    assert 'name = "alpha"' in text
    assert 'source_path = ".pi/skills/alpha"' in text
    assert 'content_hash = "sha256:' in text
    assert "broken" not in text
    assert "warning:" in result.stderr
    assert "broken" in result.stderr
    assert "Wrote sv index with 1 skill" in result.stdout


def test_index_command_escapes_output_path(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project\x1b[31m"
    skill = project / "skills" / "alpha"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill.\n---\n",
        encoding="utf-8",
    )

    result = run_sv(parse(["index"]), cwd=project, home=home)

    assert result.exit_code == 0
    assert "\x1b" not in result.stdout
    assert "\\x1b[31m" in result.stdout


def test_index_command_updates_readme_only_for_skill_vaults(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    skill = project / "skills" / "alpha"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill.\n---\n",
        encoding="utf-8",
    )
    (project / ".sv").mkdir(parents=True)
    (project / ".sv" / "index.toml").write_text(
        "schema_version = 1\n"
        'kind = "skill-vault"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "skills = []\n",
        encoding="utf-8",
    )
    readme = project / "README.md"
    readme.write_text(
        "# Vault\n"
        "\n"
        "Before.\n"
        "<!-- sv:skills:start -->\n"
        "stale\n"
        "<!-- sv:skills:end -->\n"
        "After.\n",
        encoding="utf-8",
    )

    result = run_sv(parse(["index"]), cwd=project, home=home)

    assert result.exit_code == 0
    assert readme.read_text(encoding="utf-8") == (
        "# Vault\n"
        "\n"
        "Before.\n"
        "<!-- sv:skills:start -->\n"
        "| Skill | Description |\n"
        "| --- | --- |\n"
        "| alpha | Alpha skill. |\n"
        "<!-- sv:skills:end -->\n"
        "After.\n"
    )

    normal_project = tmp_path / "normal-project"
    normal_skill = normal_project / "skills" / "beta"
    normal_skill.mkdir(parents=True)
    (normal_skill / "SKILL.md").write_text(
        "---\nname: beta\ndescription: Beta skill.\n---\n",
        encoding="utf-8",
    )
    normal_readme = normal_project / "README.md"
    normal_readme.write_text(
        "# Normal\n"
        "<!-- sv:skills:start -->\n"
        "user-controlled text\n"
        "<!-- sv:skills:end -->\n",
        encoding="utf-8",
    )

    result = run_sv(parse(["index"]), cwd=normal_project, home=home)

    assert result.exit_code == 0
    assert normal_readme.read_text(encoding="utf-8") == (
        "# Normal\n"
        "<!-- sv:skills:start -->\n"
        "user-controlled text\n"
        "<!-- sv:skills:end -->\n"
    )


def test_print_add_result_escapes_existing_manifest_repo_id(
    tmp_path: Path, capsys
):
    _print_add_result(
        AddSkillResult(
            skill="alpha",
            target=tmp_path / "project" / ".pi" / "skills" / "alpha",
            status="exists",
            repo_id="Org/Skills",
            existing_repo_id="Bad\x1b[2JRepo",
        )
    )

    output = capsys.readouterr().out
    assert_no_raw_control_characters(output)
    assert "Bad\\x1b[2JRepo" in output


def test_print_sync_result_escapes_manifest_repo_ids(capsys):
    _print_sync_result(
        SyncResult(
            updated=[],
            skipped=[
                SyncSkip(
                    skill="alpha",
                    reason="source-missing",
                    repo_ids=("Bad\x1b[2JRepo",),
                )
            ],
            backfilled=[],
        )
    )

    output = capsys.readouterr().out
    assert_no_raw_control_characters(output)
    assert "Bad\\x1b[2JRepo" in output


def test_run_builds_isolated_pi_command_and_forwards_args(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    calls = []

    def process_runner(command):
        calls.append(command)
        return 23

    result = run_sv(
        parse(["run", "--", "--model", "fast"]),
        cwd=project,
        home=home,
        process_runner=process_runner,
    )

    assert result.exit_code == 23
    assert calls == [["pi", "--no-skills", "--skill", ".pi/skills", "--model", "fast"]]


def test_run_reports_missing_pi_binary(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def process_runner(command):
        raise FileNotFoundError(command[0])

    result = run_sv(
        parse(["run"]),
        cwd=project,
        home=home,
        process_runner=process_runner,
    )

    assert result.exit_code == 1
    assert "Unable to run 'pi'" in result.stderr


def test_run_reports_process_launch_os_errors_without_traceback(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def process_runner(command):
        raise PermissionError("denied")

    exit_code = handle(
        parse(["run"]), cwd=project, home=home, process_runner=process_runner
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "error: Unable to run 'pi': denied" in captured.err
    assert_no_traceback(captured.err)


def test_run_escapes_control_characters_in_launch_errors(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def process_runner(command):
        raise PermissionError("denied\x1b[2J")

    exit_code = handle(
        parse(["run"]), cwd=project, home=home, process_runner=process_runner
    )

    assert exit_code == 1
    message = capsys.readouterr().err
    assert "denied\\x1b[2J" in message
    assert_no_raw_control_characters(message)


def test_run_rejects_symlinked_project_skills_path(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    outside_skills = tmp_path / "outside-skills"
    outside_skills.mkdir()
    (project / ".pi").mkdir(parents=True)
    (project / ".pi" / "skills").symlink_to(
        outside_skills, target_is_directory=True
    )
    calls = []

    def process_runner(command):
        calls.append(command)
        return 0

    exit_code = handle(
        parse(["run"]), cwd=project, home=home, process_runner=process_runner
    )

    assert exit_code == 1
    assert calls == []
    assert "Refusing to use symlinked Pi skills path" in capsys.readouterr().err


def test_run_rejects_symlinked_project_skill_directory(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    outside_skill = tmp_path / "outside-alpha"
    outside_skill.mkdir()
    project_skills = project / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    (project_skills / "alpha").symlink_to(outside_skill, target_is_directory=True)
    calls = []

    def process_runner(command):
        calls.append(command)
        return 0

    exit_code = handle(
        parse(["run"]), cwd=project, home=home, process_runner=process_runner
    )

    assert exit_code == 1
    assert calls == []
    assert "Refusing to manage symlinked Pi skill 'alpha'" in capsys.readouterr().err


def test_add_interactive_with_skill_does_not_touch_source_repo(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["add", "alpha", "-l"]), cwd=project, home=home, git_runner=git_runner
    )

    assert exit_code == 1
    assert "Use -l by itself" in capsys.readouterr().err


def test_add_invalid_skill_name_does_not_touch_source_repo(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["add", "../alpha"]), cwd=project, home=home, git_runner=git_runner
    )

    assert exit_code == 1
    assert "Invalid skill name" in capsys.readouterr().err


def test_add_qualified_repo_id_with_control_characters_does_not_touch_source_repo(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["add", "bad\x1b[2J:alpha"]),
        cwd=project,
        home=home,
        git_runner=git_runner,
    )

    assert exit_code == 1
    message = capsys.readouterr().err
    assert "bad\\x1b[2J" in message
    assert "\x1b" not in message


def test_malformed_config_reports_cli_error(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    config = home / ".sv" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("repos = [\n")

    exit_code = handle(parse(["list"]), cwd=project, home=home)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "error: Failed to read sv config" in captured.err
    assert "Traceback" not in captured.err


def test_remove_interactive_removes_selected_project_skills_without_source_repo(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    (project_skills / "alpha").mkdir(parents=True)
    (project_skills / "alpha" / "notes.md").write_text("alpha\n")
    (project_skills / "beta").mkdir()
    (project_skills / "beta" / "notes.md").write_text("beta\n")
    selector_calls = []

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    def skill_selector(skills):
        selector_calls.append(skills)
        return ["beta"]

    exit_code = handle(
        parse(["remove", "-l"]),
        cwd=project,
        home=home,
        git_runner=git_runner,
        skill_selector=skill_selector,
    )

    assert exit_code == 0
    assert selector_calls == []
    assert (project_skills / "alpha" / "notes.md").read_text() == "alpha\n"
    assert (project_skills / "beta" / "notes.md").read_text() == "beta\n"
    assert "No Pi skills found to remove." in capsys.readouterr().out


def test_remove_interactive_reports_no_project_skills(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(parse(["remove", "-l"]), cwd=project, home=home)

    assert exit_code == 0
    assert "No Pi skills found to remove." in capsys.readouterr().out


def test_remove_skill_removes_project_skill_without_source_repo(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    skill = project / ".pi" / "skills" / "alpha"
    skill.mkdir(parents=True)

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["remove", "alpha"]), cwd=project, home=home, git_runner=git_runner
    )

    assert exit_code == 0
    assert not skill.exists()
    assert "Removed Pi skill 'alpha'" in capsys.readouterr().out


def test_remove_interactive_with_skill_does_not_touch_source_repo(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(
        parse(["remove", "alpha", "-l"]),
        cwd=project,
        home=home,
        git_runner=git_runner,
    )

    assert exit_code == 1
    assert "Use -l by itself" in capsys.readouterr().err


def test_repo_list_table_fits_terminal_width(tmp_path: Path, capsys, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "very-long-local-skill-source-name"
    source.mkdir()

    assert handle(parse(["repo", "add", str(source)]), cwd=project, home=home) == 0
    capsys.readouterr()
    monkeypatch.setenv("COLUMNS", "40")

    exit_code = handle(parse(["repo", "list"]), cwd=project, home=home)

    assert exit_code == 0
    assert all(len(line) <= 40 for line in capsys.readouterr().out.splitlines())


def test_repo_add_and_list_use_multi_repo_config(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    assert (
        handle(parse(["repo", "add", "HamdiMaz/Skills"]), cwd=project, home=home) == 0
    )
    assert (
        handle(parse(["repo", "add", "SomeOrg/TeamSkills"]), cwd=project, home=home)
        == 0
    )
    capsys.readouterr()

    exit_code = handle(parse(["repo", "list"]), cwd=project, home=home)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Repo" in output
    assert "URL" in output
    assert "Cache" in output
    assert "HamdiMaz/Skills" in output
    assert "https://github.com/HamdiMaz/Skills.git" in output
    assert "SomeOrg/TeamSkills" in output


def test_repo_add_reports_existing_repo(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    assert (
        handle(parse(["repo", "add", "HamdiMaz/Skills"]), cwd=project, home=home) == 0
    )
    capsys.readouterr()
    exit_code = handle(
        parse(["repo", "add", "HamdiMaz/Skills"]), cwd=project, home=home
    )

    assert exit_code == 0
    assert "already configured" in capsys.readouterr().out


def test_repo_add_reports_unresolvable_home_without_traceback(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(
        parse(["repo", "add", "~definitely-not-an-sv-user/Skills"]),
        cwd=project,
        home=home,
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "error: Could not resolve home directory" in captured.err
    assert "Traceback" not in captured.err


def test_repo_remove_escapes_control_characters_in_missing_repo(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(
        parse(["repo", "remove", "missing\x1b[2J"]), cwd=project, home=home
    )

    assert exit_code == 1
    message = capsys.readouterr().err
    assert "missing\\x1b[2J" in message
    assert "\x1b" not in message


def test_repo_remove_updates_config_without_deleting_project_skills(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    skill = project / ".pi" / "skills" / "alpha"
    skill.mkdir(parents=True)

    assert (
        handle(parse(["repo", "add", "SomeOrg/TeamSkills"]), cwd=project, home=home)
        == 0
    )
    capsys.readouterr()

    exit_code = handle(
        parse(["repo", "remove", "SomeOrg/TeamSkills"]), cwd=project, home=home
    )

    assert exit_code == 0
    assert skill.is_dir()
    assert "Removed repo SomeOrg/TeamSkills" in capsys.readouterr().out


def test_add_help_explains_skill_argument(capsys):
    with pytest.raises(SystemExit) as exc_info:
        parse(["add", "--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "Skill name or repo:skill reference" in help_text
    assert "choose a source" in help_text


def test_print_wrapped_omits_indent_when_terminal_is_too_narrow(
    capsys, monkeypatch
):
    monkeypatch.setenv("COLUMNS", "4")

    _print_wrapped("one two three")

    assert all(len(line) <= 4 for line in capsys.readouterr().out.splitlines())


def test_print_wrapped_honors_display_width_for_wide_unicode(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "20")

    _print_wrapped("Duplicate 日本語日本語日本語 skill names")

    assert all(display_width(line) <= 20 for line in capsys.readouterr().out.splitlines())


def test_print_wrapped_replaces_overwide_character_at_one_column(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "1")

    _print_wrapped("🤖")

    assert capsys.readouterr().out.splitlines() == ["?"]


def test_print_wrapped_keeps_combining_mark_with_base_character(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "1")

    _print_wrapped("a\u0301b")

    assert capsys.readouterr().out.splitlines() == ["a\u0301", "b"]


def test_config_command_is_removed_from_parser():
    with pytest.raises(SystemExit):
        parse(["config", "show"])


def test_default_process_runner_delegates_to_subprocess_call(monkeypatch):
    calls = []

    def fake_call(command):
        calls.append(command)
        return 17

    monkeypatch.setattr(cli_module.subprocess, "call", fake_call)

    assert cli_module.default_process_runner(("pi", "--help")) == 17
    assert calls == [["pi", "--help"]]


def test_main_parses_arguments_and_uses_current_project_paths(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    home = tmp_path / "home"
    parsed_calls = []

    monkeypatch.setattr(cli_module.Path, "cwd", lambda: project)
    monkeypatch.setattr(cli_module.Path, "home", lambda: home)

    def fake_handle(args, cwd, home):
        parsed_calls.append((args.command, args.repo_command, cwd, home))
        return 0

    monkeypatch.setattr(cli_module, "handle", fake_handle)

    assert cli_module.main(["repo", "list"]) == 0
    assert parsed_calls == [("repo", "list", project, home)]


def test_svx_main_dispatches_to_repo_add_flow(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    home = tmp_path / "home"
    parsed_calls = []

    monkeypatch.setattr(cli_module.Path, "cwd", lambda: project)
    monkeypatch.setattr(cli_module.Path, "home", lambda: home)

    def fake_handle(args, cwd, home):
        parsed_calls.append(
            (args.command, args.repo_command, args.repo, args.skills_paths, cwd, home)
        )
        return 0

    monkeypatch.setattr(cli_module, "handle", fake_handle)

    assert cli_module.svx_main(["owner/repo", "--skills-path", "custom/skills"]) == 0
    assert parsed_calls == [
        ("repo", "add", "owner/repo", ["custom/skills"], project, home)
    ]


def test_remove_without_skill_reports_actionable_error(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    exit_code = handle(parse(["remove"]), cwd=project, home=home)

    assert exit_code == 1
    assert "Specify a skill name or use -l/--all." in capsys.readouterr().err


def test_unknown_commands_are_reported_without_tracebacks(tmp_path: Path, capsys):
    exit_code = handle(Namespace(command="mystery"), cwd=tmp_path, home=tmp_path)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Unknown command: mystery" in captured.err
    assert_no_traceback(captured.err)


def test_unknown_repo_subcommand_is_reported_without_tracebacks(
    tmp_path: Path, capsys
):
    exit_code = handle(
        Namespace(command="repo", repo_command="mystery"), cwd=tmp_path, home=tmp_path
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Unknown repo command: mystery" in captured.err
    assert_no_traceback(captured.err)


def test_empty_repo_qualified_add_reference_fails_before_git(tmp_path: Path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    exit_code = handle(parse(["add", ":alpha"]), cwd=project, home=home, git_runner=git_runner)

    assert exit_code == 1
    assert "Invalid skill reference ':alpha'. Use repo:skill." in capsys.readouterr().err


def test_missing_qualified_skill_reference_reports_exact_reference(tmp_path: Path):
    with pytest.raises(SvError, match="Org/Skills:missing"):
        cli_module._handle_add(
            "Org/Skills:missing",
            [],
            cwd=tmp_path,
            adapter=cli_module.PiAdapter(),
            skill_chooser=lambda matches: None,
        )


def test_duplicate_add_choice_can_be_cancelled(tmp_path: Path, capsys):
    first = _source_skill(tmp_path, "source-a", repo_id="Org/A")
    second = _source_skill(tmp_path, "source-b", repo_id="Org/B")

    exit_code = cli_module._handle_add(
        "alpha",
        [first, second],
        cwd=tmp_path / "project",
        adapter=cli_module.PiAdapter(),
        skill_chooser=lambda matches: None,
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Multiple source skills match 'alpha'" in output
    assert "No skill selected." in output
    assert "Org/A:alpha" in output


def test_add_all_and_interactive_report_empty_catalog(tmp_path: Path, capsys):
    project = tmp_path / "project"

    assert cli_module._handle_add_all([], cwd=project, adapter=cli_module.PiAdapter()) == 0
    assert (
        cli_module._handle_add_interactive(
            [],
            cwd=project,
            adapter=cli_module.PiAdapter(),
            skill_selector=lambda skills, **kwargs: [],
        )
        == 0
    )

    output = capsys.readouterr().out
    assert output.count("No valid skills found in configured source repos.") == 2


def test_add_interactive_picker_uses_aligned_skill_source_description_labels(
    tmp_path: Path,
):
    short = _source_skill(tmp_path, "source-a", repo_id="Org/A")
    long_source = tmp_path / "source-b"
    long_skill_dir = long_source / "skills" / "longer-name"
    long_skill_dir.mkdir(parents=True)
    long = SourceSkill(
        name="longer-name",
        description="Longer skill.",
        repo_id="Org/Longer",
        repo_url="https://github.com/Org/Longer.git",
        repo_path=long_source,
        source_path=long_skill_dir,
    )
    calls = []

    def capture_selector(skills, **kwargs):
        label = kwargs["item_label"]
        calls.append(
            (
                skills,
                kwargs["header_label"],
                [label(skill) for skill in skills],
            )
        )
        return []

    exit_code = cli_module._handle_add_interactive(
        [short, long],
        cwd=tmp_path / "project",
        adapter=cli_module.PiAdapter(),
        skill_selector=capture_selector,
    )

    assert exit_code == 0
    assert calls
    skills, header, labels = calls[0]
    assert skills == [short, long]
    assert header == "Skill        Source      Description"
    assert labels == [
        "alpha        Org/A       Alpha skill.",
        "longer-name  Org/Longer  Longer skill.",
    ]
    assert labels[0].index("Org/A") == labels[1].index("Org/Longer")
    assert labels[0].index("Alpha skill.") == labels[1].index("Longer skill.")


def test_print_add_result_existing_without_recorded_origin_mentions_requested_repo(
    tmp_path: Path, capsys
):
    _print_add_result(
        AddSkillResult(
            skill="alpha",
            target=tmp_path / "project" / ".pi" / "skills" / "alpha",
            status="exists",
            repo_id="Org/Skills",
            existing_repo_id=None,
        )
    )

    output = capsys.readouterr().out
    assert "no sv origin recorded" in output
    assert "requested Org/Skills" in output


def test_remove_interactive_cancel_leaves_project_skills_unchanged(
    tmp_path: Path, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    skill = project / ".pi" / "skills" / "alpha"
    skill.mkdir(parents=True)
    (skill / "notes.md").write_text("alpha\n")

    exit_code = handle(
        parse(["remove", "-l"]),
        cwd=project,
        home=home,
        skill_selector=lambda skills: [],
    )

    assert exit_code == 0
    assert (skill / "notes.md").read_text() == "alpha\n"
    assert "No Pi skills found to remove." in capsys.readouterr().out


def test_choose_skill_returns_none_without_tty(monkeypatch, tmp_path: Path):
    class NonTty:
        def isatty(self):
            return False

    monkeypatch.setattr(cli_module.sys, "stdin", NonTty())
    monkeypatch.setattr(cli_module.sys, "stdout", NonTty())

    assert cli_module._choose_skill([_source_skill(tmp_path, "source")]) is None


def test_choose_skill_reprompts_until_single_checkbox_selection(
    monkeypatch, tmp_path: Path, capsys
):
    skill = _source_skill(tmp_path, "source")
    selections = iter([[skill, skill], [skill]])

    monkeypatch.setattr(cli_module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli_module.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        cli_module,
        "select_skills",
        lambda matches, **kwargs: next(selections),
    )

    assert cli_module._choose_skill([skill]) == skill
    assert "Select exactly one source skill, or q to cancel." in capsys.readouterr().out


def test_choose_skill_accepts_empty_checkbox_selection(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(cli_module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli_module.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        cli_module,
        "select_skills",
        lambda matches, **kwargs: [],
    )

    assert cli_module._choose_skill([_source_skill(tmp_path, "source")]) is None


def _source_skill(tmp_path: Path, repo_folder: str, *, repo_id: str = "Org/Skills") -> SourceSkill:
    source = tmp_path / repo_folder
    skill_dir = source / "skills" / "alpha"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("---\nname: alpha\ndescription: Alpha skill.\n---\n")
    return SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo_id,
        repo_url=f"https://github.com/{repo_id}.git",
        repo_path=source,
        source_path=skill_dir,
    )


def test_cli_rejects_invalid_add_argument_combinations(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    cases = [
        (["add", "-l", "alpha"], "Use -l by itself"),
        (["add", "-l", "--all"], "Use -l by itself"),
        (["add", "--repo", "source"], "Use --repo only with --all"),
        (["add", "alpha", "--all"], "Use either a skill name or --all"),
        (["add"], "Specify a skill name"),
    ]
    for argv, message in cases:
        result = run_sv(parse(argv), cwd=project, home=home)
        assert result.exit_code == 1
        assert message in result.stderr


def test_cli_rejects_invalid_remove_argument_combinations(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    cases = [
        (["remove", "-l", "alpha"], "Use -l by itself"),
        (["remove", "-l", "--all"], "Use -l by itself"),
        (["remove", "alpha", "--all"], "Use either a skill name or --all"),
        (["remove", "alpha", "--yes"], "Use --yes only with -l or --all"),
        (["remove"], "Specify a skill name"),
    ]
    for argv, message in cases:
        result = run_sv(parse(argv), cwd=project, home=home)
        assert result.exit_code == 1
        assert message in result.stderr


def test_cli_list_and_search_without_source_config_print_guidance(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    list_result = run_sv(parse(["list"]), cwd=project, home=home)
    search_result = run_sv(parse(["search", "alpha"]), cwd=project, home=home)

    assert list_result.exit_code == 1
    assert "No skill source repos are configured" in list_result.stderr
    assert search_result.exit_code == 1
    assert "No skill source repos are configured" in search_result.stderr


def test_cli_global_status_records_source_state_in_every_cwd(tmp_path: Path):
    assert cli_module._should_record_global_source_state(tmp_path / "project", tmp_path / "home")
    assert cli_module._should_record_global_source_state(tmp_path / "home", tmp_path / "home")


def test_cli_prompt_for_initial_sources_handles_cancel_custom_repo_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    from sv.config import RecommendedSource

    paths = cli_module.SvPaths.from_home(tmp_path / "home")
    source = RecommendedSource(repo="Org/Skills", description="Recommended")
    monkeypatch.setattr(cli_module, "recommended_sources", lambda: [source])

    choices = iter(["bad", "m", "bad/repo", "1"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(choices))
    calls: list[str] = []

    def fake_add_repo(_paths, repo):
        calls.append(repo)
        if repo == "bad/repo":
            raise SvError("bad source")
        return cli_module.RepoChangeResult(status="added", repo=cli_module.RepoConfig(id=repo, url=f"https://github.com/{repo}.git"))

    monkeypatch.setattr(cli_module, "add_repo", fake_add_repo)
    monkeypatch.setattr(cli_module, "load_config", lambda _paths: cli_module.SvConfig(repos=(cli_module.RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git"),)))

    config = cli_module._prompt_for_initial_sources(paths)

    assert [repo.id for repo in config.repos] == ["Org/Skills"]
    assert calls == ["bad/repo", "Org/Skills"]
    output = capsys.readouterr().out
    assert "Could not add source repo: bad source" in output

    monkeypatch.setattr(cli_module, "recommended_sources", lambda: [])
    monkeypatch.setattr("builtins.input", lambda _prompt: "q")
    with pytest.raises(SvError, match="No skill source repos"):
        cli_module._prompt_for_initial_sources(paths)

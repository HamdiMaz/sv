from pathlib import Path
import shutil

import pytest

from sv.catalog import SourceSkill
from sv.config import SvPaths
from sv.manifest import (
    GlobalSourceState,
    ManifestEntry,
    load_manifest,
    save_global_manifest,
    save_manifest,
)
from sv.project import add_project_skill
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


def _manifest_entry(skill: str) -> ManifestEntry:
    return ManifestEntry(
        name=skill,
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        source_path=f"skills/{skill}",
        description=f"{skill.title()} skill.",
    )


def _vault_manifest_entry(skill: str) -> ManifestEntry:
    return ManifestEntry(
        name=skill,
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        source_path=f"skills/{skill}",
        description=f"{skill.title()} skill.",
        target_kind="skill-vault",
        target_agent=None,
        target_path=f"skills/{skill}",
    )


def _write_empty_vault_index(vault: Path) -> None:
    (vault / ".git").mkdir(parents=True)
    index_file = vault / ".sv" / "index.toml"
    index_file.parent.mkdir(exist_ok=True)
    index_file.write_text(
        "schema_version = 1\n"
        'kind = "skill-vault"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n'
        "skills = []\n",
        encoding="utf-8",
    )


def _source_skill(source_root: Path, name: str) -> SourceSkill:
    skill_dir = source_root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name.title()} skill.\n---\n",
        encoding="utf-8",
    )
    (skill_dir / "notes.md").write_text(f"{name} remote\n", encoding="utf-8")
    return SourceSkill(
        name=name,
        description=f"{name.title()} skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=source_root,
        source_path=skill_dir,
    )


def test_status_in_normal_project_shows_managed_pi_skill_states_and_excludes_manual(
    tmp_path: Path, run_sv
):
    source = make_source_repo(tmp_path)
    write_source_skill(source, "gamma", "Gamma skill.", "gamma v1\n")
    run_git(["add", "skills/gamma"], source)
    run_git(["commit", "-m", "add gamma"], source)

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    run_git(["init"], project)
    configure_source(source, project, home)

    for skill in ("alpha", "beta", "gamma"):
        add_result = run_sv(["add", skill], cwd=project, home=home, git_runner=default_runner)
        assert add_result.exit_code == 0

    manual = project / ".pi" / "skills" / "manual"
    manual.mkdir()
    (manual / "SKILL.md").write_text(
        "---\nname: manual\ndescription: Manual skill.\n---\n",
        encoding="utf-8",
    )

    (project / ".pi" / "skills" / "alpha" / "notes.md").write_text(
        "alpha local edit\n", encoding="utf-8"
    )
    write_source_skill(source, "beta", "Beta skill.", "beta v2\n")
    run_git(["rm", "-r", "skills/gamma"], source)
    run_git(["add", "skills/beta"], source)
    run_git(["commit", "-m", "update beta and remove gamma"], source)

    nested = project / "src" / "package"
    nested.mkdir(parents=True)
    result = run_sv(["status"], cwd=nested, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "Project sv-managed Pi skills" in result.stdout
    assert "alpha" in result.stdout
    assert ".pi/skills/alpha" in result.stdout
    assert ":skills/alpha" in result.stdout
    assert "modified" in result.stdout
    assert "beta" in result.stdout
    assert "update available" in result.stdout
    assert "gamma" in result.stdout
    assert "orphan" in result.stdout
    assert "manual" not in result.stdout
    manifest = load_manifest(project / ".pi" / "skills")
    assert manifest["alpha"].modified is True
    assert manifest["beta"].update_available is True
    assert manifest["gamma"].orphan is True
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stdout)
    assert_no_raw_control_characters(result.stderr)


def test_status_reports_manifest_managed_pi_skills_with_missing_targets(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    save_manifest(project_skills, {"alpha": _manifest_entry("alpha")})

    result = run_sv(
        ["status"],
        cwd=project,
        home=home,
        git_runner=lambda args, cwd=None: (_ for _ in ()).throw(
            AssertionError(f"unexpected git call: {args}")
        ),
    )

    assert result.exit_code == 0
    assert "Project sv-managed Pi skills" in result.stdout
    assert "alpha" in result.stdout
    assert ".pi/skills/alpha" in result.stdout
    assert "missing" in result.stdout
    assert "No sv-managed Pi skills found" not in result.stdout
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stdout)
    assert_no_raw_control_characters(result.stderr)


def test_status_reports_manifest_managed_pi_skills_with_invalid_targets(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    target = project_skills / "alpha"
    target.parent.mkdir(parents=True)
    target.write_text("not a skill directory\n", encoding="utf-8")
    save_manifest(project_skills, {"alpha": _manifest_entry("alpha")})

    result = run_sv(
        ["status"],
        cwd=project,
        home=home,
        git_runner=lambda args, cwd=None: (_ for _ in ()).throw(
            AssertionError(f"unexpected git call: {args}")
        ),
    )

    assert result.exit_code == 0
    assert "Project sv-managed Pi skills" in result.stdout
    assert "alpha" in result.stdout
    assert "invalid target" in result.stdout
    assert "No sv-managed Pi skills found" not in result.stdout
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stdout)
    assert_no_raw_control_characters(result.stderr)



def test_status_with_only_missing_targets_does_not_refresh_unreachable_sources(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    missing_source = tmp_path / "missing-source"
    paths.config_file.write_text(
        "schema_version = 1\n"
        "[[repos]]\n"
        'id = "Missing/Source"\n'
        f'url = "{missing_source}"\n',
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    save_manifest(project_skills, {"alpha": _manifest_entry("alpha")})

    result = run_sv(["status"], cwd=project, home=home)

    assert result.exit_code == 0
    assert "Project sv-managed Pi skills" in result.stdout
    assert "missing" in result.stdout
    assert result.stderr == ""



def test_status_reports_manifest_managed_vault_skills_with_missing_targets(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    _write_empty_vault_index(vault)
    vault_skills = vault / "skills"
    save_manifest(vault_skills, {"alpha": _vault_manifest_entry("alpha")})

    result = run_sv(
        ["status"],
        cwd=vault,
        home=home,
        git_runner=lambda args, cwd=None: (_ for _ in ()).throw(
            AssertionError(f"unexpected git call: {args}")
        ),
    )

    assert result.exit_code == 0
    assert "Skill-vault sv-managed skills" in result.stdout
    assert "alpha" in result.stdout
    assert "skills/alpha" in result.stdout
    assert "missing" in result.stdout
    assert "No sv-managed vault skills found" not in result.stdout
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stdout)
    assert_no_raw_control_characters(result.stderr)


def test_status_in_skill_vault_shows_vault_states_index_and_readme_freshness(
    tmp_path: Path, run_sv
):
    source = make_source_repo(tmp_path)
    write_source_skill(source, "gamma", "Gamma skill.", "gamma v1\n")
    run_git(["add", "skills/gamma"], source)
    run_git(["commit", "-m", "add gamma"], source)

    home = tmp_path / "home"
    vault = tmp_path / "vault"
    result = run_sv(["init", str(vault)], cwd=tmp_path, home=home, git_runner=default_runner)
    assert result.exit_code == 0
    configure_source(source, vault, home)

    for skill in ("alpha", "beta", "gamma"):
        add_result = run_sv(["add", skill], cwd=vault, home=home, git_runner=default_runner)
        assert add_result.exit_code == 0

    (vault / "skills" / "alpha" / "notes.md").write_text(
        "alpha local edit\n", encoding="utf-8"
    )
    write_source_skill(source, "beta", "Beta skill.", "beta v2\n")
    run_git(["rm", "-r", "skills/gamma"], source)
    run_git(["add", "skills/beta"], source)
    run_git(["commit", "-m", "update beta and remove gamma"], source)

    nested = vault / "docs" / "examples"
    nested.mkdir(parents=True)
    result = run_sv(["status"], cwd=nested, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "Skill-vault sv-managed skills" in result.stdout
    assert "alpha" in result.stdout
    assert "skills/alpha" in result.stdout
    assert ":skills/alpha" in result.stdout
    assert "modified" in result.stdout
    assert "beta" in result.stdout
    assert "update available" in result.stdout
    assert "gamma" in result.stdout
    assert "orphan" in result.stdout
    assert "Index" in result.stdout
    assert "fresh" in result.stdout
    assert "README" in result.stdout
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stdout)
    assert_no_raw_control_characters(result.stderr)


def test_init_folder_inside_git_worktree_creates_detectable_nested_vault(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    parent = tmp_path / "parent"
    parent.mkdir()
    run_git(["init"], parent)

    result = run_sv(["init", "vault"], cwd=parent, home=home, git_runner=default_runner)

    vault = parent / "vault"
    assert result.exit_code == 0
    assert (vault / ".git").exists()
    assert (vault / ".sv" / "index.toml").exists()

    nested = vault / "docs" / "nested"
    nested.mkdir(parents=True)
    status = run_sv(["status"], cwd=nested, home=home, git_runner=default_runner)

    assert status.exit_code == 0
    assert "Skill-vault status" in status.stdout
    assert "Index" in status.stdout
    assert "Global skill source status" not in status.stdout
    assert_no_traceback(status.stdout)
    assert_no_traceback(status.stderr)
    assert_no_raw_control_characters(status.stdout)
    assert_no_raw_control_characters(status.stderr)


def test_status_in_skill_vault_reports_stale_index_and_readme(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    result = run_sv(["init", str(vault)], cwd=tmp_path, home=home, git_runner=default_runner)
    assert result.exit_code == 0

    manual = vault / "skills" / "manual"
    manual.mkdir()
    (manual / "SKILL.md").write_text(
        "---\nname: manual\ndescription: Manual skill.\n---\n",
        encoding="utf-8",
    )

    result = run_sv(["status"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "Index" in result.stdout
    assert "stale" in result.stdout
    assert "README" in result.stdout
    assert "stale" in result.stdout


def test_status_in_skill_vault_freshness_honors_persistent_scan_config(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    result = run_sv(["init", str(vault)], cwd=tmp_path, home=home, git_runner=default_runner)
    assert result.exit_code == 0
    (vault / ".sv" / "index-config.toml").write_text(
        "schema_version = 1\n"
        'exclude_paths = ["skills/draft"]\n',
        encoding="utf-8",
    )
    draft = vault / "skills" / "draft"
    draft.mkdir()
    (draft / "SKILL.md").write_text(
        "---\nname: draft\ndescription: Draft skill.\n---\n",
        encoding="utf-8",
    )

    result = run_sv(["status"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "Index: fresh" in result.stdout
    assert "README: fresh" in result.stdout


def test_status_in_skill_vault_compares_freshness_in_canonical_index_order(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    result = run_sv(["init", str(vault)], cwd=tmp_path, home=home, git_runner=default_runner)
    assert result.exit_code == 0
    (vault / ".sv" / "index-config.toml").write_text(
        "schema_version = 1\n"
        'include_paths = ["z-root", "a-root"]\n',
        encoding="utf-8",
    )
    alpha = vault / "z-root" / "alpha"
    beta = vault / "a-root" / "beta"
    alpha.mkdir(parents=True)
    beta.mkdir(parents=True)
    (alpha / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill.\n---\n",
        encoding="utf-8",
    )
    (beta / "SKILL.md").write_text(
        "---\nname: beta\ndescription: Beta skill.\n---\n",
        encoding="utf-8",
    )
    index_result = run_sv(["index"], cwd=vault, home=home, git_runner=default_runner)
    assert index_result.exit_code == 0

    result = run_sv(["status"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "Index: fresh" in result.stdout
    assert "README: fresh" in result.stdout


def test_status_in_empty_skill_vault_does_not_refresh_unreachable_sources(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    init_result = run_sv(
        ["init", str(vault)], cwd=tmp_path, home=home, git_runner=default_runner
    )
    assert init_result.exit_code == 0
    missing_source = tmp_path / "missing-source"
    add_result = run_sv(
        ["repo", "add", str(missing_source)],
        cwd=vault,
        home=home,
        git_runner=default_runner,
    )
    assert add_result.exit_code == 0

    result = run_sv(["status"], cwd=vault, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "Skill-vault status" in result.stdout
    assert "No sv-managed vault skills found in this skill-vault." in result.stdout
    assert "repository" not in result.stderr
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stdout)
    assert_no_raw_control_characters(result.stderr)


def test_status_in_nested_non_git_project_with_empty_manifest_context_is_not_global(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "empty-manifest-project"
    project.mkdir()
    metadata = project / ".sv"
    metadata.mkdir()
    (metadata / "manifest.toml").write_text("schema_version = 1\n", encoding="utf-8")
    nested = project / "src" / "package"
    nested.mkdir(parents=True)

    result = run_sv(["status"], cwd=nested, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "No sv-managed Pi skills found in this project" in result.stdout
    assert "Global skill source status" not in result.stdout
    assert "No global skill sources configured" not in result.stdout


def test_status_in_nested_non_git_project_with_manifest_context_is_not_global(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "manifest-project"
    project.mkdir()
    entry = _source_skill(tmp_path / "source", "alpha")
    project_skills = project / ".pi" / "skills"
    add_project_skill(entry, project_skills)
    nested = project / "src" / "package"
    nested.mkdir(parents=True)

    result = run_sv(["status"], cwd=nested, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "Project sv-managed Pi skills" in result.stdout
    assert "alpha" in result.stdout
    assert "Global skill source status" not in result.stdout


def test_status_in_nested_non_git_project_index_context_is_not_global(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "indexed-project"
    project.mkdir()
    metadata = project / ".sv"
    metadata.mkdir()
    (metadata / "index.toml").write_text(
        "schema_version = 1\n"
        'kind = "project-index"\n'
        'generated_by = "sv"\n'
        'generated_at = "2026-05-15T00:00:00Z"\n',
        encoding="utf-8",
    )
    entry = _source_skill(tmp_path / "source", "alpha")
    project_skills = project / ".pi" / "skills"
    add_project_skill(entry, project_skills)
    nested = project / "src" / "package"
    nested.mkdir(parents=True)

    result = run_sv(["status"], cwd=nested, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "Project sv-managed Pi skills" in result.stdout
    assert "alpha" in result.stdout
    assert "Global skill source status" not in result.stdout


def test_status_at_home_with_global_manifest_shows_global_source_health(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    home.mkdir()
    paths = SvPaths.from_home(home)
    add_result = run_sv(["repo", "add", "Org/Skills"], cwd=home, home=home)
    assert add_result.exit_code == 0
    save_global_manifest(
        paths,
        {
            "Org/Skills": GlobalSourceState(
                repo_id="Org/Skills",
                repo_url="https://github.com/Org/Skills.git",
                backend="github-api",
                last_refresh_status="ok",
                index_hash="sha256:1bc04b5291c26a46d918139138b992d2de976d6851d0893b0476b85bfbdfc6e6",
                catalog_skill_count=3,
            )
        },
    )

    result = run_sv(["status"], cwd=home, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "Global skill source status" in result.stdout
    assert "Org/Skills" in result.stdout
    assert "github-api" in result.stdout
    assert "No sv-managed Pi skills found in this project" not in result.stdout
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stdout)
    assert_no_raw_control_characters(result.stderr)


def test_status_at_home_with_empty_global_manifest_and_pi_skills_is_global(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".pi" / "skills").mkdir(parents=True)
    save_global_manifest(SvPaths.from_home(home), {})

    result = run_sv(["status"], cwd=home, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "No global skill sources configured" in result.stdout
    assert "No sv-managed Pi skills found in this project" not in result.stdout
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)


def test_status_under_home_with_global_manifest_shows_global_source_health(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    outside = home / "work" / "scratch"
    outside.mkdir(parents=True)
    paths = SvPaths.from_home(home)
    add_result = run_sv(["repo", "add", "Org/Skills"], cwd=outside, home=home)
    assert add_result.exit_code == 0
    save_global_manifest(
        paths,
        {
            "Org/Skills": GlobalSourceState(
                repo_id="Org/Skills",
                repo_url="https://github.com/Org/Skills.git",
                backend="github-api",
                last_refresh_status="ok",
                index_hash="sha256:1bc04b5291c26a46d918139138b992d2de976d6851d0893b0476b85bfbdfc6e6",
                catalog_skill_count=3,
            )
        },
    )

    result = run_sv(["status"], cwd=outside, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "Global skill source status" in result.stdout
    assert "Org/Skills" in result.stdout
    assert "github-api" in result.stdout
    assert "No sv-managed Pi skills found in this project" not in result.stdout
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stdout)
    assert_no_raw_control_characters(result.stderr)


def test_status_global_context_ignores_project_state_in_global_manifest_path(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    outside.mkdir()
    entry = _source_skill(tmp_path / "source", "alpha")
    add_project_skill(entry, home / ".pi" / "skills")
    add_result = run_sv(["repo", "add", "Org/Skills"], cwd=outside, home=home)
    assert add_result.exit_code == 0

    result = run_sv(["status"], cwd=outside, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "Global skill source status" in result.stdout
    assert "Org/Skills" in result.stdout
    assert "unknown" in result.stdout
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)


def test_status_outside_git_project_shows_global_source_health(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    outside.mkdir()
    paths = SvPaths.from_home(home)
    add_result = run_sv(["repo", "add", "Org/Skills"], cwd=outside, home=home)
    assert add_result.exit_code == 0
    save_global_manifest(
        paths,
        {
            "Org/Skills": GlobalSourceState(
                repo_id="Org/Skills",
                repo_url="https://github.com/Org/Skills.git",
                backend="github-api",
                last_refresh_started_at="2026-05-15T00:00:00Z",
                last_refresh_finished_at="2026-05-15T00:00:02Z",
                last_refresh_status="ok",
                index_hash="sha256:1bc04b5291c26a46d918139138b992d2de976d6851d0893b0476b85bfbdfc6e6",
                catalog_hash="sha256:652f55016243bf1b9f1bbea46d5749ef892dbe394e46de9d66ab1aacf0b4af57",
                catalog_skill_count=3,
                health_status="ok",
                health_details="catalog refreshed",
            )
        },
    )

    result = run_sv(["status"], cwd=outside, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "Global skill source status" in result.stdout
    assert "Org/Skills" in result.stdout
    assert "github-api" in result.stdout
    assert "ok" in result.stdout
    assert "1bc04b5291c2..." in result.stdout
    assert "652f55016243..." in result.stdout
    assert "sha256:" not in result.stdout
    assert "3" in result.stdout
    assert "No Git project found" not in result.stdout
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stdout)
    assert_no_raw_control_characters(result.stderr)


def test_status_outside_git_project_shows_unknown_backend_for_legacy_global_state(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    outside.mkdir()
    paths = SvPaths.from_home(home)
    save_global_manifest(
        paths,
        {
            "Org/Skills": GlobalSourceState(
                repo_id="Org/Skills",
                repo_url="https://github.com/Org/Skills.git",
                last_refresh_status="ok",
                health_status="ok",
            )
        },
    )

    result = run_sv(["status"], cwd=outside, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "Global skill source status" in result.stdout
    assert "Org/Skills" in result.stdout
    assert "unknown" in result.stdout
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stdout)
    assert_no_raw_control_characters(result.stderr)



def test_status_without_source_config_still_detects_local_modifications(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    run_git(["init"], project)
    entry = _source_skill(tmp_path / "source", "alpha")
    project_skills = project / ".pi" / "skills"
    add_project_skill(entry, project_skills)

    (project_skills / "alpha" / "notes.md").write_text(
        "alpha local edit\n", encoding="utf-8"
    )

    result = run_sv(["status"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "alpha" in result.stdout
    assert "modified" in result.stdout
    assert load_manifest(project_skills)["alpha"].modified is True


def test_status_rejects_invalid_manifest_skill_name_before_inspecting_target(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    run_git(["init"], project)
    manifest = project / ".sv" / "manifest.toml"
    manifest.parent.mkdir()
    manifest.write_text(
        "schema_version = 1\n"
        "\n"
        "[[skills]]\n"
        'name = "../outside"\n'
        'target_kind = "project-agent"\n'
        'target_agent = "pi"\n'
        'target_path = ".pi/skills/../outside"\n'
        'source_repo_id = "Org/Skills"\n'
        'source_repo_url = "https://github.com/Org/Skills.git"\n'
        'source_path = "skills/outside"\n'
        'description = "Bad skill."\n',
        encoding="utf-8",
    )
    (project / ".pi" / "skills").mkdir(parents=True)
    outside_target = tmp_path / "outside-target"
    outside_target.mkdir()
    (project / ".pi" / "outside").symlink_to(
        outside_target, target_is_directory=True
    )

    result = run_sv(["status"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 1
    assert "Invalid Pi skill directory" in result.stderr


def test_status_in_normal_project_does_not_present_unmanaged_manual_skills_as_managed(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    run_git(["init"], project)
    manual = project / ".pi" / "skills" / "manual"
    manual.mkdir(parents=True)
    (manual / "SKILL.md").write_text(
        "---\nname: manual\ndescription: Manual skill.\n---\n",
        encoding="utf-8",
    )

    result = run_sv(["status"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "No sv-managed Pi skills found in this project." in result.stdout
    assert "manual" not in result.stdout
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stdout)
    assert_no_raw_control_characters(result.stderr)


def test_status_in_empty_project_does_not_refresh_unreachable_sources(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    run_git(["init"], project)
    missing_source = tmp_path / "missing-source"
    add_result = run_sv(
        ["repo", "add", str(missing_source)],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )
    assert add_result.exit_code == 0

    result = run_sv(["status"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "No sv-managed Pi skills found in this project." in result.stdout
    assert "Global skill source status" not in result.stdout
    assert "repository" not in result.stderr
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)
    assert_no_raw_control_characters(result.stdout)
    assert_no_raw_control_characters(result.stderr)


def test_status_refreshes_sources_even_when_metadata_cache_is_fresh(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    assert run_sv(["add", "alpha"], cwd=project, home=home).exit_code == 0
    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)

    result = run_sv(["status"], cwd=project, home=home, git_runner=default_runner)

    assert result.exit_code == 0
    assert "alpha" in result.stdout
    assert "update available" in result.stdout


def test_status_cached_does_not_refresh_source(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)

    assert run_sv(["add", "alpha"], cwd=project, home=home).exit_code == 0
    shutil.rmtree(source / ".git")

    def fail_git(args, cwd=None):
        raise AssertionError(f"--cached must not call Git or GitHub backends: {args}")

    result = run_sv(["status", "--cached"], cwd=project, home=home, git_runner=fail_git)

    assert result.exit_code == 0
    assert "alpha" in result.stdout

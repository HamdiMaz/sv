from pathlib import Path

from sv.manifest import ManifestEntry, load_manifest, save_manifest
from tests.helpers import (
    assert_no_partial_sv_dirs,
    assert_no_raw_control_characters,
    assert_no_traceback,
    configure_source,
    make_source_repo,
)


def _forbid_git_calls(args, cwd=None):
    raise AssertionError(f"unexpected git call: {args}")


def _manifest_entry(skill: str) -> ManifestEntry:
    return ManifestEntry(
        name=skill,
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        source_path=f"skills/{skill}",
        description=f"{skill.title()} skill.",
    )


def test_remove_missing_skill_fails_without_git_or_mutation(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    kept = project_skills / "alpha"
    kept.mkdir()
    (kept / "notes.md").write_text("keep me\n")
    save_manifest(project_skills, {"alpha": _manifest_entry("alpha")})

    result = run_sv(
        ["remove", "missing"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
    )

    assert result.exit_code == 1
    assert_no_raw_control_characters(result.stderr)
    assert_no_traceback(result.stderr)
    assert "Pi skill 'missing' was not found in this project." in result.stderr
    assert kept.exists()
    assert (kept / "notes.md").read_text() == "keep me\n"
    assert load_manifest(project_skills)["alpha"].repo_id == "Org/Skills"


def test_remove_invalid_skill_name_fails_without_git_or_mutation(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"

    result = run_sv(
        ["remove", "../alpha"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
    )

    assert result.exit_code == 1
    assert_no_raw_control_characters(result.stderr)
    assert_no_traceback(result.stderr)
    assert "Invalid skill name '../alpha'." in result.stderr


def test_remove_existing_skill_updates_manifest_and_removes_directory(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    target = project_skills / "alpha"
    target.mkdir(parents=True)
    (target / "notes.md").write_text("alpha local\n")
    other = project_skills / "beta"
    other.mkdir()
    (other / "notes.md").write_text("beta local\n")
    save_manifest(
        project_skills,
        {
            "alpha": _manifest_entry("alpha"),
            "beta": _manifest_entry("beta"),
        },
    )

    result = run_sv(
        ["remove", "alpha"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
    )

    assert result.exit_code == 0
    assert_no_raw_control_characters(result.stdout)
    assert_no_traceback(result.stdout)
    assert f"Removed Pi skill 'alpha' from {target}" in result.stdout
    assert not target.exists()
    assert other.exists()
    manifest = load_manifest(project_skills)
    assert sorted(manifest) == ["beta"]
    assert manifest["beta"].repo_id == "Org/Skills"
    assert_no_partial_sv_dirs(project_skills)


def test_remove_interactive_skills_with_no_projects_skills_does_nothing_without_git(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"

    result = run_sv(
        ["remove", "-l"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
    )

    assert result.exit_code == 0
    assert_no_raw_control_characters(result.stdout)
    assert_no_traceback(result.stdout)
    assert "No Pi skills found to remove." in result.stdout


def test_remove_interactive_removes_selected_project_skills_without_source_repo(tmp_path: Path, run_sv):
    source = make_source_repo(tmp_path, "source-for-remove")
    home = tmp_path / "home"
    project = tmp_path / "project"
    configure_source(source, project, home)

    project_skills = project / ".pi" / "skills"
    local = project_skills / "gamma"
    local.mkdir(parents=True)
    (local / "notes.md").write_text("gamma local\n")
    save_manifest(project_skills, {"gamma": _manifest_entry("gamma")})

    def skill_selector(skills):
        assert skills == ["gamma"]
        return ["gamma"]

    result = run_sv(
        ["remove", "-l"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
        skill_selector=skill_selector,
    )

    assert result.exit_code == 0
    assert_no_raw_control_characters(result.stdout)
    assert_no_traceback(result.stdout)
    assert not local.exists()
    assert "Removed Pi skill 'gamma' from" in result.stdout
    assert not (project_skills / ".sv-manifest.toml").exists()


def test_remove_interactive_with_skill_reports_help_text_without_git(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"

    result = run_sv(
        ["remove", "alpha", "-l"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
    )

    assert result.exit_code == 1
    assert_no_raw_control_characters(result.stderr)
    assert_no_traceback(result.stderr)
    assert "Use -l by itself, or provide a skill name." in result.stderr

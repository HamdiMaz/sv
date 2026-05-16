from dataclasses import replace
from pathlib import Path
import sys

from sv.manifest import ManifestEntry, load_manifest, save_manifest
from tests.helpers import (
    assert_no_partial_sv_dirs,
    assert_no_raw_control_characters,
    assert_no_traceback,
    run_git,
)


def _forbid_git_calls(args, cwd=None):
    raise AssertionError(f"unexpected git call: {args}")


class _TtyProxy:
    def __init__(self, wrapped):
        self._wrapped = wrapped

    def isatty(self):
        return True

    def write(self, value):
        return self._wrapped.write(value)

    def flush(self):
        return self._wrapped.flush()


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


def test_remove_existing_skill_updates_canonical_manifest_and_removes_directory(
    tmp_path: Path, run_sv
):
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
    assert (project / ".sv" / "manifest.toml").is_file()
    assert not (project_skills / ".sv-manifest.toml").exists()
    manifest = load_manifest(project_skills)
    assert sorted(manifest) == ["beta"]
    assert manifest["beta"].repo_id == "Org/Skills"
    assert manifest["beta"].target_path == ".pi/skills/beta"
    assert_no_partial_sv_dirs(project_skills)


def test_remove_from_git_subdirectory_targets_repo_root_manifest(
    tmp_path: Path, run_sv
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    nested = project / "nested" / "work"
    nested.mkdir(parents=True)
    run_git(["init"], project)
    project_skills = project / ".pi" / "skills"
    target = project_skills / "alpha"
    target.mkdir(parents=True)
    (target / "notes.md").write_text("alpha local\n")
    save_manifest(project_skills, {"alpha": _manifest_entry("alpha")})

    result = run_sv(
        ["remove", "alpha"],
        cwd=nested,
        home=home,
        git_runner=_forbid_git_calls,
    )

    assert result.exit_code == 0
    assert "Removed Pi skill 'alpha'" in result.stdout
    assert not target.exists()
    assert load_manifest(project_skills) == {}
    assert not (nested / ".pi").exists()
    assert not (nested / ".sv").exists()


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


def test_remove_interactive_requires_yes_in_non_tty_without_mutation(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    local = project_skills / "gamma"
    local.mkdir(parents=True)
    (local / "notes.md").write_text("gamma local\n")
    save_manifest(project_skills, {"gamma": _manifest_entry("gamma")})

    result = run_sv(
        ["remove", "-l"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
        skill_selector=lambda skills, **kwargs: skills,
    )

    assert result.exit_code == 1
    assert "Non-TTY 'sv remove -l' requires --yes." in result.stderr
    assert local.exists()
    assert sorted(load_manifest(project_skills)) == ["gamma"]


def test_remove_interactive_removes_selected_project_skills_without_source_repo(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    local = project_skills / "gamma"
    local.mkdir(parents=True)
    (local / "notes.md").write_text("gamma local\n")
    save_manifest(project_skills, {"gamma": _manifest_entry("gamma")})

    def skill_selector(skills):
        assert skills == ["gamma"]
        return ["gamma"]

    result = run_sv(
        ["remove", "-l", "--yes"],
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


def test_remove_interactive_declined_confirmation_leaves_skills_unchanged(
    tmp_path: Path, run_sv, monkeypatch
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    local = project_skills / "gamma"
    local.mkdir(parents=True)
    (local / "notes.md").write_text("gamma local\n")
    save_manifest(project_skills, {"gamma": _manifest_entry("gamma")})
    monkeypatch.setattr(sys, "stdin", _TtyProxy(sys.stdin))
    monkeypatch.setattr(sys, "stdout", _TtyProxy(sys.stdout))
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    result = run_sv(
        ["remove", "-l"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
        skill_selector=lambda skills, **kwargs: skills,
    )

    assert result.exit_code == 0
    assert local.exists()
    assert sorted(load_manifest(project_skills)) == ["gamma"]
    assert "No skills removed." in result.stdout


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
    assert "Use -l by itself, or provide a skill name/--all." in result.stderr


def test_remove_all_requires_yes_in_non_tty_without_mutation(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    managed = project_skills / "alpha"
    managed.mkdir(parents=True)
    (managed / "notes.md").write_text("managed\n")
    manual = project_skills / "manual"
    manual.mkdir()
    (manual / "notes.md").write_text("manual\n")
    save_manifest(project_skills, {"alpha": _manifest_entry("alpha")})

    result = run_sv(
        ["remove", "--all"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
    )

    assert result.exit_code == 1
    assert "Non-TTY 'sv remove --all' requires --yes." in result.stderr
    assert managed.exists()
    assert manual.exists()
    assert sorted(load_manifest(project_skills)) == ["alpha"]


def test_remove_all_yes_removes_only_manifest_managed_skills(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    alpha = project_skills / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "notes.md").write_text("alpha\n")
    beta = project_skills / "beta"
    beta.mkdir()
    (beta / "notes.md").write_text("beta\n")
    manual = project_skills / "manual"
    manual.mkdir()
    (manual / "notes.md").write_text("manual\n")
    save_manifest(
        project_skills,
        {"alpha": _manifest_entry("alpha"), "beta": _manifest_entry("beta")},
    )

    result = run_sv(
        ["remove", "--all", "--yes"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
    )

    assert result.exit_code == 0
    assert not alpha.exists()
    assert not beta.exists()
    assert manual.exists()
    assert (manual / "notes.md").read_text() == "manual\n"
    assert load_manifest(project_skills) == {}
    assert "Removed Pi skill 'alpha'" in result.stdout
    assert "Removed Pi skill 'beta'" in result.stdout
    assert "manual" not in result.stdout


def test_remove_interactive_lists_managed_rows_and_ignores_unmanaged(tmp_path: Path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    managed = project_skills / "alpha"
    managed.mkdir(parents=True)
    (managed / "notes.md").write_text("managed\n")
    manual = project_skills / "manual"
    manual.mkdir()
    (manual / "notes.md").write_text("manual\n")
    save_manifest(
        project_skills,
        {"alpha": replace(_manifest_entry("alpha"), orphan=True)},
    )
    selector_calls = []

    def skill_selector(skills, **kwargs):
        selector_calls.append((skills, kwargs))
        return skills

    result = run_sv(
        ["remove", "-l", "--yes"],
        cwd=project,
        home=home,
        git_runner=_forbid_git_calls,
        skill_selector=skill_selector,
    )

    assert result.exit_code == 0
    assert selector_calls[0][0] == ["alpha"]
    label = selector_calls[0][1]["item_label"]("alpha")
    assert "Skill" in label
    assert "Target" in label
    assert "Source/Status" in label
    assert ".pi/skills/alpha" in label
    assert "orphan" in label
    assert not managed.exists()
    assert manual.exists()

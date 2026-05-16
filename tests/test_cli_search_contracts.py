from pathlib import Path
import base64
import subprocess

from sv.config import SvPaths


def test_search_with_no_configured_repos_prints_next_step_and_skips_git(
    tmp_path: Path,
    run_sv,
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text("repos = []\n")

    def git_runner(args, cwd=None):
        raise AssertionError(f"unexpected git call: {args}")

    result = run_sv(["search", "alpha"], cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 0
    assert (
        "No skill source repos configured. Add one with 'sv repo add <owner/repo>'."
        in result.stdout
    )
    assert result.stderr == ""


def test_search_uses_github_index_without_cloning_or_downloading_skill_folders(
    tmp_path: Path,
    run_sv,
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\n'
        'id = "Org/Skills"\n'
        'url = "https://github.com/Org/Skills.git"\n'
    )
    index_text = """
    schema_version = 1
    kind = "skill-vault"
    generated_by = "sv"
    generated_at = "2026-05-15T00:00:00Z"

    [[skills]]
    name = "alpha"
    description = "Alpha remote index skill."
    source_path = "skills/alpha"
    content_hash = "sha256:alpha"
    skill_file_hash = "sha256:alpha-skill"
    """
    calls: list[tuple[list[str], Path | None]] = []

    def git_runner(args, cwd=None):
        command = list(args)
        calls.append((command, cwd))
        if command == [
            "gh",
            "api",
            "/repos/Org/Skills/contents/.sv/index.toml",
            "--jq",
            ".content",
        ]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=base64.b64encode(index_text.encode("utf-8")).decode("ascii"),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    result = run_sv(["search", "alpha"], cwd=project, home=home, git_runner=git_runner)

    assert result.exit_code == 0
    assert "Rank" in result.stdout
    assert "Skill" in result.stdout
    assert "Repo" in result.stdout
    assert "Path" in result.stdout
    assert "alpha" in result.stdout
    assert "Alpha remote index skill." in result.stdout
    assert result.stderr == ""
    assert calls == [
        (
            [
                "gh",
                "api",
                "/repos/Org/Skills/contents/.sv/index.toml",
                "--jq",
                ".content",
            ],
            None,
        )
    ]


def test_search_with_no_matches_prints_empty_message(
    tmp_path: Path,
    run_sv,
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\n'
        'id = "Org/Skills"\n'
        'url = "https://github.com/Org/Skills.git"\n'
    )
    index_text = """
    schema_version = 1
    kind = "skill-vault"
    generated_by = "sv"
    generated_at = "2026-05-15T00:00:00Z"

    [[skills]]
    name = "alpha"
    description = "Alpha remote index skill."
    source_path = "skills/alpha"
    content_hash = "sha256:alpha"
    skill_file_hash = "sha256:alpha-skill"
    """

    def git_runner(args, cwd=None):
        command = list(args)
        if command == [
            "gh",
            "api",
            "/repos/Org/Skills/contents/.sv/index.toml",
            "--jq",
            ".content",
        ]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=base64.b64encode(index_text.encode("utf-8")).decode("ascii"),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    result = run_sv(
        ["search", "missing\x1bskill"], cwd=project, home=home, git_runner=git_runner
    )

    assert result.exit_code == 0
    assert "No matching skills found for 'missing\\x1bskill'." in result.stdout
    assert result.stderr == ""


def test_search_matches_description_repo_id_and_source_path_with_ranked_output(
    tmp_path: Path,
    run_sv,
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\n'
        'id = "Org/Primary"\n'
        'url = "https://github.com/Org/Primary.git"\n\n'
        '[[repos]]\n'
        'id = "Team/PathSource"\n'
        'url = "https://github.com/Team/PathSource.git"\n'
    )
    primary_index = """
    schema_version = 1
    kind = "skill-vault"
    generated_by = "sv"
    generated_at = "2026-05-15T00:00:00Z"

    [[skills]]
    name = "doc-helper"
    description = "Writes docs for release notes."
    source_path = "skills/doc-helper"
    content_hash = "sha256:doc"
    skill_file_hash = "sha256:doc-skill"

    [[skills]]
    name = "other"
    description = "Unrelated skill."
    source_path = "skills/other"
    content_hash = "sha256:other"
    skill_file_hash = "sha256:other-skill"
    """
    path_index = """
    schema_version = 1
    kind = "skill-vault"
    generated_by = "sv"
    generated_at = "2026-05-15T00:00:00Z"

    [[skills]]
    name = "deep-skill"
    description = "Specialized helper."
    source_path = "packages/pi/skills/deep-skill"
    content_hash = "sha256:deep"
    skill_file_hash = "sha256:deep-skill"
    """

    def git_runner(args, cwd=None):
        command = list(args)
        if command == [
            "gh",
            "api",
            "/repos/Org/Primary/contents/.sv/index.toml",
            "--jq",
            ".content",
        ]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=base64.b64encode(primary_index.encode("utf-8")).decode("ascii"),
                stderr="",
            )
        if command == [
            "gh",
            "api",
            "/repos/Team/PathSource/contents/.sv/index.toml",
            "--jq",
            ".content",
        ]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=base64.b64encode(path_index.encode("utf-8")).decode("ascii"),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    description_result = run_sv(
        ["search", "release"], cwd=project, home=home, git_runner=git_runner
    )
    repo_result = run_sv(
        ["search", "pathsource"], cwd=project, home=home, git_runner=git_runner
    )
    path_result = run_sv(
        ["search", "packages/pi"], cwd=project, home=home, git_runner=git_runner
    )

    assert description_result.exit_code == 0
    assert "doc-helper" in description_result.stdout
    assert "other" not in description_result.stdout

    assert repo_result.exit_code == 0
    assert "deep-skill" in repo_result.stdout
    assert "Team/PathSource" in repo_result.stdout

    assert path_result.exit_code == 0
    assert "deep-skill" in path_result.stdout
    assert "packages/pi/skills/deep-skill" in path_result.stdout

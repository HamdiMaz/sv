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
    paths.config_file.write_text("schema_version = 1\nrepos = []\n")

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
        'schema_version = 1\n[[repos]]\n'
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
    content_hash = "sha256:8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8"
    skill_file_hash = "sha256:63b6db06d8ca622e5986709498b492dfc53aad421a238dba44b7146d37d934bf"
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
        'schema_version = 1\n[[repos]]\n'
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
    content_hash = "sha256:8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8"
    skill_file_hash = "sha256:63b6db06d8ca622e5986709498b492dfc53aad421a238dba44b7146d37d934bf"
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
        'schema_version = 1\n[[repos]]\n'
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
    content_hash = "sha256:139d544b821b13ebea14f1b0fe18577222e415c2966e3a3511c4196055232202"
    skill_file_hash = "sha256:83cdead05d09802d969010b7ebb4d71654a29fd6582029e20ec151c211f0f463"

    [[skills]]
    name = "other"
    description = "Unrelated skill."
    source_path = "skills/other"
    content_hash = "sha256:d9298a10d1b0735837dc4bd85dac641b0f3cef27a47e5d53a54f2f3f5b2fcffa"
    skill_file_hash = "sha256:33733b9c9a59de37251edb1af6d3ecb3b6551dc5a40e5b37aeea50b14d537778"
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
    content_hash = "sha256:74611c1d6455b534323a21f8133a6f43dc3a8188e7b946f96dcc28dde932fcb2"
    skill_file_hash = "sha256:0d3a78084b5d74bb21cb33cc0982b382fba1c43eabfb584a048219a23f2eef40"
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

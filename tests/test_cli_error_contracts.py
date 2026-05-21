from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import os
import subprocess

import pytest

from sv.config import SvPaths
from sv.source import SourceBackendError, default_runner
from tests.helpers import (
    assert_no_raw_control_characters,
    assert_no_traceback,
    configure_source,
    make_source_repo,
)


@dataclass(frozen=True)
class CliErrorScenario:
    args: list[str]
    home: Path
    project: Path
    expected_fragment: str
    git_runner: Callable | None = None
    process_runner: Callable | None = None


ScenarioBuilder = Callable[[Path], CliErrorScenario]


def _forbid_git_calls(args, cwd=None):
    raise AssertionError(f"unexpected git call: {args}")


def _assert_cli_stderr_has_no_raw_control_characters(stderr: str) -> None:
    assert_no_raw_control_characters(stderr)
    for char in ("\r", "\t"):
        assert char not in stderr, f"stderr contains raw control character {char!r}:\n{stderr}"


def _write_config(home: Path, text: str) -> None:
    paths = SvPaths.from_home(home)
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text(text)


class _FailingBackend:
    def __init__(self, name: str, detail: str, hint: str | None = None):
        self.name = name
        self._detail = detail
        self._hint = hint

    def read_index(self):
        raise SourceBackendError("reading .sv/index.toml", self._detail, hint=self._hint)

    def list_candidate_skill_files(self, configured_skills_paths=()):
        raise AssertionError("read_index failure should stop this backend")

    def read_file(self, path: str):
        raise AssertionError("read_index failure should stop this backend")

    def materialize_folder(self, source_path: str, destination: Path) -> None:
        raise AssertionError("read_index failure should stop this backend")


def test_missing_git_during_init_is_actionable_and_traceback_free(tmp_path: Path, run_sv) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def missing_git(args, cwd=None):
        raise FileNotFoundError("git\x1b[2J")

    result = run_sv(["init", "vault"], cwd=project, home=home, git_runner=missing_git)

    assert result.exit_code == 1
    assert "Git is required but was not found on PATH." in result.stderr
    assert "Install Git" in result.stderr
    assert_no_traceback(result.stderr)
    _assert_cli_stderr_has_no_raw_control_characters(result.stderr)


def test_auth_rate_limit_failures_explain_api_and_git_fallbacks(
    tmp_path: Path, run_sv, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    _write_config(
        home,
        'schema_version = 1\n[[repos]]\n'
        'id = "Org/Skills"\n'
        'url = "https://github.com/Org/Skills.git"\n',
    )

    def fake_backends(repo, paths, runner=None, *, update=True):
        return (
            _FailingBackend(
                "github-gh-api",
                "API rate limit exceeded for 1.2.3.4\x1b[2J",
                "Run 'gh auth login' or set GH_TOKEN before retrying.",
            ),
            _FailingBackend(
                "git-treeless-partial",
                "Git is required but was not found on PATH.",
            ),
        )

    monkeypatch.setattr("sv.cli.source_backends_for_repo", fake_backends)

    result = run_sv(["list"], cwd=project, home=home)

    assert result.exit_code == 1
    assert "sv could not refresh Org/Skills" in result.stderr
    assert "GitHub API auth/rate limits" in result.stderr
    assert "lightweight Git fallback" in result.stderr
    assert "gh auth login" in result.stderr
    assert "GH_TOKEN" in result.stderr
    assert_no_traceback(result.stderr)
    _assert_cli_stderr_has_no_raw_control_characters(result.stderr)


@pytest.mark.integration
def test_duplicate_choice_error_has_no_raw_control_characters(
    tmp_path: Path, run_sv
) -> None:
    source_a = make_source_repo(tmp_path, "source-a")
    source_b = make_source_repo(tmp_path, "source-b")
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source_a, project, home)
    configure_source(source_b, project, home)

    result = run_sv(
        ["add", "alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    assert result.exit_code == 1
    assert "Multiple source skills match 'alpha'" in result.stderr
    assert_no_traceback(result.stderr)
    _assert_cli_stderr_has_no_raw_control_characters(result.stderr)


def _malformed_config(tmp_path: Path) -> CliErrorScenario:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    _write_config(home, "repos = [\n")
    return CliErrorScenario(
        args=["list"],
        home=home,
        project=project,
        expected_fragment="Failed to read sv config",
    )


def _missing_skill(tmp_path: Path) -> CliErrorScenario:
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".pi").mkdir(parents=True)
    _write_config(home, "schema_version = 1\nrepos = []\n")
    return CliErrorScenario(
        args=["add", "missing"],
        home=home,
        project=project,
        expected_fragment="Skill 'missing' was not found in configured source repos.",
    )


def _invalid_skill(tmp_path: Path) -> CliErrorScenario:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    return CliErrorScenario(
        args=["add", "../alpha"],
        home=home,
        project=project,
        git_runner=_forbid_git_calls,
        expected_fragment="Invalid skill name '../alpha'.",
    )


def _git_failure(tmp_path: Path) -> CliErrorScenario:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    _write_config(
        home,
        'schema_version = 1\n[[repos]]\n'
        'id = "HamdiMaz/Skills"\n'
        'url = "https://github.com/HamdiMaz/Skills.git"\n',
    )

    def git_runner(args, cwd=None):
        if args == ["git", "--version"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="git version 2.39.0\n",
            )
        return subprocess.CompletedProcess(
            args=args,
            returncode=128,
            stderr="fatal: clone failed \x1b[31mred\n",
        )

    return CliErrorScenario(
        args=["list"],
        home=home,
        project=project,
        git_runner=git_runner,
        expected_fragment="fatal: clone failed \\x1b[31mred",
    )


def _pi_launch_os_error(tmp_path: Path) -> CliErrorScenario:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    def process_runner(command: list[str]) -> int:
        raise OSError("denied\x1b[2J")

    return CliErrorScenario(
        args=["run", "pi"],
        home=home,
        project=project,
        process_runner=process_runner,
        expected_fragment="Unable to run 'pi': denied\\x1b[2J",
    )


def _malformed_manifest_during_add(tmp_path: Path) -> CliErrorScenario:
    source = make_source_repo(tmp_path, "source-add-manifest")
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    project_skills = project / ".pi" / "skills"
    target = project_skills / "alpha"
    target.mkdir(parents=True)
    (target / "notes.md").write_text("local alpha\n")
    (project_skills / ".sv-manifest.toml").write_text("[[skills]]\nname = \n")
    return CliErrorScenario(
        args=["add", "alpha"],
        home=home,
        project=project,
        git_runner=default_runner,
        expected_fragment="Failed to read sv manifest",
    )


def _malformed_manifest_during_remove(tmp_path: Path) -> CliErrorScenario:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    target = project_skills / "alpha"
    target.mkdir(parents=True)
    (target / "notes.md").write_text("local alpha\n")
    (project_skills / ".sv-manifest.toml").write_text("[[skills]]\nname = \n")
    return CliErrorScenario(
        args=["remove", "alpha"],
        home=home,
        project=project,
        expected_fragment="Failed to read sv manifest",
    )


def _malformed_manifest_during_sync(tmp_path: Path) -> CliErrorScenario:
    source = make_source_repo(tmp_path, "source-sync-manifest")
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    configure_source(source, project, home)
    project_skills = project / ".pi" / "skills"
    target = project_skills / "alpha"
    target.mkdir(parents=True)
    (target / "notes.md").write_text("local alpha\n")
    (project_skills / ".sv-manifest.toml").write_text("[[skills]]\nname = \n")
    return CliErrorScenario(
        args=["sync"],
        home=home,
        project=project,
        git_runner=default_runner,
        expected_fragment="Failed to read sv manifest",
    )


def _unsafe_symlinked_pi_path(tmp_path: Path) -> CliErrorScenario:
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support is required")
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside-pi"
    (outside / "skills").mkdir(parents=True)
    os.symlink(outside, project / ".pi", target_is_directory=True)
    return CliErrorScenario(
        args=["remove", "alpha"],
        home=home,
        project=project,
        expected_fragment="Refusing to use symlinked Pi agent folder",
    )


def _unsafe_symlinked_skill_path(tmp_path: Path) -> CliErrorScenario:
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support is required")
    home = tmp_path / "home"
    project = tmp_path / "project"
    project_skills = project / ".pi" / "skills"
    project_skills.mkdir(parents=True)
    outside = tmp_path / "outside-alpha"
    outside.mkdir()
    os.symlink(outside, project_skills / "alpha", target_is_directory=True)
    return CliErrorScenario(
        args=["remove", "alpha"],
        home=home,
        project=project,
        expected_fragment="Refusing to manage symlinked Pi skill 'alpha'",
    )


@pytest.mark.parametrize(
    "build_scenario",
    [
        pytest.param(_malformed_config, id="malformed-config"),
        pytest.param(_missing_skill, id="missing-skill"),
        pytest.param(_invalid_skill, id="invalid-skill"),
        pytest.param(_git_failure, id="git-failure"),
        pytest.param(_pi_launch_os_error, id="pi-launch-os-error"),
        pytest.param(
            _malformed_manifest_during_add,
            marks=pytest.mark.integration,
            id="malformed-manifest-add",
        ),
        pytest.param(_malformed_manifest_during_remove, id="malformed-manifest-remove"),
        pytest.param(
            _malformed_manifest_during_sync,
            marks=pytest.mark.integration,
            id="malformed-manifest-sync",
        ),
        pytest.param(_unsafe_symlinked_pi_path, id="unsafe-symlinked-pi-path"),
        pytest.param(_unsafe_symlinked_skill_path, id="unsafe-symlinked-skill-path"),
    ],
)
def test_cli_error_paths_are_traceback_free(
    tmp_path: Path, run_sv, build_scenario: ScenarioBuilder
) -> None:
    scenario = build_scenario(tmp_path)

    result = run_sv(
        scenario.args,
        cwd=scenario.project,
        home=scenario.home,
        git_runner=scenario.git_runner,
        process_runner=scenario.process_runner,
    )

    assert result.exit_code == 1
    assert result.stderr.startswith("error: ")
    assert scenario.expected_fragment in result.stderr
    assert_no_traceback(result.stderr)
    _assert_cli_stderr_has_no_raw_control_characters(result.stderr)

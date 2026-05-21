from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import TYPE_CHECKING
import unicodedata

from sv.catalog import SourceSkill

import pytest

if TYPE_CHECKING:
    from sv.cli import SkillChooser, SkillSelector


def display_width(value: str) -> int:
    width = 0
    for char in value:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


@dataclass
class SvResult:
    exit_code: int
    stdout: str
    stderr: str


def assert_no_traceback(output: str) -> None:
    if "Traceback" in output:
        raise AssertionError(f"Unexpected traceback output:\n{output}")


def assert_no_raw_control_characters(output: str) -> None:
    def is_raw_control_character(char: str) -> bool:
        if char in {"\n", "\r", "\t"}:
            return False
        codepoint = ord(char)
        return codepoint < 0x20 or 0x7F <= codepoint <= 0x9F

    for index, char in enumerate(output):
        if is_raw_control_character(char):
            codepoint = ord(char)
            raise AssertionError(
                f"Output contains raw control character U+{codepoint:04x} at index {index}:"
                f" {char!r}"
            )


def assert_no_partial_sv_dirs(project_skills_dir: Path) -> None:
    if not project_skills_dir.exists():
        return

    partial_dirs = [
        path.name
        for path in project_skills_dir.iterdir()
        if path.name.startswith(".")
        and (
            path.name.endswith(".sv-add-tmp")
            or path.name.endswith(".sv-sync-tmp")
            or path.name.endswith(".sv-remove-backup")
        )
    ]

    assert not partial_dirs, (
        f"Found partial sv directories in {project_skills_dir}: {partial_dirs}"
    )


def parse_sv(args: list[str] | tuple[str, ...]) -> Namespace:
    from sv.cli import build_parser

    return build_parser().parse_args(args)


def run_sv(
    args: list[str] | tuple[str, ...] | Namespace,
    *,
    cwd: Path,
    home: Path,
    capsys,
    git_runner=None,
    process_runner=None,
    skill_selector: "SkillSelector | None" = None,
    skill_chooser: "SkillChooser | None" = None,
) -> SvResult:
    from sv.cli import handle, select_skills

    parsed = args if isinstance(args, Namespace) else parse_sv(args)
    capsys.readouterr()

    exit_code = handle(
        parsed,
        cwd=cwd,
        home=home,
        git_runner=git_runner,
        process_runner=process_runner,
        skill_selector=select_skills if skill_selector is None else skill_selector,
        skill_chooser=skill_chooser,
    )
    captured = capsys.readouterr()
    return SvResult(
        exit_code=exit_code,
        stdout=captured.out,
        stderr=captured.err,
    )


def run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True)


def write_source_skill(source: Path, name: str, description: str, body: str) -> None:
    skill_dir = source / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"
    )
    (skill_dir / "notes.md").write_text(body)


def make_catalog_source_skill(tmp_path: Path, name: str = "alpha") -> SourceSkill:
    source = tmp_path / "source"
    skill_dir = source / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name.title()} skill.\n---\n"
    )
    (skill_dir / "notes.md").write_text(f"{name} source\n")
    return SourceSkill(
        name=name,
        description=f"{name.title()} skill.",
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        repo_path=source,
        source_path=skill_dir,
    )


def make_source_repo(tmp_path: Path, name: str = "skill-source") -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is required for integration tests")

    source = tmp_path / name
    write_source_skill(source, "alpha", "Alpha skill.", "alpha v1\n")
    write_source_skill(source, "beta", "Beta skill.", "beta v1\n")

    run_git(["init"], source)
    run_git(["config", "user.email", "tests@example.com"], source)
    run_git(["config", "user.name", "sv tests"], source)
    run_git(["add", "skills"], source)
    run_git(["commit", "-m", "initial skills"], source)
    return source


def configure_source(source: Path, project: Path, home: Path) -> None:
    from sv.cli import build_parser, handle

    exit_code = handle(
        build_parser().parse_args(["repo", "add", str(source)]), cwd=project, home=home
    )
    assert exit_code == 0

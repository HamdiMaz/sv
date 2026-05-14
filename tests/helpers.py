from pathlib import Path
import shutil
import subprocess
import unicodedata

import pytest

from sv.cli import build_parser, handle


def display_width(value: str) -> int:
    width = 0
    for char in value:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True)


def write_source_skill(source: Path, name: str, description: str, body: str) -> None:
    skill_dir = source / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"
    )
    (skill_dir / "notes.md").write_text(body)


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
    exit_code = handle(
        build_parser().parse_args(["repo", "add", str(source)]), cwd=project, home=home
    )
    assert exit_code == 0

from __future__ import annotations

from pathlib import Path
import re
import shlex

import pytest

from tests.helpers import parse_sv


EXPECTED_DOC_LINKS = {
    "getting-started": "docs/getting-started.md",
    "examples": "docs/examples.md",
    "usage": "docs/usage.md",
    "commands": "docs/commands.md",
    "output": "docs/output.md",
    "testing": "docs/testing.md",
    "troubleshooting": "docs/troubleshooting.md",
}

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
README_PATH = _PROJECT_ROOT / "README.md"
DOCS_DIR = _PROJECT_ROOT / "docs"


COMMON_DOC_COMMAND_EXAMPLES = [
    "sv list",
    "sv add find-docs",
    "sv add repo:skill",
    "sv add -l",
    "sv add --all",
    "sv remove",
    "sv sync",
    "sv update",
    "sv run -- --model fast",
    "sv repo add HamdiMaz/Skills",
    "sv repo list",
    "sv repo remove HamdiMaz/Skills",
]



def _documented_paths() -> list[Path]:
    paths = [README_PATH]
    paths.extend(sorted(DOCS_DIR.glob("*.md")))
    return paths


def _extract_documented_sv_commands() -> list[tuple[str, int, str]]:
    commands: list[tuple[str, int, str]] = []

    for path in _documented_paths():
        lines = path.read_text(encoding="utf-8").splitlines()
        in_code_block = False
        for line_no, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if not in_code_block:
                continue
            if not stripped or stripped.startswith("#"):
                continue
            if not stripped.startswith("sv "):
                continue
            if "<" in stripped or ">" in stripped:
                continue

            try:
                parsed = shlex.split(stripped)
            except ValueError:
                continue

            if not parsed or parsed[0] != "sv":
                continue

            commands.append(
                (str(path.relative_to(_PROJECT_ROOT)), line_no, " ".join(parsed))
            )

    return commands


def _parse_sv_command(command: str) -> None:
    args = shlex.split(command)
    if not args:
        raise ValueError("Empty command line cannot be parsed")
    if args[0] == "sv":
        args = args[1:]
    parse_sv(args)


def _normalize_markdown_link_target(raw_target: str) -> str:
    """Return a local path target normalized for deterministic link checks."""

    target = raw_target.strip()
    target = target.split(" ", 1)[0]
    return target.split("#", 1)[0]


@pytest.mark.parametrize(
    ("document_path", "line_number", "command"),
    _extract_documented_sv_commands(),
)
def test_documented_sv_commands_parse(document_path, line_number, command):
    try:
        _parse_sv_command(command)
    except SystemExit as exc:
        pytest.fail(
            f"{document_path}:{line_number} failed to parse command '{command}' with"
            f" parser exit code {exc.code}."
        )


@pytest.mark.parametrize("command", COMMON_DOC_COMMAND_EXAMPLES)
def test_common_documented_sv_command_examples_parse(command):
    _parse_sv_command(command)


def test_no_documented_removed_command_parses(capsys):
    with pytest.raises(SystemExit):
        parse_sv(["config", "show"])

    assert "invalid choice" in capsys.readouterr().err


def test_readme_links_to_expected_local_docs():
    readme_path = README_PATH
    readme_text = readme_path.read_text(encoding="utf-8")

    linked_targets = set(
        _normalize_markdown_link_target(raw_target)
        for raw_target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", readme_text)
    )

    for doc_key, relative_path in EXPECTED_DOC_LINKS.items():
        assert relative_path in linked_targets, (
            f"README.md is missing a local docs link for '{doc_key}':"
            f" expected '{relative_path}'."
        )

        doc_path = readme_path.parent / relative_path
        assert doc_path.exists(), (
            f"README.md links to '{relative_path}' but file does not exist"
            f" at '{doc_path}'."
        )


def test_top_level_docs_files_have_headings():
    docs_dir = DOCS_DIR
    docs_files = sorted(docs_dir.glob("*.md"))

    missing_headings: list[str] = []
    for doc_path in docs_files:
        lines = doc_path.read_text(encoding="utf-8").splitlines()
        first_non_empty_line = next((line for line in lines if line.strip()), "")
        normalized_first_line = first_non_empty_line.lstrip("\ufeff")
        if not normalized_first_line.startswith("# "):
            missing_headings.append(f"{doc_path.name}: {first_non_empty_line!r}")

    assert not missing_headings, (
        "Expected every docs/*.md file to start with a top-level heading. Missing or"
        f" malformed headings in: {', '.join(missing_headings)}"
    )

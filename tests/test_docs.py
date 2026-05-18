from __future__ import annotations

from pathlib import Path
import re
import shlex
from urllib.parse import urlsplit

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
CHANGELOG_PATH = _PROJECT_ROOT / "CHANGELOG.md"
DOCS_DIR = _PROJECT_ROOT / "docs"
WORKFLOW_PATH = _PROJECT_ROOT / ".github" / "workflows" / "tests.yml"

FULL_RELEASE_COMMANDS = [
    "uv run ruff check .",
    "uv run ty check src tests",
    "uv run pytest --cov=sv --cov-report=term-missing",
    "rm -rf dist",
    "uv build",
    'tmp_venv="$(mktemp -d)"',
    "trap 'rm -rf \"$tmp_venv\"' EXIT",
    'python -m venv "$tmp_venv"',
    'wheel_path="$(python -c \'from pathlib import Path; wheels = sorted(Path("dist").glob("sv-*.whl")); assert len(wheels) == 1, wheels; print(wheels[0])\')"',
    'expected_version="$(python -c \'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])\')"',
    'uv pip install --python "$tmp_venv/bin/python" --link-mode=copy --no-index "$wheel_path"',
    '"$tmp_venv/bin/sv" --help',
    'EXPECTED_SV_VERSION="$expected_version" "$tmp_venv/bin/python" -c \'import os, sv; assert sv.__version__ == os.environ["EXPECTED_SV_VERSION"], sv.__version__\'',
]

COMMON_DOC_COMMAND_EXAMPLES = [
    "sv list",
    "sv add find-docs",
    "sv add repo:skill",
    "sv add -l",
    "sv add --all",
    "sv remove find-docs",
    "sv sync",
    "sv update",
    "sv run -- --model fast",
    "sv repo add HamdiMaz/Skills",
    "sv repo list",
    "sv repo remove HamdiMaz/Skills",
    "sv search find-docs",
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


def _fenced_code_block_after_heading(path: Path, heading: str) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    heading_line = f"## {heading}"

    try:
        heading_index = lines.index(heading_line)
    except ValueError:
        pytest.fail(f"{path.relative_to(_PROJECT_ROOT)} is missing '{heading_line}'.")

    block_start = None
    for index in range(heading_index + 1, len(lines)):
        if lines[index].strip().startswith("```"):
            block_start = index + 1
            break

    if block_start is None:
        pytest.fail(
            f"{path.relative_to(_PROJECT_ROOT)} is missing a fenced code block after"
            f" '{heading_line}'."
        )

    for index in range(block_start, len(lines)):
        if lines[index].strip().startswith("```"):
            return lines[block_start:index]

    pytest.fail(
        f"{path.relative_to(_PROJECT_ROOT)} has an unterminated fenced code block"
        f" after '{heading_line}'."
    )


def _workflow_release_run_commands() -> list[str]:
    release_commands: list[str] = []
    current_step_name = ""
    lines = WORKFLOW_PATH.read_text(encoding="utf-8").splitlines()
    index = 0

    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("- name:"):
            current_step_name = stripped.removeprefix("- name:").strip()
            index += 1
            continue
        if not stripped.startswith("run:"):
            index += 1
            continue

        normalized_name = current_step_name.lower()
        if normalized_name.startswith("set up") or "install" in normalized_name:
            index += 1
            continue

        run_value = stripped.removeprefix("run:").strip()
        if run_value != "|":
            release_commands.append(run_value)
            index += 1
            continue

        index += 1
        while index < len(lines):
            block_line = lines[index]
            block_stripped = block_line.strip()
            if block_stripped.startswith("- name:"):
                break
            if block_stripped:
                release_commands.append(block_stripped)
            index += 1

    return release_commands


def _clean_markdown_link_target(raw_target: str) -> str:
    target = raw_target.strip()
    target = target.split(" ", 1)[0]
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    return target


def _normalize_markdown_link_target(raw_target: str) -> str:
    """Return a local path target normalized for deterministic link checks."""

    return urlsplit(_clean_markdown_link_target(raw_target)).path


def _markdown_link_targets(line: str) -> list[str]:
    targets = re.findall(r"\[[^\]]+\]\(([^)]+)\)", line)
    reference_definition = re.match(r"\s*\[[^\]]+\]:\s+(\S+)", line)
    if reference_definition:
        targets.append(reference_definition.group(1))
    return targets


def _local_markdown_links() -> list[tuple[str, int, str]]:
    links: list[tuple[str, int, str]] = []

    for path in _documented_paths():
        relative_document = str(path.relative_to(_PROJECT_ROOT))
        in_code_block = False
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            for raw_target in _markdown_link_targets(line):
                cleaned_target = _clean_markdown_link_target(raw_target)
                parsed = urlsplit(cleaned_target)
                target = parsed.path
                if not target or cleaned_target.startswith("#"):
                    continue
                if parsed.scheme or parsed.netloc:
                    continue
                links.append((relative_document, line_number, target))

    return links


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


def test_command_reference_documents_search():
    command_reference = (DOCS_DIR / "commands.md").read_text(encoding="utf-8")

    assert "`sv search <query>`" in command_reference
    assert "ranked" in command_reference
    assert "name, description, repo, and source path" in command_reference
    assert "fuzzy matching for skill names, repo IDs, aliases, and source paths" in command_reference
    assert "descriptions match by text substring" in command_reference


def test_command_reference_documents_repo_aliases_and_svx():
    command_reference = (DOCS_DIR / "commands.md").read_text(encoding="utf-8")

    assert "`sv repo -l`" in command_reference
    assert "exact alias for `sv repo list`" in command_reference
    assert "`svx <repo>`" in command_reference
    assert "console script alias for `sv repo add <repo>`" in command_reference
    assert "passes through repeatable `--skills-path` values" in command_reference


def test_command_reference_documents_epic1_commands_and_flags():
    command_reference = (DOCS_DIR / "commands.md").read_text(encoding="utf-8")

    expected_snippets = [
        "`sv init [folder]`",
        "`sv status`",
        "`sv add --all --repo <repo>`",
        "`sv remove --all`",
        "`sv remove --all --yes`",
        "`sv repo add <repo> --skills-path <path>`",
        "`sv repo remove -l`",
    ]

    missing_snippets = [
        snippet for snippet in expected_snippets if snippet not in command_reference
    ]
    assert not missing_snippets, (
        "docs/commands.md is missing Epic 1 command or flag documentation: "
        f"{', '.join(missing_snippets)}"
    )


def test_docs_describe_explicit_first_run_source_configuration():
    stale_default_source_phrases = [
        "Default skill source when no repo config exists",
        "uses the default `HamdiMaz/Skills` repo",
        "default repo is used again",
        "restoring the default repo",
    ]
    stale_locations: list[str] = []
    for path in [*_documented_paths(), CHANGELOG_PATH]:
        text = path.read_text(encoding="utf-8")
        for phrase in stale_default_source_phrases:
            if phrase in text:
                stale_locations.append(f"{path.relative_to(_PROJECT_ROOT)}: {phrase}")

    assert not stale_locations, (
        "Docs should describe explicit first-run source configuration instead of "
        f"implicit defaults: {'; '.join(stale_locations)}"
    )

    readme = README_PATH.read_text(encoding="utf-8")
    assert "No skill source repos configured." in readme
    assert "sv repo add HamdiMaz/Skills" in readme


def test_docs_do_not_describe_removed_add_all_positional_alias():
    stale_locations = [
        str(path.relative_to(_PROJECT_ROOT))
        for path in [*_documented_paths(), CHANGELOG_PATH]
        if "sv add all" in path.read_text(encoding="utf-8")
    ]

    assert not stale_locations, (
        "Docs should describe `sv add --all`, not the removed positional "
        f"`sv add all` behavior. Stale mentions in: {', '.join(stale_locations)}"
    )


def test_docs_describe_sv_jobs_parallelism_control() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in _documented_paths())

    assert "SV_JOBS" in combined
    assert "SV_JOBS=1" in combined
    assert "parallel" in combined.lower()
    assert "64" in combined


def test_docs_distinguish_update_from_force_sync():
    stale_phrases = [
        "`sv update` does the same work",
        "Equivalent to refresh sources plus `sv sync`",
        "`sv sync` and `sv update` both refresh configured source repos before syncing project skills",
        "Both commands refresh configured source repos before syncing managed project skills",
        "sync/update overwrite semantics",
        "`sv update` to refresh configured source repo caches and sync project skills in one command",
    ]
    stale_locations: list[str] = []
    for path in [*_documented_paths(), CHANGELOG_PATH]:
        text = path.read_text(encoding="utf-8")
        for phrase in stale_phrases:
            if phrase in text:
                stale_locations.append(f"{path.relative_to(_PROJECT_ROOT)}: {phrase}")

    assert not stale_locations, (
        "Docs should not describe `sv update` as destructive force-sync: "
        f"{'; '.join(stale_locations)}"
    )

    docs_requiring_safe_update_language = [
        README_PATH,
        DOCS_DIR / "commands.md",
        DOCS_DIR / "usage.md",
        DOCS_DIR / "examples.md",
        DOCS_DIR / "getting-started.md",
    ]
    missing_safe_language = [
        str(path.relative_to(_PROJECT_ROOT))
        for path in docs_requiring_safe_update_language
        if "preserves local edits" not in path.read_text(encoding="utf-8")
    ]
    assert not missing_safe_language, (
        "Docs that mention `sv update` should say it preserves local edits. "
        f"Missing in: {', '.join(missing_safe_language)}"
    )


def test_no_documented_removed_command_parses(capsys):
    with pytest.raises(SystemExit):
        parse_sv(["config", "show"])

    assert "invalid choice" in capsys.readouterr().err


def test_testing_guide_full_release_commands_match_ci():
    documented_commands = [
        line.strip()
        for line in _fenced_code_block_after_heading(
            DOCS_DIR / "testing.md", "Full release verification"
        )
        if line.strip()
    ]
    workflow_release_commands = _workflow_release_run_commands()

    assert documented_commands == FULL_RELEASE_COMMANDS
    assert workflow_release_commands == documented_commands, (
        "Release commands in docs/testing.md should exactly match the GitHub"
        " Actions run-step sequence after setup/install commands. Saw"
        f" {workflow_release_commands!r}."
    )


def test_release_verification_commands_do_not_hardcode_package_version():
    release_text = "\n".join(
        [
            (DOCS_DIR / "testing.md").read_text(encoding="utf-8"),
            WORKFLOW_PATH.read_text(encoding="utf-8"),
        ]
    )

    assert not re.search(r"dist/sv-[^\s\"']+-py3-none-any\.whl", release_text)
    assert not re.search(r"sv\.__version__ == [\"'][^\"']+[\"']", release_text)
    assert "rm -rf dist" in release_text
    assert "assert len(wheels) == 1" in release_text


@pytest.mark.parametrize(
    ("document_path", "line_number", "link_target"),
    _local_markdown_links(),
)
def test_documented_local_markdown_links_exist(
    document_path: str, line_number: int, link_target: str
):
    source_path = _PROJECT_ROOT / document_path
    target_path = (source_path.parent / link_target).resolve()

    assert target_path.is_relative_to(_PROJECT_ROOT), (
        f"{document_path}:{line_number} links outside the project: '{link_target}'."
    )
    assert target_path.is_file(), (
        f"{document_path}:{line_number} links to missing local file "
        f"'{link_target}' resolved as '{target_path}'."
    )


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

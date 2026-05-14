from pathlib import Path
import re


EXPECTED_DOC_LINKS = {
    "getting-started": "docs/getting-started.md",
    "examples": "docs/examples.md",
    "usage": "docs/usage.md",
    "commands": "docs/commands.md",
    "output": "docs/output.md",
    "troubleshooting": "docs/troubleshooting.md",
}


def _normalize_markdown_link_target(raw_target: str) -> str:
    """Return a local path target normalized for deterministic link checks."""

    target = raw_target.strip()
    target = target.split(" ", 1)[0]
    return target.split("#", 1)[0]


def test_readme_links_to_expected_local_docs():
    readme_path = Path(__file__).resolve().parents[1] / "README.md"
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
    docs_dir = Path(__file__).resolve().parents[1] / "docs"
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

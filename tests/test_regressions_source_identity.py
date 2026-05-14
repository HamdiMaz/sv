from pathlib import Path

import pytest

from sv.catalog import build_source_catalog
from sv.config import RepoConfig, SvPaths, derive_repo_id, repo_source_key

EQUIVALENT_GITHUB_REPOS = (
    "HamdiMaz/Skills",
    "https://github.com/HamdiMaz/Skills",
    "https://github.com/HamdiMaz/Skills.git",
    "git@github.com:HamdiMaz/Skills.git",
    "ssh://git@github.com/HamdiMaz/Skills",
)


@pytest.mark.parametrize("repo_url", EQUIVALENT_GITHUB_REPOS)
def test_derive_repo_id_normalizes_equivalent_github_forms(repo_url: str) -> None:
    assert derive_repo_id(repo_url) == "HamdiMaz/Skills"


@pytest.mark.parametrize("repo_url", EQUIVALENT_GITHUB_REPOS)
def test_repo_source_key_stable_for_equivalent_github_forms(repo_url: str) -> None:
    assert repo_source_key(repo_url) == "github:hamdimaz/skills"


def write_source_skill(source_repo: Path, name: str, description: str) -> None:
    skill_dir = source_repo / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"
    )


def test_build_source_catalog_coalesces_equivalent_github_sources_and_records_aliases(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo_ids = (
        "HamdiMaz/Skills",
        "Mirror/Skills",
        "GitHub/Skills",
        "SSH/Skills",
        "Tunnel/Skills",
    )
    repos = tuple(
        RepoConfig(id=repo_id, url=repo_url)
        for repo_id, repo_url in zip(repo_ids, EQUIVALENT_GITHUB_REPOS)
    )

    write_source_skill(paths.source_repo_for("HamdiMaz/Skills"), "alpha", "Alpha skill.")

    catalog = build_source_catalog(repos, paths)

    assert len(catalog) == 1
    assert catalog[0].name == "alpha"
    assert catalog[0].repo_id == "HamdiMaz/Skills"
    assert catalog[0].repo_aliases == repo_ids[1:]
    assert catalog[0].repo_url == EQUIVALENT_GITHUB_REPOS[0]

    # Equivalent sources should not create extra rows.
    assert [(entry.repo_id, entry.name) for entry in catalog] == [
        ("HamdiMaz/Skills", "alpha")
    ]

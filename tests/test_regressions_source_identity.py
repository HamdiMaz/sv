from pathlib import Path

import pytest

from sv.catalog import build_source_catalog, find_qualified_catalog_entry
from sv.config import (
    RepoConfig,
    SvPaths,
    add_repo,
    derive_repo_id,
    load_config,
    repo_source_key,
    remove_repo,
)

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


def test_add_repo_rewrite_preserves_duplicate_source_aliases(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\n'
        'id = "Org/Skills"\n'
        'url = "https://github.com/Org/Skills.git"\n\n'
        '[[repos]]\n'
        'id = "Mirror/Skills"\n'
        'url = "git@github.com:Org/Skills.git"\n'
    )

    add_repo(paths, "SomeOrg/TeamSkills")

    assert load_config(paths).repos == (
        RepoConfig(
            id="Org/Skills",
            url="https://github.com/Org/Skills.git",
            aliases=("Mirror/Skills",),
        ),
        RepoConfig(
            id="SomeOrg/TeamSkills",
            url="https://github.com/SomeOrg/TeamSkills.git",
        ),
    )
    assert 'aliases = ["Mirror/Skills"]' in paths.config_file.read_text()


def test_remove_repo_keeps_duplicate_aliases_for_remaining_sources(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\n'
        'id = "Org/Skills"\n'
        'url = "https://github.com/Org/Skills.git"\n\n'
        '[[repos]]\n'
        'id = "Mirror/Skills"\n'
        'url = "git@github.com:Org/Skills.git"\n\n'
        '[[repos]]\n'
        'id = "SomeOrg/TeamSkills"\n'
        'url = "https://github.com/SomeOrg/TeamSkills.git"\n'
    )

    removed = remove_repo(paths, "SomeOrg/TeamSkills")

    assert removed.id == "SomeOrg/TeamSkills"
    assert load_config(paths).repos == (
        RepoConfig(
            id="Org/Skills",
            url="https://github.com/Org/Skills.git",
            aliases=("Mirror/Skills",),
        ),
    )
    assert 'aliases = ["Mirror/Skills"]' in paths.config_file.read_text()


def test_qualified_catalog_lookup_uses_aliases_from_rewrite(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\n'
        'id = "Org/Skills"\n'
        'url = "https://github.com/Org/Skills"\n\n'
        '[[repos]]\n'
        'id = "Mirror/Skills"\n'
        'url = "git@github.com:Org/Skills.git"\n'
    )

    write_source_skill(paths.source_repo_for("Org/Skills"), "alpha", "Alpha skill.")
    catalog = build_source_catalog(load_config(paths).repos, paths)

    matched = find_qualified_catalog_entry(catalog, "Mirror/Skills:alpha")
    assert matched is not None
    assert matched.repo_id == "Org/Skills"

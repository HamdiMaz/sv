from pathlib import Path

import pytest

from sv.config import (
    DEFAULT_REPO,
    RepoConfig,
    SvConfig,
    SvPaths,
    add_repo,
    derive_repo_id,
    load_config,
    normalize_repo,
    remove_repo,
)


def test_normalize_repo_accepts_github_shorthand():
    assert normalize_repo("HamdiMaz/Skills") == "https://github.com/HamdiMaz/Skills.git"


def test_normalize_repo_keeps_https_url():
    url = "https://github.com/HamdiMaz/Skills.git"
    assert normalize_repo(url) == url


def test_normalize_repo_keeps_ssh_url():
    url = "git@github.com:HamdiMaz/Skills.git"
    assert normalize_repo(url) == url


def test_normalize_repo_rejects_empty_value():
    with pytest.raises(ValueError, match="repo cannot be empty"):
        normalize_repo("   ")


def test_derive_repo_id_uses_owner_repo_for_github_forms():
    assert derive_repo_id("HamdiMaz/Skills") == "HamdiMaz/Skills"
    assert derive_repo_id("https://github.com/HamdiMaz/Skills.git") == "HamdiMaz/Skills"
    assert derive_repo_id("git@github.com:HamdiMaz/Skills.git") == "HamdiMaz/Skills"


def test_derive_repo_id_uses_safe_hashed_id_for_local_paths(tmp_path: Path):
    repo = tmp_path / "skill-source"

    repo_id = derive_repo_id(str(repo))

    assert repo_id.startswith("local-skill-source-")
    assert "/" not in repo_id
    assert " " not in repo_id


def test_paths_include_sources_root_and_per_repo_cache(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)

    assert paths.sv_home == tmp_path / ".sv"
    assert paths.config_file == tmp_path / ".sv" / "config.toml"
    assert paths.sources_dir == tmp_path / ".sv" / "sources"
    assert paths.source_repo_for("HamdiMaz/Skills") == (
        tmp_path / ".sv" / "sources" / "HamdiMaz" / "Skills" / "repo"
    )


def test_load_config_uses_default_repo_when_config_missing(tmp_path: Path):
    config = load_config(SvPaths.from_home(tmp_path))

    assert config == SvConfig(
        repos=(RepoConfig(id="HamdiMaz/Skills", url=DEFAULT_REPO),)
    )


def test_load_config_reads_old_single_repo_config(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text('repo = "https://github.com/SomeOrg/TeamSkills.git"\n')

    config = load_config(paths)

    assert config == SvConfig(
        repos=(
            RepoConfig(
                id="SomeOrg/TeamSkills",
                url="https://github.com/SomeOrg/TeamSkills.git",
            ),
        )
    )


def test_add_repo_writes_multi_repo_config_without_duplicates(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)

    first = add_repo(paths, "HamdiMaz/Skills")
    second = add_repo(paths, "https://github.com/SomeOrg/TeamSkills.git")
    duplicate = add_repo(paths, "HamdiMaz/Skills")

    assert first.status == "added"
    assert second.status == "added"
    assert duplicate.status == "exists"
    assert load_config(paths).repos == (
        RepoConfig(id="HamdiMaz/Skills", url="https://github.com/HamdiMaz/Skills.git"),
        RepoConfig(
            id="SomeOrg/TeamSkills",
            url="https://github.com/SomeOrg/TeamSkills.git",
        ),
    )
    assert paths.config_file.read_text() == (
        '[[repos]]\n'
        'id = "HamdiMaz/Skills"\n'
        'url = "https://github.com/HamdiMaz/Skills.git"\n'
        "\n"
        '[[repos]]\n'
        'id = "SomeOrg/TeamSkills"\n'
        'url = "https://github.com/SomeOrg/TeamSkills.git"\n'
    )


def test_remove_repo_writes_remaining_repos(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    add_repo(paths, "HamdiMaz/Skills")
    add_repo(paths, "SomeOrg/TeamSkills")

    removed = remove_repo(paths, "HamdiMaz/Skills")

    assert removed.id == "HamdiMaz/Skills"
    assert load_config(paths).repos == (
        RepoConfig(
            id="SomeOrg/TeamSkills",
            url="https://github.com/SomeOrg/TeamSkills.git",
        ),
    )


def test_remove_repo_reports_missing_repo(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)

    with pytest.raises(ValueError, match="Repo 'missing/repo' is not configured"):
        remove_repo(paths, "missing/repo")

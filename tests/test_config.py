from pathlib import Path

import pytest

from sv.config import (
    DEFAULT_REPO,
    SvConfig,
    SvPaths,
    load_config,
    normalize_repo,
    save_repo,
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


def test_paths_use_single_sv_home_under_user_home(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)

    assert paths.sv_home == tmp_path / ".sv"
    assert paths.config_file == tmp_path / ".sv" / "config.toml"
    assert paths.source_repo == tmp_path / ".sv" / "sources" / "default" / "repo"


def test_load_config_uses_default_repo_when_config_missing(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)

    config = load_config(paths)

    assert config == SvConfig(repo=DEFAULT_REPO)


def test_save_repo_writes_normalized_repo(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)

    saved = save_repo(paths, "HamdiMaz/Skills")

    assert saved == "https://github.com/HamdiMaz/Skills.git"
    assert (
        paths.config_file.read_text()
        == 'repo = "https://github.com/HamdiMaz/Skills.git"\n'
    )
    assert load_config(paths) == SvConfig(repo="https://github.com/HamdiMaz/Skills.git")

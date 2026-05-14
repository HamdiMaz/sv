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
from sv.errors import SvError


def test_normalize_repo_accepts_github_shorthand():
    assert normalize_repo("HamdiMaz/Skills") == "https://github.com/HamdiMaz/Skills.git"


def test_normalize_repo_keeps_https_url():
    url = "https://github.com/HamdiMaz/Skills.git"
    assert normalize_repo(url) == url


def test_normalize_repo_keeps_ssh_url():
    url = "git@github.com:HamdiMaz/Skills.git"
    assert normalize_repo(url) == url


@pytest.mark.parametrize(
    ("raw_repo", "expected"),
    [
        ("HamdiMaz/Skills", "https://github.com/HamdiMaz/Skills.git"),
        ("https://github.com/HamdiMaz/Skills/", "https://github.com/HamdiMaz/Skills/"),
        ("http://github.com/HamdiMaz/Skills.git", "http://github.com/HamdiMaz/Skills.git"),
        ("git@github.com:HamdiMaz/Skills.git", "git@github.com:HamdiMaz/Skills.git"),
        ("ssh://git@github.com/HamdiMaz/Skills.git", "ssh://git@github.com/HamdiMaz/Skills.git"),
        ("file:///tmp/skill-source", "file:///tmp/skill-source"),
    ],
)
def test_normalize_repo_supported_form_matrix(raw_repo: str, expected: str):
    assert normalize_repo(raw_repo) == expected


@pytest.mark.parametrize(
    ("raw_repo", "create_path"),
    [
        ("skill-source", True),
        ("./skill-source", True),
        ("./missing-skill-source", False),
    ],
)
def test_normalize_repo_local_path_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw_repo: str, create_path: bool
):
    if create_path:
        (tmp_path / raw_repo.lstrip("./")).mkdir()

    monkeypatch.chdir(tmp_path)
    normalized = normalize_repo(raw_repo)
    expected = (tmp_path / raw_repo.lstrip("./")).resolve()

    assert Path(normalized).is_absolute()
    assert normalized == str(expected)


def test_derive_repo_id_uses_owner_repo_for_github_ssh_url():
    assert derive_repo_id("ssh://git@github.com/HamdiMaz/Skills.git") == "HamdiMaz/Skills"


@pytest.mark.parametrize(
    ("raw_repo", "expected"),
    [
        ("HamdiMaz/Skills", "HamdiMaz/Skills"),
        ("https://github.com/HamdiMaz/Skills/", "HamdiMaz/Skills"),
        ("http://github.com/HamdiMaz/Skills.git", "HamdiMaz/Skills"),
        ("git@github.com:HamdiMaz/Skills.git", "HamdiMaz/Skills"),
        ("ssh://git@github.com/HamdiMaz/Skills.git", "HamdiMaz/Skills"),
    ],
)
def test_derive_repo_id_supported_form_matrix(raw_repo: str, expected: str):
    assert derive_repo_id(raw_repo) == expected


@pytest.mark.parametrize(
    ("raw_repo", "create_path", "expected_prefix"),
    [
        ("skill-source", True, "local-skill-source-"),
        ("./missing-skill-source", False, "local-missing-skill-source-"),
        ("file:///tmp/skill-source", False, "local-skill-source-"),
    ],
)
def test_derive_repo_id_file_and_local_path_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_repo: str,
    create_path: bool,
    expected_prefix: str,
):
    if raw_repo != "file:///tmp/skill-source":
        if create_path:
            (tmp_path / raw_repo.lstrip("./")).mkdir()
        monkeypatch.chdir(tmp_path)

    assert derive_repo_id(raw_repo).startswith(expected_prefix)


def test_derive_repo_id_uses_owner_repo_for_github_forms():
    assert derive_repo_id("HamdiMaz/Skills") == "HamdiMaz/Skills"
    assert derive_repo_id("https://github.com/HamdiMaz/Skills.git") == "HamdiMaz/Skills"
    assert derive_repo_id("git@github.com:HamdiMaz/Skills.git") == "HamdiMaz/Skills"
    assert derive_repo_id("ssh://git@github.com/HamdiMaz/Skills.git") == "HamdiMaz/Skills"


def test_derive_repo_id_uses_safe_hashed_id_for_local_paths(tmp_path: Path):
    repo = tmp_path / "skill-source"

    repo_id = derive_repo_id(str(repo))

    assert repo_id.startswith("local-skill-source-")
    assert "/" not in repo_id
    assert " " not in repo_id


def test_normalize_repo_reports_unresolvable_user_home():
    with pytest.raises(ValueError, match="Could not resolve home directory"):
        normalize_repo("~definitely-not-an-sv-user/Skills")


def test_normalize_repo_resolves_relative_local_paths(tmp_path: Path, monkeypatch):
    repo = tmp_path / "skill-source"
    repo.mkdir()
    monkeypatch.chdir(tmp_path)

    assert normalize_repo("skill-source") == str(repo)


def test_normalize_repo_resolves_parent_relative_local_paths(
    tmp_path: Path, monkeypatch
):
    repo = tmp_path / "skill-source"
    repo.mkdir()
    child = tmp_path / "project"
    child.mkdir()
    monkeypatch.chdir(child)

    assert normalize_repo("../skill-source") == str(repo)


def test_normalize_repo_expands_user_local_paths(tmp_path: Path, monkeypatch):
    repo = tmp_path / "skill-source"
    repo.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))

    assert normalize_repo("~/skill-source") == str(repo)


def test_normalize_repo_rejects_empty_value():
    with pytest.raises(ValueError, match="repo cannot be empty"):
        normalize_repo("   ")


@pytest.mark.parametrize(
    ("raw_repo", "match"),
    [
        ("--upload-pack=/tmp/fake", "repo cannot start with '-'"),
        ("https://example.com/skills\trepo.git", "repo cannot contain control characters"),
        ("https://example.com/skills\nrepo.git", "repo cannot contain control characters"),
        ("\x00repo", "repo cannot contain control characters"),
    ],
)
def test_repo_parser_invalid_input_matrix(raw_repo: str, match: str):
    with pytest.raises(ValueError, match=match):
        normalize_repo(raw_repo)

    with pytest.raises(ValueError, match=match):
        derive_repo_id(raw_repo)


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


def test_single_repo_compatibility_property_returns_none_for_empty_config():
    assert SvConfig(repos=()).repo is None


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


def test_load_config_reports_malformed_toml_as_sv_error(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text("repos = [\n")

    with pytest.raises(SvError, match="Failed to read sv config"):
        load_config(paths)


def test_load_config_reports_invalid_repo_entries_as_sv_error(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text('[[repos]]\nid = "Org/Skills"\n')

    with pytest.raises(SvError, match="Invalid sv config"):
        load_config(paths)


def test_load_config_rejects_non_string_repo_value(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text("repo = 123\n")

    with pytest.raises(SvError, match="repo must be a string"):
        load_config(paths)


def test_load_config_rejects_non_string_repo_entry_fields(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\nid = []\nurl = "https://example.com/skills.git"\n'
    )

    with pytest.raises(SvError, match="repo entry 1 field 'id' must be a string"):
        load_config(paths)


def test_load_config_rejects_unsafe_repo_id(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\nid = "../../outside"\nurl = "https://example.com/skills.git"\n'
    )

    with pytest.raises(
        SvError, match="repo entry 1 field 'id' contains unsafe path components"
    ):
        load_config(paths)


def test_add_repo_reports_config_write_failures(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    paths.sv_home.write_text("not a directory\n")

    with pytest.raises(SvError, match="Failed to write sv config"):
        add_repo(paths, "Org/Skills")


def test_add_repo_resolves_parent_relative_local_paths(
    tmp_path: Path, monkeypatch
):
    paths = SvPaths.from_home(tmp_path / "home")
    repo = tmp_path / "Skills"
    repo.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    result = add_repo(paths, "../Skills")
    other_project = tmp_path / "other-project"
    other_project.mkdir()
    monkeypatch.chdir(other_project)

    assert result.repo.url == str(repo)
    assert result.repo.id.startswith("local-Skills-")
    assert f'url = "{repo}"' in paths.config_file.read_text()
    assert load_config(paths).repos == (result.repo,)


def test_add_repo_rejects_unsafe_github_shorthand(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)

    with pytest.raises(SvError, match="repo id contains unsafe path components"):
        add_repo(paths, "Org/..")

    assert not paths.config_file.exists()


def test_load_config_rejects_control_characters_in_repo_id(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\nid = "Org/\\u001bSkills"\nurl = "https://example.com/skills.git"\n'
    )

    with pytest.raises(
        SvError, match="repo entry 1 field 'id' contains unsupported characters"
    ):
        load_config(paths)


def test_load_config_rejects_option_like_repo_entry_url(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\nid = "Org/Skills"\nurl = "--upload-pack=/tmp/fake"\n'
    )

    with pytest.raises(SvError, match="repo cannot start with '-'"):
        load_config(paths)


def test_load_config_coalesces_identical_duplicate_repo_entries(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\nid = "Org/Skills"\nurl = "https://github.com/Org/Skills.git"\n\n'
        '[[repos]]\nid = "Org/Skills"\nurl = "https://github.com/Org/Skills.git"\n'
    )

    assert load_config(paths).repos == (
        RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git"),
    )


def test_load_config_coalesces_duplicate_repo_id_with_equivalent_url(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\nid = "Org/Skills"\nurl = "https://github.com/Org/Skills"\n\n'
        '[[repos]]\nid = "Org/Skills"\nurl = "git@github.com:Org/Skills.git"\n'
    )

    assert load_config(paths).repos == (
        RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills"),
    )


def test_load_config_rejects_duplicate_repo_id_with_different_urls(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\nid = "Org/Skills"\nurl = "https://github.com/Org/Skills.git"\n\n'
        '[[repos]]\nid = "Org/Skills"\nurl = "https://github.com/Other/Skills.git"\n'
    )

    with pytest.raises(SvError, match="listed more than once with different URLs"):
        load_config(paths)


def test_load_config_coalesces_duplicate_repo_urls_with_different_ids(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\nid = "Org/Skills"\nurl = "https://github.com/Org/Skills.git"\n\n'
        '[[repos]]\nid = "Mirror/Skills"\nurl = "https://github.com/Org/Skills.git"\n'
    )

    config = load_config(paths)

    assert config.repos == (
        RepoConfig(
            id="Org/Skills",
            url="https://github.com/Org/Skills.git",
            aliases=("Mirror/Skills",),
        ),
    )


def test_load_config_coalesces_equivalent_github_repo_urls(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\nid = "Org/Skills"\nurl = "https://github.com/Org/Skills"\n\n'
        '[[repos]]\nid = "Mirror/Skills"\nurl = "git@github.com:Org/Skills.git"\n'
    )

    config = load_config(paths)

    assert config.repos == (
        RepoConfig(
            id="Org/Skills",
            url="https://github.com/Org/Skills",
            aliases=("Mirror/Skills",),
        ),
    )


def test_add_repo_preserves_duplicate_source_aliases_when_rewriting_config(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\nid = "Org/Skills"\nurl = "https://github.com/Org/Skills.git"\n\n'
        '[[repos]]\nid = "Mirror/Skills"\nurl = "git@github.com:Org/Skills.git"\n'
    )

    result = add_repo(paths, "SomeOrg/TeamSkills")

    assert result.status == "added"
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


def test_remove_repo_does_not_remove_canonical_repo_by_alias(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text(
        '[[repos]]\nid = "Org/Skills"\nurl = "https://github.com/Org/Skills.git"\n\n'
        '[[repos]]\nid = "Mirror/Skills"\nurl = "git@github.com:Org/Skills.git"\n'
    )

    with pytest.raises(ValueError, match="not configured"):
        remove_repo(paths, "Mirror/Skills")

    assert load_config(paths).repos == (
        RepoConfig(
            id="Org/Skills",
            url="https://github.com/Org/Skills.git",
            aliases=("Mirror/Skills",),
        ),
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
        "[[repos]]\n"
        'id = "HamdiMaz/Skills"\n'
        'url = "https://github.com/HamdiMaz/Skills.git"\n'
        "\n"
        "[[repos]]\n"
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


def test_remove_last_repo_preserves_empty_repo_config(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    add_repo(paths, "HamdiMaz/Skills")

    removed = remove_repo(paths, "HamdiMaz/Skills")

    assert removed.id == "HamdiMaz/Skills"
    assert load_config(paths).repos == ()
    assert paths.config_file.read_text() == "repos = []\n"


def test_remove_repo_reports_missing_repo(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)

    with pytest.raises(ValueError, match="Repo 'missing/repo' is not configured"):
        remove_repo(paths, "missing/repo")

import pytest

from pathlib import Path

from sv.config import SvPaths, load_config
from sv.index import load_index
from sv.manifest import load_manifest
from sv.source import default_runner
from tests.helpers import (
    assert_no_traceback,
    make_source_repo,
    run_git,
    write_source_skill,
)


pytestmark = pytest.mark.integration


def _assert_success(result) -> None:
    assert result.exit_code == 0
    assert_no_traceback(result.stdout)
    assert_no_traceback(result.stderr)
    assert result.stderr == ""


def _write_skill_at(
    repo: Path, relative_dir: str, name: str, description: str, body: str
) -> None:
    skill_dir = repo / relative_dir
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    (skill_dir / "notes.md").write_text(body, encoding="utf-8")


def _init_git_source(repo: Path, message: str = "initial skills") -> None:
    run_git(["init"], repo)
    run_git(["config", "user.email", "tests@example.com"], repo)
    run_git(["config", "user.name", "sv tests"], repo)
    run_git(["add", "."], repo)
    run_git(["commit", "-m", message], repo)


def test_local_git_user_journey_smoke(tmp_path, run_sv):
    source = make_source_repo(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    repo_add = run_sv(
        ["repo", "add", str(source)],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    _assert_success(repo_add)
    assert "Added repo" in repo_add.stdout
    assert str(source) in repo_add.stdout
    configured_repo = load_config(SvPaths.from_home(home)).repos[0]

    repo_list = run_sv(["repo", "list"], cwd=project, home=home)

    _assert_success(repo_list)
    assert configured_repo.id in repo_list.stdout
    assert "URL" in repo_list.stdout
    assert "Cache" in repo_list.stdout

    list_result = run_sv(
        ["list"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    _assert_success(list_result)
    assert "alpha" in list_result.stdout
    assert "Alpha skill." in list_result.stdout
    assert "beta" in list_result.stdout
    assert "Beta skill." in list_result.stdout

    add_result = run_sv(
        ["add", "alpha"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    _assert_success(add_result)
    assert "Added Pi skill 'alpha'" in add_result.stdout
    alpha_notes = project / ".pi" / "skills" / "alpha" / "notes.md"
    assert alpha_notes.read_text() == "alpha v1\n"
    assert not (project / ".pi" / "skills" / "beta").exists()

    write_source_skill(source, "alpha", "Alpha skill.", "alpha v2\n")
    run_git(["add", "skills/alpha"], source)
    run_git(["commit", "-m", "update alpha"], source)

    sync_result = run_sv(
        ["sync"],
        cwd=project,
        home=home,
        git_runner=default_runner,
    )

    _assert_success(sync_result)
    assert "Synced Pi skill 'alpha'." in sync_result.stdout
    assert alpha_notes.read_text() == "alpha v2\n"

    remove_result = run_sv(["remove", "alpha"], cwd=project, home=home)

    _assert_success(remove_result)
    assert "Removed Pi skill 'alpha'" in remove_result.stdout
    assert not (project / ".pi" / "skills" / "alpha").exists()

    process_calls: list[list[str]] = []

    def process_runner(command: list[str]) -> int:
        process_calls.append(command)
        return 0

    run_result = run_sv(
        ["run", "--", "--help"],
        cwd=project,
        home=home,
        process_runner=process_runner,
    )

    _assert_success(run_result)
    assert process_calls == [["pi", "--no-skills", "--skill", ".pi/skills", "--help"]]


def test_epic1_source_discovery_and_materialization_acceptance(tmp_path, run_sv):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    indexed_source = tmp_path / "indexed-source"
    _write_skill_at(
        indexed_source,
        "packages/agents/pi/skills/indexed-deep",
        "indexed-deep",
        "Indexed deep skill.",
        "indexed materialized\n",
    )
    _init_git_source(indexed_source)
    index_result = run_sv(
        ["index"], cwd=indexed_source, home=home, git_runner=default_runner
    )
    _assert_success(index_result)
    run_git(["add", ".sv/index.toml"], indexed_source)
    run_git(["commit", "-m", "publish sv index"], indexed_source)

    generic_source = tmp_path / "generic-source"
    _write_skill_at(
        generic_source,
        "skills/root-fallback",
        "root-fallback",
        "Root fallback skill.",
        "root materialized\n",
    )
    _write_skill_at(
        generic_source,
        "workspace/skills/workspace-fallback",
        "workspace-fallback",
        "Workspace fallback skill.",
        "workspace materialized\n",
    )
    _write_skill_at(
        generic_source,
        "packages/agents/pi/skills/hidden-deep",
        "hidden-deep",
        "Hidden deep skill.",
        "hidden materialized\n",
    )
    _init_git_source(generic_source)

    explicit_source = tmp_path / "explicit-source"
    _write_skill_at(
        explicit_source,
        "packages/agents/pi/skills/explicit-deep",
        "explicit-deep",
        "Explicit deep skill.",
        "explicit materialized\n",
    )
    _init_git_source(explicit_source)

    for args in (
        ["repo", "add", str(indexed_source)],
        ["repo", "add", str(generic_source)],
        [
            "repo",
            "add",
            str(explicit_source),
            "--skills-path",
            "packages/agents/pi/skills",
        ],
    ):
        result = run_sv(args, cwd=project, home=home, git_runner=default_runner)
        _assert_success(result)

    list_result = run_sv(
        ["list"], cwd=project, home=home, git_runner=default_runner
    )
    _assert_success(list_result)
    assert "indexed-deep" in list_result.stdout
    assert "root-fallback" in list_result.stdout
    assert "workspace-fallback" in list_result.stdout
    assert "explicit-deep" in list_result.stdout
    assert "hidden-deep" not in list_result.stdout

    search_result = run_sv(
        ["search", "agents/pi"], cwd=project, home=home, git_runner=default_runner
    )
    _assert_success(search_result)
    assert "indexed-deep" in search_result.stdout
    assert "explicit-deep" in search_result.stdout
    assert "hidden-deep" not in search_result.stdout
    assert "Rank" in search_result.stdout

    for skill in (
        "indexed-deep",
        "explicit-deep",
        "root-fallback",
        "workspace-fallback",
    ):
        add_result = run_sv(
            ["add", skill], cwd=project, home=home, git_runner=default_runner
        )
        _assert_success(add_result)
        assert f"Added Pi skill '{skill}'" in add_result.stdout

    assert (
        project / ".pi" / "skills" / "indexed-deep" / "notes.md"
    ).read_text() == "indexed materialized\n"
    assert (
        project / ".pi" / "skills" / "explicit-deep" / "notes.md"
    ).read_text() == "explicit materialized\n"
    assert (
        project / ".pi" / "skills" / "root-fallback" / "notes.md"
    ).read_text() == "root materialized\n"
    assert (
        project / ".pi" / "skills" / "workspace-fallback" / "notes.md"
    ).read_text() == "workspace materialized\n"
    assert not (project / ".pi" / "skills" / "hidden-deep").exists()
    assert (project / ".sv" / "manifest.toml").is_file()
    assert not (project / ".sv" / "index.toml").exists()
    assert not (project / ".pi" / "skills" / ".sv-manifest.toml").exists()

    manifest = load_manifest(project / ".pi" / "skills")
    assert sorted(manifest) == [
        "explicit-deep",
        "indexed-deep",
        "root-fallback",
        "workspace-fallback",
    ]
    assert manifest["indexed-deep"].source_path == (
        "packages/agents/pi/skills/indexed-deep"
    )
    assert manifest["explicit-deep"].source_path == (
        "packages/agents/pi/skills/explicit-deep"
    )
    assert manifest["root-fallback"].source_path == "skills/root-fallback"
    assert manifest["workspace-fallback"].source_path == (
        "workspace/skills/workspace-fallback"
    )
    assert {entry.target_kind for entry in manifest.values()} == {"project-agent"}
    assert {entry.target_agent for entry in manifest.values()} == {"pi"}

    paths = SvPaths.from_home(home)
    repos = load_config(paths).repos
    cache_by_id = {repo.id: paths.source_repo_for(repo.id) for repo in repos}
    assert not (
        cache_by_id[repos[0].id]
        / "packages/agents/pi/skills/indexed-deep/notes.md"
    ).exists()
    assert not (
        cache_by_id[repos[1].id]
        / "skills/root-fallback/notes.md"
    ).exists()
    assert not (
        cache_by_id[repos[1].id]
        / "workspace/skills/workspace-fallback/notes.md"
    ).exists()
    assert not (
        cache_by_id[repos[2].id]
        / "packages/agents/pi/skills/explicit-deep/notes.md"
    ).exists()


def test_epic1_vault_state_update_sync_orphan_status_acceptance(tmp_path, run_sv):
    source = make_source_repo(tmp_path, "state-source")
    write_source_skill(source, "gamma", "Gamma skill.", "gamma v1\n")
    run_git(["add", "skills/gamma"], source)
    run_git(["commit", "-m", "add gamma"], source)

    home = tmp_path / "home"
    vault = tmp_path / "vault"
    init_result = run_sv(
        ["init", str(vault)], cwd=tmp_path, home=home, git_runner=default_runner
    )
    _assert_success(init_result)
    assert (vault / ".git").exists()
    assert (vault / "skills").is_dir()
    assert load_index(vault / ".sv" / "index.toml").kind == "skill-vault"
    assert (vault / ".sv" / "manifest.toml").is_file()

    readme = vault / "README.md"
    readme.write_text(
        "# Team Vault\n\n"
        "<!-- sv:skills:start -->\n"
        "outdated generated content\n"
        "<!-- sv:skills:end -->\n\n"
        "Do not touch this footer.\n",
        encoding="utf-8",
    )

    repo_add = run_sv(
        ["repo", "add", str(source)], cwd=vault, home=home, git_runner=default_runner
    )
    _assert_success(repo_add)
    for skill in ("alpha", "beta", "gamma"):
        add_result = run_sv(
            ["add", skill], cwd=vault, home=home, git_runner=default_runner
        )
        _assert_success(add_result)
        assert f"Added vault skill '{skill}'" in add_result.stdout

    assert not (vault / ".pi").exists()
    assert (vault / "skills" / "alpha" / "notes.md").read_text() == "alpha v1\n"
    vault_manifest = load_manifest(vault / "skills")
    assert sorted(vault_manifest) == ["alpha", "beta", "gamma"]
    assert {entry.target_kind for entry in vault_manifest.values()} == {"skill-vault"}
    assert {entry.target_agent for entry in vault_manifest.values()} == {None}
    assert "outdated generated content" not in readme.read_text(encoding="utf-8")
    readme_text = readme.read_text(encoding="utf-8")
    assert "# Team Vault" in readme_text
    assert "| alpha | Alpha skill. |" in readme_text
    assert "| beta | Beta skill. |" in readme_text
    assert "| gamma | Gamma skill. |" in readme_text
    assert "Do not touch this footer." in readme_text

    (vault / "skills" / "alpha" / "notes.md").write_text(
        "alpha local edit\n", encoding="utf-8"
    )
    write_source_skill(source, "beta", "Beta skill.", "beta v2\n")
    run_git(["rm", "-r", "skills/gamma"], source)
    run_git(["add", "skills/beta"], source)
    run_git(["commit", "-m", "update beta and remove gamma"], source)

    status_result = run_sv(
        ["status", "--refresh"], cwd=vault, home=home, git_runner=default_runner
    )
    _assert_success(status_result)
    assert "Skill-vault status" in status_result.stdout
    status_lines = status_result.stdout.splitlines()
    assert any("alpha" in line and "modified" in line for line in status_lines)
    assert any("beta" in line and "update available" in line for line in status_lines)
    assert any("gamma" in line and "orphan" in line for line in status_lines)

    update_result = run_sv(
        ["update"], cwd=vault, home=home, git_runner=default_runner
    )
    _assert_success(update_result)
    assert (vault / "skills" / "alpha" / "notes.md").read_text() == (
        "alpha local edit\n"
    )
    assert (vault / "skills" / "beta" / "notes.md").read_text() == "beta v2\n"
    assert (vault / "skills" / "gamma" / "notes.md").read_text() == "gamma v1\n"
    vault_manifest = load_manifest(vault / "skills")
    assert vault_manifest["alpha"].modified is True
    assert vault_manifest["beta"].update_available is False
    assert vault_manifest["gamma"].orphan is True

    sync_result = run_sv(
        ["sync"], cwd=vault, home=home, git_runner=default_runner
    )
    _assert_success(sync_result)
    assert (vault / "skills" / "alpha" / "notes.md").read_text() == "alpha v1\n"
    assert load_manifest(vault / "skills")["gamma"].orphan is True

    write_source_skill(source, "gamma", "Gamma skill.", "gamma v2\n")
    run_git(["add", "skills/gamma"], source)
    run_git(["commit", "-m", "restore gamma"], source)
    reattach_result = run_sv(
        ["sync"], cwd=vault, home=home, git_runner=default_runner
    )
    _assert_success(reattach_result)
    assert (vault / "skills" / "gamma" / "notes.md").read_text() == "gamma v2\n"
    assert load_manifest(vault / "skills")["gamma"].orphan is False

    manual = vault / "skills" / "manual"
    manual.mkdir()
    (manual / "SKILL.md").write_text(
        "---\nname: manual\ndescription: Manual skill.\n---\n", encoding="utf-8"
    )
    (manual / "notes.md").write_text("manual stays\n", encoding="utf-8")

    remove_all = run_sv(["remove", "--all", "--yes"], cwd=vault, home=home)
    _assert_success(remove_all)
    assert not (vault / "skills" / "alpha").exists()
    assert not (vault / "skills" / "beta").exists()
    assert not (vault / "skills" / "gamma").exists()
    assert (manual / "notes.md").read_text(encoding="utf-8") == "manual stays\n"
    assert load_manifest(vault / "skills") == {}
    refreshed_index = load_index(vault / ".sv" / "index.toml")
    indexed_names = {entry.name for entry in refreshed_index.skills}
    assert indexed_names == {"manual"}
    refreshed_readme = readme.read_text(encoding="utf-8")
    assert "| manual | Manual skill. |" in refreshed_readme
    assert "| alpha |" not in refreshed_readme
    assert "| beta |" not in refreshed_readme
    assert "| gamma |" not in refreshed_readme

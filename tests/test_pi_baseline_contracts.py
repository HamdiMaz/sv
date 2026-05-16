from pathlib import Path

from sv.agents import PiAdapter
from sv.catalog import SourceSkill, build_source_catalog, find_catalog_matches, find_qualified_catalog_entry
from sv.config import RepoConfig, SvPaths
from sv.hashing import sha256_file, sha256_skill_directory
from sv.manifest import ManifestEntry, load_manifest
from sv.project import add_project_skill, remove_project_skill, sync_project_skills


def _write_source_skill(
    source_root: Path,
    name: str,
    *,
    repo_id: str,
    description: str | None = None,
    notes: str,
) -> SourceSkill:
    skill_dir = source_root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_description = description or f"{name.title()} skill."
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {skill_description}\n---\n\n# {name}\n"
    )
    (skill_dir / "notes.md").write_text(notes)
    return SourceSkill(
        name=name,
        description=skill_description,
        repo_id=repo_id,
        repo_url=f"https://github.com/{repo_id}.git",
        repo_path=source_root,
        source_path=skill_dir,
    )


def _manifest_entry(entry: SourceSkill) -> ManifestEntry:
    content_hash = sha256_skill_directory(entry.source_path)
    return ManifestEntry(
        name=entry.name,
        repo_id=entry.repo_id,
        repo_url=entry.repo_url,
        source_path=entry.source_relative_path,
        description=entry.description,
        source_backend=entry.source_backend,
        source_content_hash=content_hash,
        source_skill_file_hash=sha256_file(entry.source_path / "SKILL.md"),
        installed_content_hash=content_hash,
        local_content_hash=content_hash,
    )


def test_pi_project_add_and_remove_contract_targets_project_pi_skills_dir(tmp_path: Path):
    adapter = PiAdapter()
    project = tmp_path / "project"
    project_skills = adapter.project_skill_dir(project)
    entry = _write_source_skill(
        tmp_path / "source",
        "alpha",
        repo_id="Org/Skills",
        notes="alpha from source\n",
    )

    add_result = add_project_skill(entry, project_skills)

    target = project / ".pi" / "skills" / "alpha"
    assert project_skills == project / ".pi" / "skills"
    assert add_result.status == "added"
    assert add_result.target == target
    assert (target / "notes.md").read_text() == "alpha from source\n"
    manifest = load_manifest(project_skills)
    assert manifest["alpha"] == _manifest_entry(entry)

    remove_result = remove_project_skill("alpha", project_skills)

    assert remove_result.target == target
    assert not target.exists()
    assert load_manifest(project_skills) == {}


def test_sync_contract_uses_legacy_pi_manifest_origin_for_duplicate_sources(
    tmp_path: Path,
):
    source_a = _write_source_skill(
        tmp_path / "source-a",
        "alpha",
        repo_id="Org/A",
        description="Alpha from A.",
        notes="alpha from a\n",
    )
    source_b = _write_source_skill(
        tmp_path / "source-b",
        "alpha",
        repo_id="Org/B",
        description="Alpha from B.",
        notes="alpha from b\n",
    )
    source_c = _write_source_skill(
        tmp_path / "source-c",
        "alpha",
        repo_id="Org/C",
        description="Alpha from C.",
        notes="alpha from c\n",
    )
    project_skills = tmp_path / "project" / ".pi" / "skills"
    local = project_skills / "alpha"
    local.mkdir(parents=True)
    (local / "notes.md").write_text("local alpha\n")
    legacy_manifest = project_skills / ".sv-manifest.toml"
    legacy_manifest.write_text(
        "[[skills]]\n"
        'name = "alpha"\n'
        'repo_id = "Org/B"\n'
        'repo_url = "https://github.com/Org/B.git"\n'
        'source_path = "skills/alpha"\n'
        'description = "Alpha from B."\n'
    )

    result = sync_project_skills([source_a, source_b, source_c], project_skills)

    assert result.no_skills_dir is False
    assert result.updated == ["alpha"]
    assert result.backfilled == []
    assert result.skipped == []
    assert (local / "notes.md").read_text() == "alpha from b\n"
    assert load_manifest(project_skills)["alpha"] == _manifest_entry(source_b)


def test_source_catalog_contract_scans_top_level_skills_and_keeps_repo_duplicates(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path / "home")
    repo_a = RepoConfig(id="Org/A", url="https://github.com/Org/A.git")
    repo_b = RepoConfig(id="Org/B", url="https://github.com/Org/B.git")
    repo_a_path = paths.source_repo_for(repo_a.id)
    repo_b_path = paths.source_repo_for(repo_b.id)
    _write_source_skill(
        repo_a_path,
        "alpha",
        repo_id=repo_a.id,
        description="Alpha from A.",
        notes="alpha a\n",
    )
    _write_source_skill(
        repo_b_path,
        "alpha",
        repo_id=repo_b.id,
        description="Alpha from B.",
        notes="alpha b\n",
    )
    deep_skill = repo_a_path / "packages" / "pi" / "skills" / "deep"
    deep_skill.mkdir(parents=True)
    (deep_skill / "SKILL.md").write_text(
        "---\nname: deep\ndescription: Deep skill.\n---\n"
    )
    pi_project_skill = repo_a_path / ".pi" / "skills" / "project-local"
    pi_project_skill.mkdir(parents=True)
    (pi_project_skill / "SKILL.md").write_text(
        "---\nname: project-local\ndescription: Project-local skill.\n---\n"
    )

    catalog = build_source_catalog([repo_a, repo_b], paths)

    assert [(entry.name, entry.repo_id) for entry in catalog] == [
        ("alpha", "Org/A"),
        ("alpha", "Org/B"),
    ]
    assert [entry.source_relative_path for entry in catalog] == [
        "skills/alpha",
        "skills/alpha",
    ]
    assert find_catalog_matches(catalog, "alpha") == catalog
    assert find_qualified_catalog_entry(catalog, "Org/B:alpha") == catalog[1]

from pathlib import Path

from sv.catalog import SourceSkill, build_source_catalog
from sv.config import RepoConfig, SvPaths


def make_skill(repo_path: Path, name: str, description: str) -> None:
    skill_dir = repo_path / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"
    )


def test_build_source_catalog_returns_valid_skills_with_repo_context(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    make_skill(repo_path, "beta", "Beta skill.")
    make_skill(repo_path, "alpha", "Alpha skill.")

    catalog = build_source_catalog([repo], paths)

    assert catalog == [
        SourceSkill(
            name="alpha",
            description="Alpha skill.",
            repo_id="Org/Skills",
            repo_url="https://github.com/Org/Skills.git",
            repo_path=repo_path,
            source_path=repo_path / "skills" / "alpha",
        ),
        SourceSkill(
            name="beta",
            description="Beta skill.",
            repo_id="Org/Skills",
            repo_url="https://github.com/Org/Skills.git",
            repo_path=repo_path,
            source_path=repo_path / "skills" / "beta",
        ),
    ]


def test_build_source_catalog_sorts_by_skill_name_then_repo_id(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    alpha = RepoConfig(id="A/Skills", url="https://github.com/A/Skills.git")
    beta = RepoConfig(id="B/Skills", url="https://github.com/B/Skills.git")
    make_skill(paths.source_repo_for(beta.id), "same", "B skill.")
    make_skill(paths.source_repo_for(alpha.id), "same", "A skill.")

    catalog = build_source_catalog([beta, alpha], paths)

    assert [(entry.name, entry.repo_id) for entry in catalog] == [
        ("same", "A/Skills"),
        ("same", "B/Skills"),
    ]


def test_build_source_catalog_skips_invalid_skills(tmp_path: Path):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    repo_path = paths.source_repo_for(repo.id)
    make_skill(repo_path, "valid", "Valid skill.")
    invalid = repo_path / "skills" / "invalid"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text("---\nname: other\ndescription: Bad.\n---\n")

    catalog = build_source_catalog([repo], paths)

    assert [entry.name for entry in catalog] == ["valid"]

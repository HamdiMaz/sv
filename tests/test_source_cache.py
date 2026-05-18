from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import os

import pytest

from sv.catalog import SourceSkill  # noqa: F401
from sv.config import RepoConfig, SvPaths
from sv.errors import SvError
from sv.source_cache import (
    CacheMode,
    CachePolicy,
    CachedCatalogEntry,
    CachedCatalogDocument,
    DEFAULT_METADATA_TTL_SECONDS,
    DEFAULT_SKILL_BODY_MAX_BYTES,
    DEFAULT_SKILL_BODY_MAX_UNUSED_SECONDS,
    catalog_cache_path,
    cached_catalog_is_fresh,
    load_cached_catalog,
    save_cached_catalog,
    _cached_catalog_hash,
)


def test_sv_paths_expose_global_cache_paths(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)

    assert paths.cache_dir == tmp_path / ".sv" / "cache" / "v1"
    assert paths.catalog_cache_dir == paths.cache_dir / "catalog"
    assert paths.skill_body_cache_dir == paths.cache_dir / "skills"
    assert paths.cache_tmp_dir == paths.cache_dir / "tmp"
    assert paths.cache_prune_marker == paths.cache_dir / "last-prune.toml"


def test_default_cache_policy_matches_product_defaults() -> None:
    policy = CachePolicy.default()

    assert policy.mode is CacheMode.NORMAL
    assert policy.metadata_ttl_seconds == DEFAULT_METADATA_TTL_SECONDS
    assert policy.metadata_ttl_seconds == 24 * 60 * 60
    assert policy.allow_stale_on_error is True
    assert DEFAULT_SKILL_BODY_MAX_UNUSED_SECONDS == 30 * 24 * 60 * 60
    assert DEFAULT_SKILL_BODY_MAX_BYTES == 256 * 1024 * 1024


def _repo() -> RepoConfig:
    return RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")


def _catalog_document(refreshed_at: str) -> CachedCatalogDocument:
    document = CachedCatalogDocument(
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        source_key="github:org/skills",
        skills_paths=(),
        backend="github-https-api",
        refreshed_at=refreshed_at,
        catalog_hash="sha256:" + ("0" * 64),
        index_hash="sha256:a51a6c19a1ffc7416827e89adf20749d23ad42452c396cf7e627409f2896922c",
        entries=(
            CachedCatalogEntry(
                name="find-docs",
                description="Find documentation.",
                source_path="skills/find-docs",
                content_hash="sha256:25bf8e1a2393f1108d37029b3df5593236c755742ec93465bbafa9b290bddcf6",
                skill_file_hash="sha256:c651ccb96b0c0e490de4cc12b9b46d643e6dba87840fab27e2c8d4d5cc2037fa",
            ),
        ),
    )
    return replace(document, catalog_hash=_cached_catalog_hash(document))


def test_catalog_cache_round_trips_metadata(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    document = _catalog_document("2026-05-18T12:00:00Z")

    save_cached_catalog(paths, _repo(), document)

    assert catalog_cache_path(paths, _repo()).is_file()
    assert load_cached_catalog(paths, _repo()) == document


def test_cached_catalog_freshness_uses_ttl() -> None:
    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    fresh = _catalog_document("2026-05-18T11:30:00Z")
    stale = _catalog_document("2026-05-17T11:00:00Z")
    future = _catalog_document("2026-05-18T12:05:00Z")

    assert cached_catalog_is_fresh(fresh, now=now, ttl_seconds=24 * 60 * 60) is True
    assert cached_catalog_is_fresh(stale, now=now, ttl_seconds=24 * 60 * 60) is False
    assert cached_catalog_is_fresh(future, now=now, ttl_seconds=24 * 60 * 60) is False


def test_catalog_cache_write_refuses_symlinked_temp_file(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    path = catalog_cache_path(paths, repo)
    path.parent.mkdir(parents=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    target = tmp_path / "attacker-target.toml"
    temp_path.symlink_to(target)

    with pytest.raises(SvError, match="symlinked"):
        save_cached_catalog(paths, repo, _catalog_document("2026-05-18T12:00:00Z"))

    assert not target.exists()


def test_catalog_cache_load_rejects_catalog_hash_mismatch(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    document = _catalog_document("2026-05-18T12:00:00Z")
    save_cached_catalog(paths, repo, document)
    path = catalog_cache_path(paths, repo)
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            f'catalog_hash = "{document.catalog_hash}"',
            'catalog_hash = "sha256:41cf6794ba4200b839c53531555f0f3998df4cbb01a4d5cb0b94e3ca5e23947d"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(SvError, match="catalog_hash does not match document"):
        load_cached_catalog(paths, repo)


def test_catalog_cache_load_rejects_trust_field_tampering(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    save_cached_catalog(paths, repo, _catalog_document("2026-05-18T12:00:00Z"))
    path = catalog_cache_path(paths, repo)
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            'refreshed_at = "2026-05-18T12:00:00Z"',
            'refreshed_at = "2026-05-18T12:30:00Z"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(SvError, match="catalog_hash does not match document"):
        load_cached_catalog(paths, repo)


def test_catalog_cache_load_rejects_provenance_field_tampering(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    document = _catalog_document("2026-05-18T12:00:00Z")
    save_cached_catalog(paths, repo, document)
    path = catalog_cache_path(paths, repo)
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            f'index_hash = "{document.index_hash}"',
            'index_hash = "sha256:41cf6794ba4200b839c53531555f0f3998df4cbb01a4d5cb0b94e3ca5e23947d"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(SvError, match="catalog_hash does not match document"):
        load_cached_catalog(paths, repo)

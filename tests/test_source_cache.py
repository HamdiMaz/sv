from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import os
import stat
import time

import pytest

import sv.source_cache as source_cache
from sv.catalog import SourceSkill
from sv.config import RepoConfig, SvPaths
from sv.errors import SvError
from sv.hashing import sha256_file, sha256_skill_directory
from sv.source import FakeSourceBackend, SourceBackendError
from sv.source_cache import (
    attach_source_materializers,
    cache_summary,
    clean_cache,
    CacheMode,
    CachePolicy,
    CacheRefreshResult,
    CachedCatalogEntry,
    CachedCatalogDocument,
    DEFAULT_METADATA_TTL_SECONDS,
    DEFAULT_SKILL_BODY_MAX_BYTES,
    DEFAULT_SKILL_BODY_MAX_UNUSED_SECONDS,
    catalog_cache_path,
    cached_catalog_is_fresh,
    load_cached_catalog,
    save_cached_catalog,
    get_catalog_with_cache,
    load_skill_body_metadata,
    prune_skill_body_cache,
    skill_body_cache_path,
    store_skill_body_cache,
    try_materialize_from_skill_body_cache,
    record_cached_skill_body_hash,
    wrap_catalog_with_skill_body_cache,
    _cached_catalog_hash,
    _utc_timestamp,
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


def test_cache_summary_counts_catalogs_and_skill_bodies(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    save_cached_catalog(paths, _repo(), _catalog_document("2026-05-18T12:00:00Z"))
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
    )

    summary = cache_summary(paths, now=datetime(2026, 5, 18, 13, 0, tzinfo=UTC))
    cleaned = clean_cache(paths, now=datetime(2026, 5, 18, 13, 0, tzinfo=UTC))

    assert summary.catalog_files == 1
    assert summary.skill_bodies == 1
    assert summary.skill_body_bytes > 0
    assert cleaned == summary


def test_clean_cache_refuses_symlinked_catalog_cache_before_pruning(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    old_skill = _write_skill_tree(tmp_path / "old-source", "old", "old body\n")
    old_hash = sha256_skill_directory(old_skill, expected_name="old")
    store_skill_body_cache(
        paths,
        old_skill,
        skill_name="old",
        content_hash=old_hash,
        source_reference="Org/Skills:skills/old",
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    attacker_catalog = tmp_path / "attacker-catalog"
    attacker_catalog.mkdir()
    paths.catalog_cache_dir.symlink_to(attacker_catalog, target_is_directory=True)

    with pytest.raises(SvError, match="symlinked"):
        clean_cache(paths, now=datetime(2026, 5, 18, 13, 0, tzinfo=UTC))

    assert skill_body_cache_path(paths, old_hash).exists()


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


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _write_skill_tree(root: Path, name: str, body: str = "body\n") -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Cached skill.\n---\n",
        encoding="utf-8",
    )
    (skill / "notes.md").write_text(body, encoding="utf-8")
    return skill


def _source_skill(repo: RepoConfig, paths: SvPaths, name: str) -> SourceSkill:
    return SourceSkill(
        name=name,
        description=f"{name} skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=paths.source_repo_for(repo.id),
        source_path=paths.source_repo_for(repo.id) / "skills" / name,
        source_relative_path=f"skills/{name}",
        source_backend="fake",
        source_content_hash="sha256:41cf6794ba4200b839c53531555f0f3998df4cbb01a4d5cb0b94e3ca5e23947d",
        source_skill_file_hash="sha256:c651ccb96b0c0e490de4cc12b9b46d643e6dba87840fab27e2c8d4d5cc2037fa",
    )


def test_utc_timestamp_treats_naive_datetime_as_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset is unavailable on this platform")
    original_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()

    try:
        timestamp = _utc_timestamp(datetime(2026, 5, 18, 12, 34, 56))
    finally:
        if original_tz is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", original_tz)
        time.tzset()

    assert timestamp == "2026-05-18T12:34:56Z"


def test_catalog_cache_round_trips_metadata(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    document = _catalog_document("2026-05-18T12:00:00Z")

    save_cached_catalog(paths, _repo(), document)

    assert catalog_cache_path(paths, _repo()).is_file()
    assert load_cached_catalog(paths, _repo()) == document


def test_catalog_cache_round_trips_empty_catalog(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    document_without_hash = CachedCatalogDocument(
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        source_key="github:org/skills",
        skills_paths=(),
        backend="github-https-api",
        refreshed_at="2026-05-18T12:00:00Z",
        catalog_hash="sha256:" + ("0" * 64),
        index_hash="sha256:a51a6c19a1ffc7416827e89adf20749d23ad42452c396cf7e627409f2896922c",
        entries=(),
    )
    document = replace(
        document_without_hash,
        catalog_hash=_cached_catalog_hash(document_without_hash),
    )

    save_cached_catalog(paths, _repo(), document)

    assert load_cached_catalog(paths, _repo()) == document


def test_catalog_cache_save_makes_cache_directory_chain_owner_private(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    document = _catalog_document("2026-05-18T12:00:00Z")
    cache_root = paths.sv_home / "cache"
    cache_root.mkdir(parents=True)
    cache_root.chmod(0o777)

    save_cached_catalog(paths, _repo(), document)

    assert _mode(cache_root) == 0o700
    assert _mode(paths.cache_dir) == 0o700
    assert _mode(paths.catalog_cache_dir) == 0o700


def test_catalog_cache_save_creates_cache_directories_without_parents_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SvPaths.from_home(tmp_path)
    document = _catalog_document("2026-05-18T12:00:00Z")
    original_mkdir = Path.mkdir
    mkdir_calls: list[tuple[Path, int, bool]] = []
    expected_cache_dirs = (
        paths.sv_home / "cache",
        paths.cache_dir,
        paths.catalog_cache_dir,
    )

    def guarded_mkdir(
        self: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        if self in expected_cache_dirs:
            mkdir_calls.append((self, mode, parents))
            assert not parents, f"cache directory {self} used parents=True"
        original_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", guarded_mkdir)

    save_cached_catalog(paths, _repo(), document)

    assert mkdir_calls == [(path, 0o700, False) for path in expected_cache_dirs]
    assert [_mode(path) for path in expected_cache_dirs] == [0o700, 0o700, 0o700]


def test_catalog_cache_load_repairs_existing_cache_directory_permissions(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    document = _catalog_document("2026-05-18T12:00:00Z")
    cache_root = paths.sv_home / "cache"
    save_cached_catalog(paths, repo, document)
    cache_root.chmod(0o777)
    paths.cache_dir.chmod(0o777)
    paths.catalog_cache_dir.chmod(0o777)

    assert load_cached_catalog(paths, repo) == document

    assert _mode(cache_root) == 0o700
    assert _mode(paths.cache_dir) == 0o700
    assert _mode(paths.catalog_cache_dir) == 0o700


def test_catalog_cache_write_rejects_catalog_hash_mismatch(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    document = replace(
        _catalog_document("2026-05-18T12:00:00Z"),
        catalog_hash="sha256:" + ("1" * 64),
    )

    with pytest.raises(SvError, match="mismatched catalog_hash"):
        save_cached_catalog(paths, repo, document)

    assert not catalog_cache_path(paths, repo).exists()


def test_catalog_cache_access_rejects_symlinked_cache_ancestor(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    symlink_target = tmp_path / "attacker-cache"
    symlink_target.mkdir()
    paths.sv_home.mkdir()
    (paths.sv_home / "cache").symlink_to(symlink_target, target_is_directory=True)

    with pytest.raises(SvError, match="symlinked"):
        save_cached_catalog(paths, repo, _catalog_document("2026-05-18T12:00:00Z"))
    with pytest.raises(SvError, match="symlinked"):
        load_cached_catalog(paths, repo)


def test_catalog_cache_load_rejects_broken_symlink_cache_file(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    path = catalog_cache_path(paths, repo)
    path.parent.mkdir(parents=True)
    path.symlink_to(tmp_path / "missing-cache.toml")

    with pytest.raises(SvError, match="symlinked"):
        load_cached_catalog(paths, repo)


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


def test_get_catalog_with_cache_uses_fresh_metadata_without_refresh(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    save_cached_catalog(paths, repo, _catalog_document("2026-05-18T11:30:00Z"))
    calls: list[Sequence[RepoConfig]] = []

    def refresh(repos: Sequence[RepoConfig]) -> list[SourceSkill]:
        calls.append(repos)
        raise AssertionError("fresh cache should avoid refresh")

    catalog = get_catalog_with_cache(
        [repo],
        paths,
        policy=CachePolicy.default(),
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        refresh_catalog=refresh,
        warn=lambda message: None,
    )

    assert calls == []
    assert [(entry.name, entry.description) for entry in catalog] == [
        ("find-docs", "Find documentation.")
    ]


def test_get_catalog_with_cache_refreshes_expired_metadata(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    save_cached_catalog(paths, repo, _catalog_document("2026-05-17T11:00:00Z"))
    refreshed_entry = _source_skill(repo, paths, "new-skill")

    def refresh(repos: Sequence[RepoConfig]) -> list[SourceSkill]:
        assert list(repos) == [repo]
        return [refreshed_entry]

    catalog = get_catalog_with_cache(
        [repo],
        paths,
        policy=CachePolicy.default(),
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        refresh_catalog=refresh,
        warn=lambda message: None,
    )

    assert [entry.name for entry in catalog] == ["new-skill"]
    cached_document = load_cached_catalog(paths, repo)
    assert cached_document is not None
    assert cached_document.entries[0].name == "new-skill"


def test_get_catalog_with_cache_saves_explicit_successful_empty_refresh(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()

    def refresh(repos: Sequence[RepoConfig]) -> CacheRefreshResult:
        assert list(repos) == [repo]
        return CacheRefreshResult(entries=(), refreshed_repo_ids=frozenset({repo.id}))

    catalog = get_catalog_with_cache(
        [repo],
        paths,
        policy=CachePolicy.default(),
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        refresh_catalog=refresh,
        warn=lambda message: None,
    )

    assert catalog == []
    cached_document = load_cached_catalog(paths, repo)
    assert cached_document is not None
    assert cached_document.entries == ()
    assert cached_document.refreshed_at == "2026-05-18T12:00:00Z"


def test_get_catalog_with_cache_uses_stale_metadata_when_explicit_refresh_omits_repo(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    stale_document = _catalog_document("2026-05-17T11:00:00Z")
    save_cached_catalog(paths, repo, stale_document)
    warnings: list[str] = []

    def refresh(repos: Sequence[RepoConfig]) -> CacheRefreshResult:
        assert list(repos) == [repo]
        return CacheRefreshResult(entries=(), refreshed_repo_ids=frozenset())

    catalog = get_catalog_with_cache(
        [repo],
        paths,
        policy=CachePolicy.default(),
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        refresh_catalog=refresh,
        warn=warnings.append,
    )

    assert [entry.name for entry in catalog] == ["find-docs"]
    assert load_cached_catalog(paths, repo) == stale_document
    assert warnings == [
        "warning: using stale cached metadata for Org/Skills; refresh failed: "
        "refresh did not return metadata for Org/Skills"
    ]


def test_get_catalog_with_cache_persists_refresh_backend_and_index_hash(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    refreshed_entry = _source_skill(repo, paths, "indexed-skill")
    index_hash = (
        "sha256:a51a6c19a1ffc7416827e89adf20749d23ad42452c396cf7e627409f2896922c"
    )

    def refresh(repos: Sequence[RepoConfig]) -> CacheRefreshResult:
        assert list(repos) == [repo]
        return CacheRefreshResult(
            entries=(refreshed_entry,),
            refreshed_backends_by_repo={repo.id: "indexed-backend"},
            index_hashes_by_repo={repo.id: index_hash},
            refreshed_repo_ids=frozenset({repo.id}),
        )

    get_catalog_with_cache(
        [repo],
        paths,
        policy=CachePolicy.default(),
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        refresh_catalog=refresh,
        warn=lambda message: None,
    )

    cached_document = load_cached_catalog(paths, repo)
    assert cached_document is not None
    assert cached_document.backend == "indexed-backend"
    assert cached_document.index_hash == index_hash


def test_get_catalog_with_cache_warns_and_uses_stale_metadata_when_refresh_fails(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    save_cached_catalog(paths, repo, _catalog_document("2026-05-17T11:00:00Z"))
    warnings: list[str] = []

    def refresh(repos: Sequence[RepoConfig]) -> list[SourceSkill]:
        raise SvError("network unavailable")

    catalog = get_catalog_with_cache(
        [repo],
        paths,
        policy=CachePolicy.default(),
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        refresh_catalog=refresh,
        warn=warnings.append,
    )

    assert [entry.name for entry in catalog] == ["find-docs"]
    assert warnings == [
        "warning: using stale cached metadata for Org/Skills; refresh failed: network unavailable"
    ]


def test_get_catalog_with_cache_can_fail_closed_when_refresh_fails(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    save_cached_catalog(paths, repo, _catalog_document("2026-05-17T11:00:00Z"))

    def refresh(repos: Sequence[RepoConfig]) -> list[SourceSkill]:
        raise SvError("network unavailable")

    with pytest.raises(SvError, match="network unavailable"):
        get_catalog_with_cache(
            [repo],
            paths,
            policy=CachePolicy.force_refresh(allow_stale_on_error=False),
            now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
            refresh_catalog=refresh,
            warn=lambda message: None,
        )


def test_get_catalog_with_cache_cache_only_fails_without_cached_metadata(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()

    def refresh(repos: Sequence[RepoConfig]) -> list[SourceSkill]:
        raise AssertionError("cache-only mode must not refresh")

    with pytest.raises(SvError, match="No cached metadata found for Org/Skills"):
        get_catalog_with_cache(
            [repo],
            paths,
            policy=CachePolicy.cache_only(),
            now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
            refresh_catalog=refresh,
            warn=lambda message: None,
        )


def test_get_catalog_with_cache_refreshes_when_normal_mode_cache_is_corrupt(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    catalog_cache_path(paths, repo).parent.mkdir(parents=True)
    catalog_cache_path(paths, repo).write_text("not = [valid", encoding="utf-8")
    warnings: list[str] = []
    refreshed_entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=paths.source_repo_for(repo.id),
        source_path=paths.source_repo_for(repo.id) / "skills" / "alpha",
        source_relative_path="skills/alpha",
        source_backend="fake",
    )

    def refresh(repos: Sequence[RepoConfig]) -> list[SourceSkill]:
        assert list(repos) == [repo]
        return [refreshed_entry]

    catalog = get_catalog_with_cache(
        [repo],
        paths,
        policy=CachePolicy.default(),
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        refresh_catalog=refresh,
        warn=warnings.append,
    )

    assert [entry.name for entry in catalog] == ["alpha"]
    assert len(warnings) == 1
    assert warnings[0].startswith(
        "warning: ignoring invalid cached metadata for Org/Skills: Failed to read sv catalog cache"
    )


def test_get_catalog_with_cache_cache_only_fails_when_cache_is_corrupt(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    catalog_cache_path(paths, repo).parent.mkdir(parents=True)
    catalog_cache_path(paths, repo).write_text("not = [valid", encoding="utf-8")

    def refresh(repos: Sequence[RepoConfig]) -> list[SourceSkill]:
        raise AssertionError("cache-only mode must not refresh")

    with pytest.raises(SvError, match="Failed to read sv catalog cache"):
        get_catalog_with_cache(
            [repo],
            paths,
            policy=CachePolicy.cache_only(),
            now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
            refresh_catalog=refresh,
            warn=lambda message: None,
        )


def test_get_catalog_with_cache_fails_closed_for_symlinked_catalog_cache(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    path = catalog_cache_path(paths, repo)
    path.parent.mkdir(parents=True)
    target = tmp_path / "attacker-catalog.toml"
    path.symlink_to(target)

    def refresh(repos: Sequence[RepoConfig]) -> list[SourceSkill]:
        raise AssertionError("symlinked cache metadata must fail before refresh")

    with pytest.raises(SvError, match="symlinked"):
        get_catalog_with_cache(
            [repo],
            paths,
            policy=CachePolicy.default(),
            now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
            refresh_catalog=refresh,
            warn=lambda message: None,
        )


def test_skill_body_cache_stores_and_materializes_valid_skill(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")

    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    destination = tmp_path / "dest" / "alpha"

    materialized = try_materialize_from_skill_body_cache(
        paths,
        content_hash=content_hash,
        skill_name="alpha",
        destination=destination,
        now=datetime(2026, 5, 18, 13, tzinfo=UTC),
    )

    assert materialized is True
    assert (destination / "notes.md").read_text(encoding="utf-8") == "body\n"
    metadata = load_skill_body_metadata(paths, content_hash)
    assert metadata.use_count == 2
    assert metadata.last_used_at == "2026-05-18T13:00:00Z"


def test_skill_body_cache_hit_prunes_stale_body_entries(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    old_skill = _write_skill_tree(tmp_path / "old-source", "old", "old body\n")
    fresh_skill = _write_skill_tree(
        tmp_path / "fresh-source", "fresh", "fresh body\n"
    )
    old_hash = sha256_skill_directory(old_skill, expected_name="old")
    fresh_hash = sha256_skill_directory(fresh_skill, expected_name="fresh")
    initial_now = datetime(2026, 4, 1, tzinfo=UTC)
    store_skill_body_cache(
        paths,
        old_skill,
        skill_name="old",
        content_hash=old_hash,
        source_reference="Org/Skills:skills/old",
        now=initial_now,
    )
    store_skill_body_cache(
        paths,
        fresh_skill,
        skill_name="fresh",
        content_hash=fresh_hash,
        source_reference="Org/Skills:skills/fresh",
        now=initial_now,
    )
    paths.cache_prune_marker.write_text(
        'schema_version = 1\nlast_pruned_at = "2026-04-01T00:00:00Z"\n',
        encoding="utf-8",
    )
    destination = tmp_path / "dest" / "fresh"

    materialized = try_materialize_from_skill_body_cache(
        paths,
        content_hash=fresh_hash,
        skill_name="fresh",
        destination=destination,
        now=datetime(2026, 5, 18, tzinfo=UTC),
    )

    assert materialized is True
    assert not skill_body_cache_path(paths, old_hash).exists()
    assert skill_body_cache_path(paths, fresh_hash).exists()
    assert (destination / "notes.md").read_text(encoding="utf-8") == "fresh body\n"


def test_skill_body_cache_touch_repairs_permissive_entry_directory(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    entry_dir = skill_body_cache_path(paths, content_hash)
    entry_dir.chmod(0o777)

    materialized = try_materialize_from_skill_body_cache(
        paths,
        content_hash=content_hash,
        skill_name="alpha",
        destination=tmp_path / "dest" / "alpha",
        now=datetime(2026, 5, 18, 13, tzinfo=UTC),
    )

    assert materialized is True
    assert _mode(entry_dir) == 0o700
    metadata = load_skill_body_metadata(paths, content_hash)
    assert metadata.use_count == 2
    assert metadata.last_used_at == "2026-05-18T13:00:00Z"


def test_skill_body_cache_touch_refuses_symlinked_metadata_temp_file(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    metadata_path = skill_body_cache_path(paths, content_hash) / "metadata.toml"
    temp_path = metadata_path.with_name(f".{metadata_path.name}.{os.getpid()}.tmp")
    attacker_target = tmp_path / "attacker-target.toml"
    temp_path.symlink_to(attacker_target)

    with pytest.raises(SvError, match="symlinked"):
        try_materialize_from_skill_body_cache(
            paths,
            content_hash=content_hash,
            skill_name="alpha",
            destination=tmp_path / "dest" / "alpha",
            now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        )

    assert not attacker_target.exists()


def test_skill_body_cache_miss_returns_false(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    destination = tmp_path / "dest" / "alpha"
    content_hash = "sha256:" + ("1" * 64)

    assert (
        try_materialize_from_skill_body_cache(
            paths,
            content_hash=content_hash,
            skill_name="alpha",
            destination=destination,
            now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        )
        is False
    )
    assert not destination.exists()


def test_store_skill_body_cache_refuses_symlinked_tmp_dir(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    attacker_dir = tmp_path / "attacker"
    attacker_dir.mkdir()
    paths.cache_dir.mkdir(parents=True)
    paths.cache_tmp_dir.symlink_to(attacker_dir, target_is_directory=True)

    with pytest.raises(SvError, match="symlinked"):
        store_skill_body_cache(
            paths,
            source_skill,
            skill_name="alpha",
            content_hash=content_hash,
            source_reference="Org/Skills:skills/alpha",
            now=datetime(2026, 5, 18, 12, tzinfo=UTC),
        )

    assert list(attacker_dir.iterdir()) == []


def test_store_skill_body_cache_refuses_symlinked_body_root(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    attacker_dir = tmp_path / "attacker"
    attacker_dir.mkdir()
    paths.skill_body_cache_dir.mkdir(parents=True)
    (paths.skill_body_cache_dir / "sha256").symlink_to(
        attacker_dir, target_is_directory=True
    )

    with pytest.raises(SvError, match="symlinked"):
        store_skill_body_cache(
            paths,
            source_skill,
            skill_name="alpha",
            content_hash=content_hash,
            source_reference="Org/Skills:skills/alpha",
            now=datetime(2026, 5, 18, 12, tzinfo=UTC),
        )

    assert list(attacker_dir.iterdir()) == []


def test_skill_body_cache_metadata_hash_mismatch_is_treated_as_miss(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    metadata_path = skill_body_cache_path(paths, content_hash) / "metadata.toml"
    metadata_path.write_text(
        metadata_path.read_text(encoding="utf-8").replace(
            content_hash, "sha256:" + ("2" * 64)
        ),
        encoding="utf-8",
    )
    destination = tmp_path / "dest" / "alpha"

    assert (
        try_materialize_from_skill_body_cache(
            paths,
            content_hash=content_hash,
            skill_name="alpha",
            destination=destination,
            now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        )
        is False
    )
    assert not destination.exists()
    assert not skill_body_cache_path(paths, content_hash).exists()


def test_skill_body_cache_corrupt_metadata_is_treated_as_miss(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    (skill_body_cache_path(paths, content_hash) / "metadata.toml").write_text(
        "not = [valid", encoding="utf-8"
    )
    destination = tmp_path / "dest" / "alpha"

    assert (
        try_materialize_from_skill_body_cache(
            paths,
            content_hash=content_hash,
            skill_name="alpha",
            destination=destination,
            now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        )
        is False
    )
    assert not destination.exists()
    assert not skill_body_cache_path(paths, content_hash).exists()


def test_skill_body_cache_symlinked_metadata_fails_closed(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    metadata_path = skill_body_cache_path(paths, content_hash) / "metadata.toml"
    metadata_path.unlink()
    metadata_path.symlink_to(tmp_path / "attacker-metadata.toml")

    with pytest.raises(SvError, match="symlinked"):
        try_materialize_from_skill_body_cache(
            paths,
            content_hash=content_hash,
            skill_name="alpha",
            destination=tmp_path / "dest" / "alpha",
            now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        )

    assert skill_body_cache_path(paths, content_hash).exists()


def test_skill_body_cache_symlinked_body_file_fails_closed(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    notes_path = skill_body_cache_path(paths, content_hash) / "skill" / "notes.md"
    notes_path.unlink()
    notes_path.symlink_to(tmp_path / "attacker-notes.md")

    with pytest.raises(SvError, match="symlink"):
        try_materialize_from_skill_body_cache(
            paths,
            content_hash=content_hash,
            skill_name="alpha",
            destination=tmp_path / "dest" / "alpha",
            now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        )

    assert skill_body_cache_path(paths, content_hash).exists()


def test_skill_body_cache_corrupt_body_is_treated_as_miss(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    (skill_body_cache_path(paths, content_hash) / "skill" / "notes.md").write_text(
        "tampered\n", encoding="utf-8"
    )
    destination = tmp_path / "dest" / "alpha"

    assert (
        try_materialize_from_skill_body_cache(
            paths,
            content_hash=content_hash,
            skill_name="alpha",
            destination=destination,
            now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        )
        is False
    )
    assert not destination.exists()
    assert not skill_body_cache_path(paths, content_hash).exists()


def test_store_skill_body_cache_replaces_corrupt_existing_metadata(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    (skill_body_cache_path(paths, content_hash) / "metadata.toml").write_text(
        "not = [valid", encoding="utf-8"
    )

    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 13, tzinfo=UTC),
    )

    metadata = load_skill_body_metadata(paths, content_hash)
    assert metadata.last_used_at == "2026-05-18T13:00:00Z"
    assert metadata.use_count == 1


def test_prune_skill_body_cache_removes_entries_unused_for_more_than_30_days(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    old_skill = _write_skill_tree(tmp_path / "old-source", "old", "x" * 100)
    fresh_skill = _write_skill_tree(tmp_path / "fresh-source", "fresh", "y" * 100)
    old_hash = sha256_skill_directory(old_skill, expected_name="old")
    fresh_hash = sha256_skill_directory(fresh_skill, expected_name="fresh")
    store_skill_body_cache(
        paths,
        old_skill,
        skill_name="old",
        content_hash=old_hash,
        source_reference="Org/Skills:skills/old",
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    store_skill_body_cache(
        paths,
        fresh_skill,
        skill_name="fresh",
        content_hash=fresh_hash,
        source_reference="Org/Skills:skills/fresh",
        now=datetime(2026, 5, 17, tzinfo=UTC),
    )

    prune_skill_body_cache(
        paths,
        now=datetime(2026, 5, 18, tzinfo=UTC),
        max_unused_seconds=DEFAULT_SKILL_BODY_MAX_UNUSED_SECONDS,
        force=True,
    )

    assert not skill_body_cache_path(paths, old_hash).exists()
    assert skill_body_cache_path(paths, fresh_hash).exists()


def test_prune_skill_body_cache_enforces_size_cap_by_lru_then_use_count(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    first = _write_skill_tree(tmp_path / "first-source", "first", "a" * 100)
    second = _write_skill_tree(tmp_path / "second-source", "second", "b" * 100)
    first_hash = sha256_skill_directory(first, expected_name="first")
    second_hash = sha256_skill_directory(second, expected_name="second")
    store_skill_body_cache(
        paths,
        first,
        skill_name="first",
        content_hash=first_hash,
        source_reference="Org/Skills:skills/first",
        now=datetime(2026, 5, 10, tzinfo=UTC),
    )
    store_skill_body_cache(
        paths,
        second,
        skill_name="second",
        content_hash=second_hash,
        source_reference="Org/Skills:skills/second",
        now=datetime(2026, 5, 18, tzinfo=UTC),
    )
    second_size = load_skill_body_metadata(paths, second_hash).size_bytes

    prune_skill_body_cache(
        paths,
        now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        max_bytes=second_size,
        force=True,
    )

    assert not skill_body_cache_path(paths, first_hash).exists()
    assert skill_body_cache_path(paths, second_hash).exists()


def test_prune_skill_body_cache_uses_actual_size_when_metadata_underreports(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    first = _write_skill_tree(tmp_path / "first-source", "first", "a" * 100)
    second = _write_skill_tree(tmp_path / "second-source", "second", "b" * 100)
    first_hash = sha256_skill_directory(first, expected_name="first")
    second_hash = sha256_skill_directory(second, expected_name="second")
    store_skill_body_cache(
        paths,
        first,
        skill_name="first",
        content_hash=first_hash,
        source_reference="Org/Skills:skills/first",
        now=datetime(2026, 5, 10, tzinfo=UTC),
    )
    store_skill_body_cache(
        paths,
        second,
        skill_name="second",
        content_hash=second_hash,
        source_reference="Org/Skills:skills/second",
        now=datetime(2026, 5, 18, tzinfo=UTC),
    )
    first_size = load_skill_body_metadata(paths, first_hash).size_bytes
    metadata_path = skill_body_cache_path(paths, first_hash) / "metadata.toml"
    metadata_path.write_text(
        metadata_path.read_text(encoding="utf-8").replace(
            f"size_bytes = {first_size}", "size_bytes = 1"
        ),
        encoding="utf-8",
    )
    second_size = load_skill_body_metadata(paths, second_hash).size_bytes

    prune_skill_body_cache(
        paths,
        now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        max_bytes=second_size + 1,
        force=True,
    )

    assert not skill_body_cache_path(paths, first_hash).exists()
    assert skill_body_cache_path(paths, second_hash).exists()


def test_prune_skill_body_cache_enforces_size_cap_even_with_fresh_marker(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    first = _write_skill_tree(tmp_path / "first-source", "first", "a" * 100)
    second = _write_skill_tree(tmp_path / "second-source", "second", "b" * 100)
    first_hash = sha256_skill_directory(first, expected_name="first")
    second_hash = sha256_skill_directory(second, expected_name="second")
    store_skill_body_cache(
        paths,
        first,
        skill_name="first",
        content_hash=first_hash,
        source_reference="Org/Skills:skills/first",
        now=datetime(2026, 5, 10, tzinfo=UTC),
    )
    store_skill_body_cache(
        paths,
        second,
        skill_name="second",
        content_hash=second_hash,
        source_reference="Org/Skills:skills/second",
        now=datetime(2026, 5, 18, tzinfo=UTC),
    )
    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    paths.cache_prune_marker.write_text(
        'schema_version = 1\nlast_pruned_at = "2026-05-18T12:30:00Z"\n',
        encoding="utf-8",
    )
    second_size = load_skill_body_metadata(paths, second_hash).size_bytes

    prune_skill_body_cache(
        paths,
        now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        max_bytes=second_size,
        force=False,
    )

    assert not skill_body_cache_path(paths, first_hash).exists()
    assert skill_body_cache_path(paths, second_hash).exists()


def test_prune_skill_body_cache_treats_future_marker_as_due(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    old_skill = _write_skill_tree(tmp_path / "old-source", "old", "x" * 100)
    old_hash = sha256_skill_directory(old_skill, expected_name="old")
    store_skill_body_cache(
        paths,
        old_skill,
        skill_name="old",
        content_hash=old_hash,
        source_reference="Org/Skills:skills/old",
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    paths.cache_prune_marker.write_text(
        'schema_version = 1\nlast_pruned_at = "2026-05-19T00:00:00Z"\n',
        encoding="utf-8",
    )

    prune_skill_body_cache(
        paths,
        now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        force=False,
    )

    assert not skill_body_cache_path(paths, old_hash).exists()
    assert (
        'last_pruned_at = "2026-05-18T13:00:00Z"'
        in paths.cache_prune_marker.read_text(encoding="utf-8")
    )


def test_prune_skill_body_cache_removes_future_dated_body_metadata(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    future_skill = _write_skill_tree(tmp_path / "future-source", "future", "x" * 100)
    future_hash = sha256_skill_directory(future_skill, expected_name="future")
    store_skill_body_cache(
        paths,
        future_skill,
        skill_name="future",
        content_hash=future_hash,
        source_reference="Org/Skills:skills/future",
        now=datetime(2026, 5, 18, tzinfo=UTC),
    )
    metadata_path = skill_body_cache_path(paths, future_hash) / "metadata.toml"
    metadata_path.write_text(
        metadata_path.read_text(encoding="utf-8")
        .replace(
            'created_at = "2026-05-18T00:00:00Z"', 'created_at = "2026-05-19T00:00:00Z"'
        )
        .replace(
            'last_used_at = "2026-05-18T00:00:00Z"',
            'last_used_at = "2026-05-19T00:00:00Z"',
        ),
        encoding="utf-8",
    )

    prune_skill_body_cache(
        paths,
        now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        force=True,
    )

    assert not skill_body_cache_path(paths, future_hash).exists()


def test_prune_skill_body_cache_refuses_symlinked_marker_temp_file_before_deleting(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    old_skill = _write_skill_tree(tmp_path / "old-source", "old", "old body\n")
    old_hash = sha256_skill_directory(old_skill, expected_name="old")
    store_skill_body_cache(
        paths,
        old_skill,
        skill_name="old",
        content_hash=old_hash,
        source_reference="Org/Skills:skills/old",
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    temp_path = paths.cache_prune_marker.with_name(
        f".{paths.cache_prune_marker.name}.{os.getpid()}.tmp"
    )
    attacker_target = tmp_path / "attacker-marker.toml"
    temp_path.symlink_to(attacker_target)

    with pytest.raises(SvError, match="symlinked"):
        prune_skill_body_cache(
            paths,
            now=datetime(2026, 5, 18, 13, tzinfo=UTC),
            force=True,
        )

    assert not attacker_target.exists()
    assert skill_body_cache_path(paths, old_hash).exists()


def test_prune_skill_body_cache_ignores_corrupt_prune_marker(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.cache_dir.mkdir(parents=True)
    paths.cache_prune_marker.write_text("not = [valid", encoding="utf-8")

    prune_skill_body_cache(
        paths,
        now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        force=False,
    )

    assert (
        'last_pruned_at = "2026-05-18T13:00:00Z"'
        in paths.cache_prune_marker.read_text(encoding="utf-8")
    )


def test_prune_skill_body_cache_refuses_symlinked_body_entry(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    root = paths.skill_body_cache_dir / "sha256"
    root.mkdir(parents=True)
    attacker_dir = tmp_path / "attacker-entry"
    attacker_dir.mkdir()
    (root / ("1" * 64)).symlink_to(attacker_dir, target_is_directory=True)

    with pytest.raises(SvError, match="symlinked"):
        prune_skill_body_cache(
            paths,
            now=datetime(2026, 5, 18, 13, tzinfo=UTC),
            force=True,
        )


def test_prune_skill_body_cache_refuses_symlinked_marker_file(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.cache_dir.mkdir(parents=True)
    attacker_file = tmp_path / "attacker-marker.toml"
    paths.cache_prune_marker.symlink_to(attacker_file)

    with pytest.raises(SvError, match="symlinked"):
        prune_skill_body_cache(
            paths,
            now=datetime(2026, 5, 18, 13, tzinfo=UTC),
            force=False,
        )


def test_prune_skill_body_cache_force_refuses_symlinked_marker_before_deleting(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    old_skill = _write_skill_tree(tmp_path / "old-source", "old", "old body\n")
    old_hash = sha256_skill_directory(old_skill, expected_name="old")
    store_skill_body_cache(
        paths,
        old_skill,
        skill_name="old",
        content_hash=old_hash,
        source_reference="Org/Skills:skills/old",
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    attacker_file = tmp_path / "attacker-marker.toml"
    attacker_file.write_text("do not overwrite\n", encoding="utf-8")
    paths.cache_prune_marker.unlink()
    paths.cache_prune_marker.symlink_to(attacker_file)

    with pytest.raises(SvError, match="symlinked"):
        prune_skill_body_cache(
            paths,
            now=datetime(2026, 5, 18, 13, tzinfo=UTC),
            force=True,
        )

    assert attacker_file.read_text(encoding="utf-8") == "do not overwrite\n"
    assert skill_body_cache_path(paths, old_hash).exists()


def test_cache_aware_materializer_uses_body_cache_before_source(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    cached_skill = _write_skill_tree(tmp_path / "cache-source", "alpha", "cached\n")
    content_hash = sha256_skill_directory(cached_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        cached_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    repo = _repo()
    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=paths.source_repo_for(repo.id),
        source_path=paths.source_repo_for(repo.id) / "skills" / "alpha",
        source_relative_path="skills/alpha",
        source_backend="fake",
        source_content_hash=content_hash,
        _materializer=lambda destination: (_ for _ in ()).throw(
            AssertionError("source materializer must not be called")
        ),
    )

    wrapped = wrap_catalog_with_skill_body_cache(
        [entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: None,
    )[0]
    destination = tmp_path / "destination" / "alpha"

    wrapped.materialize_to(destination)

    assert (destination / "notes.md").read_text(encoding="utf-8") == "cached\n"


def test_cache_only_materializer_uses_body_cache_when_touch_maintenance_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SvPaths.from_home(tmp_path)
    cached_skill = _write_skill_tree(tmp_path / "cache-source", "alpha", "cached\n")
    content_hash = sha256_skill_directory(cached_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        cached_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    repo = _repo()
    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=paths.source_repo_for(repo.id),
        source_path=paths.source_repo_for(repo.id) / "skills" / "alpha",
        source_relative_path="skills/alpha",
        source_backend="fake",
        source_content_hash=content_hash,
        _materializer=lambda destination: (_ for _ in ()).throw(
            AssertionError("source materializer must not be called")
        ),
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        source_cache,
        "_touch_skill_body_cache",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            SvError("metadata write unavailable")
        ),
    )
    wrapped = wrap_catalog_with_skill_body_cache(
        [entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: None,
        allow_source_fallback=False,
        warn=warnings.append,
    )[0]
    destination = tmp_path / "destination" / "alpha"

    wrapped.materialize_to(destination)

    assert (destination / "notes.md").read_text(encoding="utf-8") == "cached\n"
    assert skill_body_cache_path(paths, content_hash).exists()
    assert warnings == [
        "warning: failed skill body cache touch/prune maintenance for alpha: metadata write unavailable"
    ]


def test_cache_aware_materializer_stores_source_materialization_on_miss(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha", "remote\n")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    repo = _repo()
    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=tmp_path / "source",
        source_path=source_skill,
        source_relative_path="skills/alpha",
        source_backend="local-cache",
        source_content_hash=content_hash,
    )
    stored: list[tuple[SourceSkill, str, str]] = []
    wrapped = wrap_catalog_with_skill_body_cache(
        [entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: stored.append(
            (entry, content_hash, skill_file_hash)
        ),
    )[0]
    destination = tmp_path / "destination" / "alpha"

    wrapped.materialize_to(destination)

    assert stored == [(entry, content_hash, sha256_file(destination / "SKILL.md"))]
    assert (destination / "notes.md").read_text(encoding="utf-8") == "remote\n"
    later = tmp_path / "later" / "alpha"
    assert (
        try_materialize_from_skill_body_cache(
            paths,
            content_hash=content_hash,
            skill_name="alpha",
            destination=later,
            now=datetime(2026, 5, 18, 14, tzinfo=UTC),
        )
        is True
    )
    assert (later / "notes.md").read_text(encoding="utf-8") == "remote\n"


def test_cache_aware_materializer_removes_destination_on_hash_mismatch(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha", "remote\n")
    repo = _repo()
    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=tmp_path / "source",
        source_path=source_skill,
        source_relative_path="skills/alpha",
        source_backend="local-cache",
        source_content_hash="sha256:41cf6794ba4200b839c53531555f0f3998df4cbb01a4d5cb0b94e3ca5e23947d",
    )
    wrapped = wrap_catalog_with_skill_body_cache(
        [entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: None,
    )[0]
    destination = tmp_path / "destination" / "alpha"

    with pytest.raises(SvError, match="hash did not match"):
        wrapped.materialize_to(destination)

    assert not destination.exists()


def test_cache_only_materializer_fails_on_body_miss_without_source_call(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=paths.source_repo_for(repo.id),
        source_path=paths.source_repo_for(repo.id) / "skills" / "alpha",
        source_relative_path="skills/alpha",
        source_backend="cache:github-https-api",
        source_content_hash="sha256:41cf6794ba4200b839c53531555f0f3998df4cbb01a4d5cb0b94e3ca5e23947d",
        _materializer=lambda destination: (_ for _ in ()).throw(
            AssertionError("source materializer must not be called")
        ),
    )
    wrapped = wrap_catalog_with_skill_body_cache(
        [entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: None,
        allow_source_fallback=False,
    )[0]

    with pytest.raises(
        SvError,
        match="Cached skill body for Org/Skills:alpha was not found",
    ):
        wrapped.materialize_to(tmp_path / "destination" / "alpha")


def test_cached_metadata_materializer_refreshes_before_source_body_miss(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    expected_skill = tmp_path / "expected" / "alpha"
    expected_skill.mkdir(parents=True)
    (expected_skill / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill.\n---\n", encoding="utf-8"
    )
    (expected_skill / "notes.md").write_text("remote\n", encoding="utf-8")
    content_hash = sha256_skill_directory(expected_skill, expected_name="alpha")
    cached_entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=paths.source_repo_for(repo.id),
        source_path=paths.source_repo_for(repo.id) / "skills" / "alpha",
        source_relative_path="skills/alpha",
        source_backend="cache:github-https-api",
        source_content_hash=content_hash,
    )
    backend = FakeSourceBackend(
        {
            "skills/alpha/SKILL.md": "---\nname: alpha\ndescription: Alpha skill.\n---\n",
            "skills/alpha/notes.md": "remote\n",
        }
    )
    refreshed_with_source = replace(
        cached_entry,
        source_backend="fake",
        _materializer=lambda destination: backend.materialize_folder(
            "skills/alpha", destination
        ),
    )
    refresh_calls: list[SourceSkill] = []
    wrapped = wrap_catalog_with_skill_body_cache(
        [cached_entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: None,
        refresh_entry_on_body_miss=lambda entry: (
            refresh_calls.append(entry) or refreshed_with_source
        ),
    )[0]
    destination = tmp_path / "destination" / "alpha"

    wrapped.materialize_to(destination)

    assert refresh_calls == [cached_entry]
    assert (destination / "notes.md").read_text(encoding="utf-8") == "remote\n"
    later = tmp_path / "later" / "alpha"
    assert (
        try_materialize_from_skill_body_cache(
            paths,
            content_hash=content_hash,
            skill_name="alpha",
            destination=later,
            now=datetime(2026, 5, 18, 14, tzinfo=UTC),
        )
        is True
    )


def test_attach_source_materializers_restores_source_fallback_for_cached_entries(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    cached_entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=paths.source_repo_for(repo.id),
        source_path=paths.source_repo_for(repo.id) / "skills" / "alpha",
        source_relative_path="skills/alpha",
        source_backend="cache:github-https-api",
    )
    backend = FakeSourceBackend(
        {
            "skills/alpha/SKILL.md": "---\nname: alpha\ndescription: Alpha skill.\n---\n",
            "skills/alpha/notes.md": "remote\n",
        }
    )

    attached = attach_source_materializers(
        [cached_entry], [repo], backend_factory=lambda selected_repo: (backend,)
    )[0]
    destination = tmp_path / "destination" / "alpha"

    attached.materialize_to(destination)

    assert (destination / "notes.md").read_text(encoding="utf-8") == "remote\n"


def test_record_cached_skill_body_hash_fills_missing_hashes_with_index_hash(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    document = CachedCatalogDocument(
        repo_id=repo.id,
        repo_url=repo.url,
        source_key="github:org/skills",
        skills_paths=(),
        backend="git-local-source",
        refreshed_at="2026-05-18T12:00:00Z",
        catalog_hash="sha256:" + ("0" * 64),
        index_hash="sha256:" + ("8" * 64),
        entries=(
            CachedCatalogEntry(
                name="find-docs",
                description="Find documentation.",
                source_path="skills/find-docs",
                content_hash=None,
                skill_file_hash=None,
            ),
        ),
    )
    save_cached_catalog(
        paths, repo, replace(document, catalog_hash=_cached_catalog_hash(document))
    )
    source_skill = _write_skill_tree(tmp_path / "source", "find-docs", "remote\n")
    content_hash = sha256_skill_directory(source_skill, expected_name="find-docs")
    skill_file_hash = sha256_file(source_skill / "SKILL.md")
    entry = SourceSkill(
        name="find-docs",
        description="Find documentation.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=tmp_path / "source",
        source_path=source_skill,
        source_relative_path="skills/find-docs",
        source_backend="cache:git-local-source",
    )

    record_cached_skill_body_hash(
        paths,
        repo,
        entry,
        content_hash=content_hash,
        skill_file_hash=skill_file_hash,
    )

    cached = load_cached_catalog(paths, repo)
    assert cached is not None
    assert cached.entries[0].content_hash == content_hash
    assert cached.entries[0].skill_file_hash == skill_file_hash


def test_cache_aware_materializer_keeps_destination_when_metadata_writeback_fails(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha", "remote\n")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    repo = _repo()
    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=tmp_path / "source",
        source_path=source_skill,
        source_relative_path="skills/alpha",
        source_backend="local-cache",
        source_content_hash=content_hash,
    )
    warnings: list[str] = []
    wrapped = wrap_catalog_with_skill_body_cache(
        [entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: (_ for _ in ()).throw(
            SvError("cached metadata is corrupt")
        ),
        warn=warnings.append,
    )[0]
    destination = tmp_path / "destination" / "alpha"

    wrapped.materialize_to(destination)

    assert (destination / "notes.md").read_text(encoding="utf-8") == "remote\n"
    assert warnings
    assert "failed cached metadata/hash writeback" in warnings[0]


def test_record_cached_skill_body_hash_does_not_overwrite_existing_hashes(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    existing_content_hash = "sha256:" + ("1" * 64)
    existing_skill_file_hash = "sha256:" + ("2" * 64)
    document = CachedCatalogDocument(
        repo_id=repo.id,
        repo_url=repo.url,
        source_key="github:org/skills",
        skills_paths=(),
        backend="github-index",
        refreshed_at="2026-05-18T12:00:00Z",
        catalog_hash="sha256:" + ("0" * 64),
        index_hash="sha256:" + ("3" * 64),
        entries=(
            CachedCatalogEntry(
                name="find-docs",
                description="Find documentation.",
                source_path="skills/find-docs",
                content_hash=existing_content_hash,
                skill_file_hash=existing_skill_file_hash,
            ),
        ),
    )
    save_cached_catalog(
        paths, repo, replace(document, catalog_hash=_cached_catalog_hash(document))
    )
    entry = SourceSkill(
        name="find-docs",
        description="Find documentation.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=tmp_path / "source",
        source_path=tmp_path / "source" / "find-docs",
        source_relative_path="skills/find-docs",
        source_backend="cache:github-index",
    )

    record_cached_skill_body_hash(
        paths,
        repo,
        entry,
        content_hash="sha256:" + ("4" * 64),
        skill_file_hash="sha256:" + ("5" * 64),
    )

    cached = load_cached_catalog(paths, repo)
    assert cached is not None
    assert cached.entries[0].content_hash == existing_content_hash
    assert cached.entries[0].skill_file_hash == existing_skill_file_hash


@pytest.mark.parametrize(
    ("content_hash", "skill_file_hash"),
    [
        ("not-a-sha256", "sha256:" + ("6" * 64)),
        ("sha256:" + ("7" * 64), "not-a-sha256"),
    ],
)
def test_record_cached_skill_body_hash_rejects_invalid_observed_hashes_before_writing(
    tmp_path: Path, content_hash: str, skill_file_hash: str
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    document = CachedCatalogDocument(
        repo_id=repo.id,
        repo_url=repo.url,
        source_key="github:org/skills",
        skills_paths=(),
        backend="git-local-source",
        refreshed_at="2026-05-18T12:00:00Z",
        catalog_hash="sha256:" + ("0" * 64),
        index_hash=None,
        entries=(
            CachedCatalogEntry(
                name="find-docs",
                description="Find documentation.",
                source_path="skills/find-docs",
                content_hash=None,
                skill_file_hash=None,
            ),
        ),
    )
    save_cached_catalog(
        paths, repo, replace(document, catalog_hash=_cached_catalog_hash(document))
    )
    before = catalog_cache_path(paths, repo).read_text(encoding="utf-8")
    entry = SourceSkill(
        name="find-docs",
        description="Find documentation.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=tmp_path / "source",
        source_path=tmp_path / "source" / "find-docs",
        source_relative_path="skills/find-docs",
        source_backend="cache:git-local-source",
    )

    with pytest.raises(SvError, match="sha256 digest"):
        record_cached_skill_body_hash(
            paths,
            repo,
            entry,
            content_hash=content_hash,
            skill_file_hash=skill_file_hash,
        )

    assert catalog_cache_path(paths, repo).read_text(encoding="utf-8") == before


def test_skill_body_cache_path_rejects_invalid_hash(tmp_path: Path) -> None:
    with pytest.raises(SvError, match="sha256 digest"):
        skill_body_cache_path(SvPaths.from_home(tmp_path), "not-a-sha256")


def test_store_skill_body_cache_cleans_temporary_root_when_copy_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")

    def fail_copy(*args: object, **kwargs: object) -> Path:
        raise SvError("disk full")

    monkeypatch.setattr(source_cache, "copy_skill_folder_to_temp", fail_copy)

    with pytest.raises(SvError, match="disk full"):
        store_skill_body_cache(
            paths,
            source_skill,
            skill_name="alpha",
            content_hash=content_hash,
            source_reference="Org/Skills:skills/alpha",
            now=datetime(2026, 5, 18, 12, tzinfo=UTC),
        )

    assert list(paths.cache_tmp_dir.iterdir()) == []
    assert not skill_body_cache_path(paths, content_hash).exists()


def test_store_skill_body_cache_fails_when_temp_names_are_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    digest = content_hash.removeprefix("sha256:")

    class FixedUuid:
        hex = "fixed"

    monkeypatch.setattr(source_cache.uuid, "uuid4", lambda: FixedUuid())
    paths.cache_tmp_dir.mkdir(parents=True)
    (paths.cache_tmp_dir / f"skill-{digest}-fixed").mkdir()

    with pytest.raises(SvError, match="temporary directory"):
        store_skill_body_cache(
            paths,
            source_skill,
            skill_name="alpha",
            content_hash=content_hash,
            source_reference="Org/Skills:skills/alpha",
            now=datetime(2026, 5, 18, 12, tzinfo=UTC),
        )


def test_materialize_from_body_cache_replaces_existing_destination(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha", "cached\n")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    destination = tmp_path / "dest" / "alpha"
    destination.mkdir(parents=True)
    (destination / "old.txt").write_text("old\n", encoding="utf-8")

    assert try_materialize_from_skill_body_cache(
        paths,
        content_hash=content_hash,
        skill_name="alpha",
        destination=destination,
        now=datetime(2026, 5, 18, 13, tzinfo=UTC),
    ) is True

    assert not (destination / "old.txt").exists()
    assert (destination / "notes.md").read_text(encoding="utf-8") == "cached\n"


def test_materialize_from_body_cache_removes_cache_when_copied_destination_hash_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    destination = tmp_path / "dest" / "alpha"
    real_hash = source_cache.sha256_skill_directory

    def corrupt_destination_hash(path: Path, *, expected_name: str | None = None) -> str:
        if path == destination:
            return "sha256:" + ("9" * 64)
        return real_hash(path, expected_name=expected_name)

    monkeypatch.setattr(source_cache, "sha256_skill_directory", corrupt_destination_hash)

    assert try_materialize_from_skill_body_cache(
        paths,
        content_hash=content_hash,
        skill_name="alpha",
        destination=destination,
        now=datetime(2026, 5, 18, 13, tzinfo=UTC),
    ) is False

    assert not destination.exists()
    assert not skill_body_cache_path(paths, content_hash).exists()


def test_cache_summary_refuses_symlinked_catalog_file(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.catalog_cache_dir.mkdir(parents=True)
    (tmp_path / "attacker.toml").write_text("owned\n", encoding="utf-8")
    (paths.catalog_cache_dir / "catalog.toml").symlink_to(tmp_path / "attacker.toml")

    with pytest.raises(SvError, match="symlinked sv catalog cache file"):
        cache_summary(paths, now=datetime(2026, 5, 18, 13, tzinfo=UTC))


def test_cache_summary_ignores_non_directory_skill_cache_children(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    root = paths.skill_body_cache_dir / "sha256"
    root.mkdir(parents=True)
    (root / ("1" * 64)).write_text("not a directory\n", encoding="utf-8")

    assert cache_summary(paths, now=datetime(2026, 5, 18, 13, tzinfo=UTC)).skill_bodies == 0


def test_cache_summary_removes_body_entry_with_hash_mismatch(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    (skill_body_cache_path(paths, content_hash) / "skill" / "notes.md").write_text(
        "tampered\n", encoding="utf-8"
    )

    summary = cache_summary(paths, now=datetime(2026, 5, 18, 13, tzinfo=UTC))

    assert summary.skill_bodies == 0
    assert not skill_body_cache_path(paths, content_hash).exists()


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("use_count", "use_count = true", "use_count must be a non-negative integer"),
        ("size_bytes", "size_bytes = -1", "size_bytes must be a non-negative integer"),
        ("created_at", 'created_at = "2026-05-18T12:00:00"', "created_at must be a UTC timestamp ending in Z"),
    ],
)
def test_load_skill_body_metadata_rejects_invalid_fields(
    tmp_path: Path, field: str, replacement: str, message: str
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    metadata_path = skill_body_cache_path(paths, content_hash) / "metadata.toml"
    original_line = next(
        line
        for line in metadata_path.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"{field} = ")
    )
    metadata_path.write_text(
        metadata_path.read_text(encoding="utf-8").replace(original_line, replacement),
        encoding="utf-8",
    )

    with pytest.raises(SvError, match=message):
        load_skill_body_metadata(paths, content_hash)


def test_prune_skill_body_cache_treats_marker_without_last_pruned_at_as_due(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.cache_dir.mkdir(parents=True)
    paths.cache_prune_marker.write_text("schema_version = 1\n", encoding="utf-8")

    prune_skill_body_cache(
        paths,
        now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        force=False,
    )

    assert 'last_pruned_at = "2026-05-18T13:00:00Z"' in paths.cache_prune_marker.read_text(
        encoding="utf-8"
    )


def test_get_catalog_with_cache_warns_when_cache_write_fails_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    refreshed_entry = _source_skill(repo, paths, "alpha")
    warnings: list[str] = []

    monkeypatch.setattr(
        source_cache,
        "save_cached_catalog",
        lambda *args, **kwargs: (_ for _ in ()).throw(SvError("disk full")),
    )

    catalog = get_catalog_with_cache(
        [repo],
        paths,
        policy=CachePolicy.default(),
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
        refresh_catalog=lambda repos: [refreshed_entry],
        warn=warnings.append,
    )

    assert [entry.name for entry in catalog] == ["alpha"]
    assert warnings == [
        "warning: failed to write cached metadata for Org/Skills: disk full"
    ]


def test_get_catalog_with_cache_fails_when_cache_write_error_is_security_sensitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    refreshed_entry = _source_skill(repo, paths, "alpha")

    monkeypatch.setattr(
        source_cache,
        "save_cached_catalog",
        lambda *args, **kwargs: (_ for _ in ()).throw(SvError("symlink attack")),
    )

    with pytest.raises(SvError, match="symlink attack"):
        get_catalog_with_cache(
            [repo],
            paths,
            policy=CachePolicy.default(),
            now=datetime(2026, 5, 18, 12, tzinfo=UTC),
            refresh_catalog=lambda repos: [refreshed_entry],
            warn=lambda message: None,
        )


def test_attach_source_materializers_leaves_non_cache_entries_unchanged(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    calls: list[RepoConfig] = []
    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=paths.source_repo_for(repo.id),
        source_path=paths.source_repo_for(repo.id) / "skills" / "alpha",
        source_relative_path="skills/alpha",
        source_backend="fake",
    )

    attached = attach_source_materializers(
        [entry], [repo], backend_factory=lambda selected_repo: calls.append(selected_repo) or ()
    )

    assert attached == [entry]
    assert calls == []


def test_attach_source_materializers_requires_configured_repo_for_cached_entry(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    entry = _source_skill(repo, paths, "alpha")
    cached_entry = replace(entry, source_backend="cache:github-https-api")

    with pytest.raises(SvError, match="No source repo configured"):
        attach_source_materializers(
            [cached_entry], [], backend_factory=lambda selected_repo: ()
        )


def test_attach_source_materializers_reports_backend_failures(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    cached_entry = replace(_source_skill(repo, paths, "alpha"), source_backend="cache:api")

    class FailingBackend:
        name = "failing"

        def read_file(self, path: str) -> bytes:
            raise SourceBackendError("read", "network down")

        def list_candidate_skill_files(
            self, configured_skills_paths: Sequence[str] = ()
        ) -> Sequence[str]:
            return ()

        def read_index(self) -> bytes | None:
            return None

        def materialize_folder(self, source_path: str, destination: Path) -> None:
            raise SourceBackendError("materialize", "network down")

    attached = attach_source_materializers(
        [cached_entry], [repo], backend_factory=lambda selected_repo: (FailingBackend(),)
    )[0]

    with pytest.raises(SvError, match="failing: network down"):
        attached.materialize_to(tmp_path / "destination" / "alpha")


def test_attach_source_materializers_reports_when_no_backends_available(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    cached_entry = replace(_source_skill(repo, paths, "alpha"), source_backend="cache:api")
    attached = attach_source_materializers(
        [cached_entry], [repo], backend_factory=lambda selected_repo: ()
    )[0]

    with pytest.raises(SvError, match="no source backends were available"):
        attached.materialize_to(tmp_path / "destination" / "alpha")


def test_cached_metadata_materializer_requires_refresh_callback_on_body_miss(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    cached_entry = replace(_source_skill(repo, paths, "alpha"), source_backend="cache:api")
    wrapped = wrap_catalog_with_skill_body_cache(
        [cached_entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: None,
    )[0]

    with pytest.raises(SvError, match="no source refresh was provided"):
        wrapped.materialize_to(tmp_path / "destination" / "alpha")


def test_cached_metadata_materializer_fails_when_refresh_omits_entry(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    cached_entry = replace(_source_skill(repo, paths, "alpha"), source_backend="cache:api")
    wrapped = wrap_catalog_with_skill_body_cache(
        [cached_entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: None,
        refresh_entry_on_body_miss=lambda entry: None,
    )[0]

    with pytest.raises(SvError, match="did not include Org/Skills:alpha"):
        wrapped.materialize_to(tmp_path / "destination" / "alpha")


def test_cache_aware_materializer_warns_when_body_cache_write_fails_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha", "remote\n")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    repo = _repo()
    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=tmp_path / "source",
        source_path=source_skill,
        source_relative_path="skills/alpha",
        source_backend="local-cache",
        source_content_hash=content_hash,
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        source_cache,
        "store_skill_body_cache",
        lambda *args, **kwargs: (_ for _ in ()).throw(SvError("disk full")),
    )
    wrapped = wrap_catalog_with_skill_body_cache(
        [entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: None,
        warn=warnings.append,
    )[0]
    destination = tmp_path / "destination" / "alpha"

    wrapped.materialize_to(destination)

    assert (destination / "notes.md").read_text(encoding="utf-8") == "remote\n"
    assert warnings == [
        "warning: failed to write skill body cache for Org/Skills:alpha: disk full"
    ]


def test_cache_aware_materializer_removes_destination_when_after_store_fails_closed(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha", "remote\n")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    repo = _repo()
    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=tmp_path / "source",
        source_path=source_skill,
        source_relative_path="skills/alpha",
        source_backend="local-cache",
        source_content_hash=content_hash,
    )
    wrapped = wrap_catalog_with_skill_body_cache(
        [entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: (_ for _ in ()).throw(
            SvError("symlink writeback")
        ),
    )[0]
    destination = tmp_path / "destination" / "alpha"

    with pytest.raises(SvError, match="symlink writeback"):
        wrapped.materialize_to(destination)

    assert not destination.exists()


@pytest.mark.parametrize(
    ("toml_text", "message"),
    [
        ('schema_version = true\nskills = []\n', "schema_version must be 1"),
        ('schema_version = 1\nskills = "bad"\n', "skills must be a list"),
        (
            'schema_version = 1\nskills = []\nrefreshed_at = "2026-05-18T12:00:00"\n',
            "refreshed_at must be a UTC timestamp ending in Z",
        ),
        (
            'schema_version = 1\nskills = []\nrefreshed_at = "2026-05-18T12:00:00Z"\n'
            'repo_id = "Org/Skills"\nrepo_url = "https://github.com/Org/Skills.git"\n'
            'source_key = "github:org/skills"\nskills_paths = ["skills", 1]\n'
            'backend = "api"\ncatalog_hash = "sha256:' + ("0" * 64) + '"\n',
            "skills_paths\\[1\\] must be a string",
        ),
        (
            'schema_version = 1\nrefreshed_at = "2026-05-18T12:00:00Z"\n'
            'repo_id = "Org/Skills"\nrepo_url = "https://github.com/Org/Skills.git"\n'
            'source_key = "github:org/skills"\nskills_paths = []\nbackend = "api"\n'
            'catalog_hash = "sha256:' + ("0" * 64) + '"\nskills = [1]\n',
            "skills\\[0\\] must be a table",
        ),
        (
            'schema_version = 1\nrefreshed_at = "2026-05-18T12:00:00Z"\n'
            'repo_id = "Org/Skills"\nrepo_url = "https://github.com/Org/Skills.git"\n'
            'source_key = "github:org/skills"\nskills_paths = []\nbackend = "api"\n'
            'catalog_hash = "sha256:' + ("0" * 64) + '"\n'
            '[[skills]]\nname = "alpha"\ndescription = "Alpha"\nsource_path = "skills/alpha"\n'
            'content_hash = "bad"\n',
            "skills\\[0\\]\\.content_hash must be a sha256 digest",
        ),
    ],
)
def test_load_cached_catalog_rejects_invalid_document_shapes(
    tmp_path: Path, toml_text: str, message: str
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    path = catalog_cache_path(paths, repo)
    path.parent.mkdir(parents=True)
    path.write_text(toml_text, encoding="utf-8")

    with pytest.raises(SvError, match=message):
        load_cached_catalog(paths, repo)


def test_save_cached_catalog_rejects_document_for_different_repo(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    other = RepoConfig(id="Other/Skills", url=repo.url)
    document = _catalog_document("2026-05-18T12:00:00Z")

    with pytest.raises(SvError, match="different repo metadata"):
        save_cached_catalog(paths, other, document)


def test_save_cached_catalog_rejects_unsupported_cache_directory(tmp_path: Path) -> None:
    paths = SvPaths(
        sv_home=tmp_path / "sv-home",
        config_file=tmp_path / "config.toml",
        sources_dir=tmp_path / "sources",
        global_manifest_file=tmp_path / "manifest.toml",
    )

    with pytest.raises(SvError, match="Unsupported path"):
        save_cached_catalog(paths, _repo(), _catalog_document("2026-05-18T12:00:00Z"))


@pytest.mark.parametrize("bad_hash", ["not-a-sha256", "sha256:" + ("1" * 63)])
def test_store_skill_body_cache_rejects_invalid_content_hash(
    tmp_path: Path, bad_hash: str
) -> None:
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")

    with pytest.raises(SvError, match="sha256 digest"):
        store_skill_body_cache(
            SvPaths.from_home(tmp_path),
            source_skill,
            skill_name="alpha",
            content_hash=bad_hash,
            source_reference="Org/Skills:skills/alpha",
            now=datetime(2026, 5, 18, 12, tzinfo=UTC),
        )


def test_store_skill_body_cache_rejects_source_hash_mismatch(tmp_path: Path) -> None:
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")

    with pytest.raises(SvError, match="content hash mismatch"):
        store_skill_body_cache(
            SvPaths.from_home(tmp_path),
            source_skill,
            skill_name="alpha",
            content_hash="sha256:" + ("1" * 64),
            source_reference="Org/Skills:skills/alpha",
            now=datetime(2026, 5, 18, 12, tzinfo=UTC),
        )


def test_store_skill_body_cache_updates_existing_valid_entry(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )

    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 13, tzinfo=UTC),
    )

    metadata = load_skill_body_metadata(paths, content_hash)
    assert metadata.use_count == 2
    assert metadata.last_used_at == "2026-05-18T13:00:00Z"


def test_store_skill_body_cache_removes_invalid_existing_entry_after_stage_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    (skill_body_cache_path(paths, content_hash) / "skill" / "notes.md").write_text(
        "tampered\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        source_cache,
        "copy_skill_folder_to_temp",
        lambda *args, **kwargs: (_ for _ in ()).throw(SvError("disk full")),
    )

    with pytest.raises(SvError, match="disk full"):
        store_skill_body_cache(
            paths,
            source_skill,
            skill_name="alpha",
            content_hash=content_hash,
            source_reference="Org/Skills:skills/alpha",
            now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        )

    assert not skill_body_cache_path(paths, content_hash).exists()


def test_store_skill_body_cache_fails_when_copied_hash_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    real_hash = source_cache.sha256_skill_directory

    def copied_hash_mismatch(path: Path, *, expected_name: str | None = None) -> str:
        if paths.cache_tmp_dir in path.parents:
            return "sha256:" + ("2" * 64)
        return real_hash(path, expected_name=expected_name)

    monkeypatch.setattr(source_cache, "sha256_skill_directory", copied_hash_mismatch)

    with pytest.raises(SvError, match="copied content hash mismatch"):
        store_skill_body_cache(
            paths,
            source_skill,
            skill_name="alpha",
            content_hash=content_hash,
            source_reference="Org/Skills:skills/alpha",
            now=datetime(2026, 5, 18, 12, tzinfo=UTC),
        )


def test_store_skill_body_cache_fails_when_staged_hash_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    real_hash = source_cache.sha256_skill_directory
    copied_calls = 0

    def staged_hash_mismatch(path: Path, *, expected_name: str | None = None) -> str:
        nonlocal copied_calls
        if paths.cache_tmp_dir in path.parents:
            copied_calls += 1
            if copied_calls == 2:
                return "sha256:" + ("3" * 64)
        return real_hash(path, expected_name=expected_name)

    monkeypatch.setattr(source_cache, "sha256_skill_directory", staged_hash_mismatch)

    with pytest.raises(SvError, match="staged content hash mismatch"):
        store_skill_body_cache(
            paths,
            source_skill,
            skill_name="alpha",
            content_hash=content_hash,
            source_reference="Org/Skills:skills/alpha",
            now=datetime(2026, 5, 18, 12, tzinfo=UTC),
        )


def test_skill_body_cache_root_symlink_fails_closed(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    (paths.skill_body_cache_dir / "sha256").mkdir(parents=True)
    target = tmp_path / "attacker-entry"
    target.mkdir()
    skill_body_cache_path(paths, "sha256:" + ("1" * 64)).symlink_to(
        target, target_is_directory=True
    )

    with pytest.raises(SvError, match="symlinked skill body cache"):
        try_materialize_from_skill_body_cache(
            paths,
            content_hash="sha256:" + ("1" * 64),
            skill_name="alpha",
            destination=tmp_path / "dest" / "alpha",
            now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        )


def test_skill_body_cache_skill_name_mismatch_is_treated_as_miss(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )

    assert try_materialize_from_skill_body_cache(
        paths,
        content_hash=content_hash,
        skill_name="beta",
        destination=tmp_path / "dest" / "beta",
        now=datetime(2026, 5, 18, 13, tzinfo=UTC),
    ) is False
    assert not skill_body_cache_path(paths, content_hash).exists()


def test_load_cached_catalog_rejects_oversized_document(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    path = catalog_cache_path(paths, repo)
    path.parent.mkdir(parents=True)
    path.write_text("#" * (source_cache._MAX_CATALOG_CACHE_BYTES + 1), encoding="utf-8")

    with pytest.raises(SvError, match="exceeds size"):
        load_cached_catalog(paths, repo)


def test_get_catalog_with_cache_cache_only_returns_cached_entries(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    save_cached_catalog(paths, repo, _catalog_document("2026-05-18T11:30:00Z"))

    catalog = get_catalog_with_cache(
        [repo],
        paths,
        policy=CachePolicy.cache_only(),
        now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        refresh_catalog=lambda repos: (_ for _ in ()).throw(AssertionError("no refresh")),
        warn=lambda message: None,
    )

    assert [entry.source_backend for entry in catalog] == ["cache:github-https-api"]


def test_get_catalog_with_cache_raises_refresh_failure_without_stale_document(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()

    with pytest.raises(SvError, match="network unavailable"):
        get_catalog_with_cache(
            [repo],
            paths,
            policy=CachePolicy.default(),
            now=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
            refresh_catalog=lambda repos: (_ for _ in ()).throw(SvError("network unavailable")),
            warn=lambda message: None,
        )


def test_record_cached_skill_body_hash_returns_when_catalog_missing(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()

    record_cached_skill_body_hash(
        paths,
        repo,
        _source_skill(repo, paths, "alpha"),
        content_hash="sha256:" + ("4" * 64),
        skill_file_hash="sha256:" + ("5" * 64),
    )

    assert not catalog_cache_path(paths, repo).exists()


def test_cache_only_materializer_fails_when_no_body_hash_is_available(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    entry = replace(_source_skill(repo, paths, "alpha"), source_content_hash=None)
    wrapped = wrap_catalog_with_skill_body_cache(
        [entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: None,
        allow_source_fallback=False,
    )[0]

    with pytest.raises(SvError, match="No cached skill body hash"):
        wrapped.materialize_to(tmp_path / "destination" / "alpha")


def test_catalog_cache_with_skills_paths_round_trips_and_hashes(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(
        id="Org/Skills",
        url="https://github.com/Org/Skills.git",
        skills_paths=("team/skills",),
    )
    entry = _source_skill(repo, paths, "alpha")

    get_catalog_with_cache(
        [repo],
        paths,
        policy=CachePolicy.default(),
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
        refresh_catalog=lambda repos: [entry],
        warn=lambda message: None,
    )

    cached = load_cached_catalog(paths, repo)
    assert cached is not None
    assert cached.skills_paths == ("team/skills",)


@pytest.mark.parametrize(
    ("toml_text", "message"),
    [
        (
            'schema_version = 1\nskills = []\nrefreshed_at = "2026-05-18T12:00:00Z"\n'
            'repo_id = "Org/Skills"\nrepo_url = "https://github.com/Org/Skills.git"\n'
            'source_key = "github:org/skills"\nskills_paths = []\nbackend = "api"\n'
            'catalog_hash = "bad"\n',
            "catalog_hash must be a sha256 digest",
        ),
        (
            'schema_version = 1\nskills = []\nrefreshed_at = "badZ"\n'
            'repo_id = "Org/Skills"\nrepo_url = "https://github.com/Org/Skills.git"\n'
            'source_key = "github:org/skills"\nskills_paths = []\nbackend = "api"\n'
            'catalog_hash = "sha256:' + ("0" * 64) + '"\n',
            "refreshed_at must be a UTC timestamp",
        ),
        (
            'schema_version = 1\nrefreshed_at = "2026-05-18T12:00:00Z"\n'
            'repo_id = "Org/Skills"\nrepo_url = "https://github.com/Org/Skills.git"\n'
            'source_key = "github:org/skills"\nskills_paths = []\nbackend = "api"\n'
            'catalog_hash = "sha256:' + ("0" * 64) + '"\n'
            '[[skills]]\nname = 1\ndescription = "Alpha"\nsource_path = "skills/alpha"\n',
            "skills\\[0\\]\\.name must be a string",
        ),
        (
            'schema_version = 1\nrefreshed_at = "2026-05-18T12:00:00Z"\n'
            'repo_id = "Org/Skills"\nrepo_url = "https://github.com/Org/Skills.git"\n'
            'source_key = "github:org/skills"\nskills_paths = []\nbackend = "api"\n'
            'catalog_hash = "sha256:' + ("0" * 64) + '"\n'
            '[[skills]]\nname = "alpha"\ndescription = "Alpha"\nsource_path = "skills/alpha"\n'
            'skill_file_hash = "bad"\n',
            "skills\\[0\\]\\.skill_file_hash must be a sha256 digest",
        ),
    ],
)
def test_load_cached_catalog_rejects_more_invalid_document_fields(
    tmp_path: Path, toml_text: str, message: str
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    path = catalog_cache_path(paths, repo)
    path.parent.mkdir(parents=True)
    path.write_text(toml_text, encoding="utf-8")

    with pytest.raises(SvError, match=message):
        load_cached_catalog(paths, repo)


@pytest.mark.parametrize(
    ("patched_function", "warning_fragment"),
    [
        ("_touch_skill_body_cache", "touch"),
        ("_prune_after_body_cache_write", "prune"),
    ],
)
def test_materialize_from_body_cache_keeps_valid_hit_when_maintenance_fails_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patched_function: str,
    warning_fragment: str,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha", "cached notes\n")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    destination = tmp_path / "dest" / "alpha"
    warnings: list[str] = []
    monkeypatch.setattr(
        source_cache,
        patched_function,
        lambda *args, **kwargs: (_ for _ in ()).throw(
            SvError("metadata write unavailable")
        ),
    )

    assert try_materialize_from_skill_body_cache(
        paths,
        content_hash=content_hash,
        skill_name="alpha",
        destination=destination,
        now=datetime(2026, 5, 18, 13, tzinfo=UTC),
        warn=warnings.append,
    ) is True

    assert (destination / "notes.md").read_text(encoding="utf-8") == "cached notes\n"
    assert skill_body_cache_path(paths, content_hash).exists()
    assert len(warnings) == 1
    assert "failed skill body cache" in warnings[0]
    assert warning_fragment in warnings[0]
    assert "metadata write unavailable" in warnings[0]


def test_cache_summary_refuses_symlinked_skill_body_root(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.skill_body_cache_dir.mkdir(parents=True)
    target = tmp_path / "attacker-root"
    target.mkdir()
    (paths.skill_body_cache_dir / "sha256").symlink_to(target, target_is_directory=True)

    with pytest.raises(SvError, match="symlinked skill body cache directory"):
        cache_summary(paths, now=datetime(2026, 5, 18, 13, tzinfo=UTC))


def test_cache_summary_refuses_symlinked_skill_folder(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    store_skill_body_cache(
        paths,
        source_skill,
        skill_name="alpha",
        content_hash=content_hash,
        source_reference="Org/Skills:skills/alpha",
        now=datetime(2026, 5, 18, 12, tzinfo=UTC),
    )
    skill_folder = skill_body_cache_path(paths, content_hash) / "skill"
    remove_target = tmp_path / "attacker-skill"
    remove_target.mkdir()
    source_cache.remove_materialization_path(skill_folder)
    skill_folder.symlink_to(remove_target, target_is_directory=True)

    with pytest.raises(SvError, match="symlinked skill body cache folder"):
        cache_summary(paths, now=datetime(2026, 5, 18, 13, tzinfo=UTC))

    assert skill_body_cache_path(paths, content_hash).exists()


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "document must be a table"),
        ({"schema_version": True}, "schema_version must be 1"),
    ],
)
def test_load_skill_body_metadata_rejects_invalid_toml_document_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    document: object,
    message: str,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    content_hash = "sha256:" + ("1" * 64)
    metadata_path = skill_body_cache_path(paths, content_hash) / "metadata.toml"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text("ignored = true\n", encoding="utf-8")
    monkeypatch.setattr(source_cache, "load_toml_document", lambda *args: document)

    with pytest.raises(SvError, match=message):
        load_skill_body_metadata(paths, content_hash)


def test_load_cached_catalog_rejects_non_table_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    path = catalog_cache_path(paths, repo)
    path.parent.mkdir(parents=True)
    path.write_text("ignored = true\n", encoding="utf-8")
    monkeypatch.setattr(source_cache, "load_toml_document", lambda *args: [])

    with pytest.raises(SvError, match="document must be a table"):
        load_cached_catalog(paths, repo)


def test_load_cached_catalog_rejects_skills_paths_that_is_not_a_list(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = _repo()
    path = catalog_cache_path(paths, repo)
    path.parent.mkdir(parents=True)
    path.write_text(
        'schema_version = 1\nskills = []\nrefreshed_at = "2026-05-18T12:00:00Z"\n'
        'repo_id = "Org/Skills"\nrepo_url = "https://github.com/Org/Skills.git"\n'
        'source_key = "github:org/skills"\nskills_paths = "skills"\nbackend = "api"\n'
        'catalog_hash = "sha256:' + ("0" * 64) + '"\n',
        encoding="utf-8",
    )

    with pytest.raises(SvError, match="skills_paths must be a list"):
        load_cached_catalog(paths, repo)


def test_load_cached_catalog_rejects_repo_metadata_field_mismatches(
    tmp_path: Path,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(
        id="Org/Skills",
        url="https://github.com/Org/Skills.git",
        skills_paths=("team/skills",),
    )
    document = CachedCatalogDocument(
        repo_id="Org/Skills",
        repo_url="https://github.com/Other/Skills.git",
        source_key="github:other/skills",
        skills_paths=("other/skills",),
        backend="api",
        refreshed_at="2026-05-18T12:00:00Z",
        catalog_hash="sha256:" + ("0" * 64),
        entries=(),
    )
    path = catalog_cache_path(paths, repo)
    path.parent.mkdir(parents=True)
    path.write_text(
        source_cache._format_cached_catalog_document(
            replace(document, catalog_hash=source_cache._cached_catalog_hash(document))
        ),
        encoding="utf-8",
    )

    with pytest.raises(SvError, match="repo_url, source_key, skills_paths"):
        load_cached_catalog(paths, repo)


def test_private_cache_string_helper_escapes_toml() -> None:
    assert source_cache._toml_string('a"b') == '"a\\"b"'


def test_save_cached_catalog_reports_cache_directory_creation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SvPaths.from_home(tmp_path)
    original_mkdir = Path.mkdir

    def failing_mkdir(
        self: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        if self == paths.catalog_cache_dir:
            raise OSError("permission denied")
        original_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)

    with pytest.raises(SvError, match="Failed to create sv catalog cache directory"):
        save_cached_catalog(paths, _repo(), _catalog_document("2026-05-18T12:00:00Z"))


def test_save_cached_catalog_reports_cache_directory_chmod_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SvPaths.from_home(tmp_path)
    original_chmod = Path.chmod

    def failing_chmod(self: Path, mode: int) -> None:
        if self == paths.cache_dir:
            raise OSError("chmod denied")
        original_chmod(self, mode)

    monkeypatch.setattr(Path, "chmod", failing_chmod)

    with pytest.raises(SvError, match="Failed to restrict sv catalog cache directory"):
        save_cached_catalog(paths, _repo(), _catalog_document("2026-05-18T12:00:00Z"))


def test_reject_symlinked_cache_dir_reports_inspection_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_is_symlink = Path.is_symlink

    def failing_is_symlink(self: Path) -> bool:
        if self == tmp_path:
            raise OSError("stat denied")
        return original_is_symlink(self)

    monkeypatch.setattr(Path, "is_symlink", failing_is_symlink)

    with pytest.raises(SvError, match="Failed to inspect sv catalog cache directory"):
        source_cache._reject_symlinked_cache_dir(tmp_path)


def test_save_cached_catalog_reraises_non_security_write_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SvPaths.from_home(tmp_path)
    monkeypatch.setattr(
        source_cache,
        "atomic_write_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(SvError("disk full")),
    )

    with pytest.raises(SvError, match="disk full"):
        save_cached_catalog(paths, _repo(), _catalog_document("2026-05-18T12:00:00Z"))


def test_cache_aware_materializer_removes_destination_when_body_cache_write_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SvPaths.from_home(tmp_path)
    source_skill = _write_skill_tree(tmp_path / "source", "alpha", "remote\n")
    content_hash = sha256_skill_directory(source_skill, expected_name="alpha")
    repo = _repo()
    entry = SourceSkill(
        name="alpha",
        description="Alpha skill.",
        repo_id=repo.id,
        repo_url=repo.url,
        repo_path=tmp_path / "source",
        source_path=source_skill,
        source_relative_path="skills/alpha",
        source_backend="local-cache",
        source_content_hash=content_hash,
    )
    monkeypatch.setattr(
        source_cache,
        "store_skill_body_cache",
        lambda *args, **kwargs: (_ for _ in ()).throw(SvError("symlink cache")),
    )
    wrapped = wrap_catalog_with_skill_body_cache(
        [entry],
        paths,
        now=lambda: datetime(2026, 5, 18, 13, tzinfo=UTC),
        after_store=lambda entry, content_hash, skill_file_hash: None,
    )[0]
    destination = tmp_path / "destination" / "alpha"

    with pytest.raises(SvError, match="symlink cache"):
        wrapped.materialize_to(destination)

    assert not destination.exists()


def test_should_prune_refuses_symlinked_marker(tmp_path: Path) -> None:
    paths = SvPaths.from_home(tmp_path)
    paths.cache_dir.mkdir(parents=True)
    attacker_marker = tmp_path / "attacker-marker.toml"
    paths.cache_prune_marker.symlink_to(attacker_marker)

    with pytest.raises(SvError, match="symlinked cache prune marker"):
        source_cache._should_prune(
            paths,
            now=datetime(2026, 5, 18, 13, tzinfo=UTC),
            entries=(),
            max_bytes=DEFAULT_SKILL_BODY_MAX_BYTES,
        )


def test_private_cache_parent_refuses_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(SvError, match="symlinked sv catalog cache directory"):
        source_cache._ensure_private_cache_parent(link)


def test_private_cache_parent_reports_creation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_mkdir = Path.mkdir

    def failing_mkdir(
        self: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        if self == tmp_path / "blocked":
            raise OSError("blocked")
        original_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)

    with pytest.raises(SvError, match="Failed to create sv catalog cache directory"):
        source_cache._ensure_private_cache_parent(tmp_path / "blocked")


def test_restrict_private_cache_dir_refuses_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(SvError, match="symlinked sv catalog cache directory"):
        source_cache._restrict_private_cache_dir(link)


def test_restrict_private_cache_dir_reports_unexpected_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "cache"
    directory.mkdir()
    original_stat = Path.stat

    def permissive_stat(
        self: Path, *, follow_symlinks: bool = True
    ) -> os.stat_result:
        result = original_stat(self, follow_symlinks=follow_symlinks)
        if self == directory:
            values = list(result)
            values[0] = stat.S_IFDIR | 0o755
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "stat", permissive_stat)

    with pytest.raises(SvError, match="mode is 0o755"):
        source_cache._restrict_private_cache_dir(directory)

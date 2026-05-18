from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import os
import stat
import time

import pytest

from sv.catalog import SourceSkill
from sv.config import RepoConfig, SvPaths
from sv.errors import SvError
from sv.hashing import sha256_skill_directory
from sv.source_cache import (
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
    skill_body_cache_path,
    store_skill_body_cache,
    try_materialize_from_skill_body_cache,
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

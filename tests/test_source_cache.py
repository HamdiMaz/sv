from __future__ import annotations

from pathlib import Path

from sv.config import SvPaths
from sv.source_cache import (
    CacheMode,
    CachePolicy,
    DEFAULT_METADATA_TTL_SECONDS,
    DEFAULT_SKILL_BODY_MAX_BYTES,
    DEFAULT_SKILL_BODY_MAX_UNUSED_SECONDS,
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

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

DEFAULT_METADATA_TTL_SECONDS = 24 * 60 * 60
DEFAULT_SKILL_BODY_MAX_UNUSED_SECONDS = 30 * 24 * 60 * 60
DEFAULT_SKILL_BODY_MAX_BYTES = 256 * 1024 * 1024
CACHE_SCHEMA_VERSION = 1


class CacheMode(Enum):
    NORMAL = "normal"
    FORCE_REFRESH = "force-refresh"
    CACHE_ONLY = "cache-only"


@dataclass(frozen=True)
class CachePolicy:
    mode: CacheMode = CacheMode.NORMAL
    metadata_ttl_seconds: int = DEFAULT_METADATA_TTL_SECONDS
    allow_stale_on_error: bool = True

    @classmethod
    def default(cls) -> "CachePolicy":
        return cls()

    @classmethod
    def force_refresh(cls, *, allow_stale_on_error: bool = True) -> "CachePolicy":
        return cls(
            mode=CacheMode.FORCE_REFRESH,
            allow_stale_on_error=allow_stale_on_error,
        )

    @classmethod
    def cache_only(cls) -> "CachePolicy":
        return cls(mode=CacheMode.CACHE_ONLY, allow_stale_on_error=False)

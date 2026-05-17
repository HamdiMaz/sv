from __future__ import annotations

SHA256_PREFIX = "sha256:"
SHA256_HEX_LENGTH = 64
SHA256_DIGEST_DESCRIPTION = (
    "sha256 digest in the form sha256:<64 lowercase hex characters>"
)
_HEX_DIGITS = frozenset("0123456789abcdef")


def is_sha256_digest(value: object) -> bool:
    """Return whether value is sv's canonical sha256 digest string."""
    if not isinstance(value, str) or not value.startswith(SHA256_PREFIX):
        return False
    digest = value.removeprefix(SHA256_PREFIX)
    return len(digest) == SHA256_HEX_LENGTH and all(
        char in _HEX_DIGITS for char in digest
    )

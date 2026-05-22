from __future__ import annotations

from pathlib import PurePosixPath
import unicodedata

from sv.errors import SvError


def normalize_executable_metadata_path(path: str) -> str:
    """Validate and normalize executable metadata paths without hiding raw components."""
    if _contains_control_character(path):
        raise SvError(f"Executable metadata path contains control characters: {path!r}.")
    if _contains_unicode_format_character(path):
        raise SvError(
            f"Executable metadata path contains Unicode format characters: {path!r}."
        )
    if path == "" or "" in path.split("/") or any(
        part in {".", ".."} for part in path.split("/")
    ):
        raise SvError(
            f"Executable metadata path contains unsafe path components: {path!r}."
        )
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or "\\" in path:
        raise SvError(
            f"Executable metadata path must be a relative POSIX path: {path!r}."
        )
    if ":" in path:
        raise SvError(
            f"Executable metadata path contains unsupported characters: {path!r}."
        )
    return candidate.as_posix()


def _contains_control_character(value: str) -> bool:
    return any(ord(char) < 0x20 or 0x7F <= ord(char) < 0xA0 for char in value)


def _contains_unicode_format_character(value: str) -> bool:
    return any(unicodedata.category(char) == "Cf" for char in value)

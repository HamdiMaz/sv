from __future__ import annotations

import hashlib
import stat
from pathlib import Path

from sv.errors import SvError
from sv.project import normalize_skill_name

_HASH_PREFIX = "sha256:"
_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    """Return the sha256 content hash for a regular file.

    Symlinked files are rejected so callers do not accidentally hash content outside
    the validated source or project skill tree.
    """
    return f"{_HASH_PREFIX}{_sha256_file_digest(path).hex()}"


def sha256_skill_directory(skill_dir: Path, *, expected_name: str | None = None) -> str:
    """Return a deterministic sha256 hash for a skill directory tree.

    The hash is based on stable POSIX-style paths relative to ``skill_dir``, regular
    file permission bits, and file content hashes. Traversal is sorted by relative
    path so filesystem iteration order does not affect the result.
    """
    _ensure_safe_skill_directory(skill_dir, expected_name=expected_name)
    digest = hashlib.sha256()
    digest.update(b"sv-skill-directory-v1\0")

    for file_path in _iter_skill_files(skill_dir):
        relative_path = _safe_relative_path(file_path, skill_dir)
        mode = _hash_file_mode(file_path)
        file_digest = _sha256_file_digest(file_path)
        digest.update(b"file\0")
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(mode.encode("ascii"))
        digest.update(b"\0")
        digest.update(file_digest)
        digest.update(b"\0")

    return f"{_HASH_PREFIX}{digest.hexdigest()}"


def _sha256_file_digest(path: Path) -> bytes:
    _ensure_regular_file(path)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(_CHUNK_SIZE), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SvError(f"Failed to hash file {path}: {exc}") from exc
    return digest.digest()


def _ensure_safe_skill_directory(skill_dir: Path, *, expected_name: str | None = None) -> None:
    name_to_validate = skill_dir.name if expected_name is None else expected_name
    try:
        normalize_skill_name(name_to_validate)
    except SvError as exc:
        raise SvError(f"Invalid skill directory at {skill_dir}: {exc}") from exc
    _reject_symlinked_ancestors(skill_dir, "Skill directory path")
    if _is_symlink(skill_dir, "skill directory"):
        raise SvError(f"Skill directory '{skill_dir.name}' contains a symlink at {skill_dir}.")
    if not skill_dir.is_dir():
        raise SvError(f"Skill directory '{skill_dir.name}' was not found at {skill_dir}.")


def _iter_skill_files(skill_dir: Path) -> list[Path]:
    try:
        paths = list(skill_dir.rglob("*"))
    except OSError as exc:
        raise SvError(f"Failed to list skill directory {skill_dir}: {exc}") from exc

    files: list[Path] = []
    for path in paths:
        if _is_symlink(path, "skill tree path"):
            raise SvError(f"Skill directory '{skill_dir.name}' contains a symlink at {path}.")
        if path.is_dir():
            _safe_relative_path(path, skill_dir)
            continue
        if path.is_file():
            _safe_relative_path(path, skill_dir)
            files.append(path)
            continue
        raise SvError(f"Skill directory '{skill_dir.name}' contains unsupported path {path}.")
    return sorted(files, key=lambda path: _safe_relative_path(path, skill_dir))


def _ensure_regular_file(path: Path) -> None:
    _reject_symlinked_ancestors(path, "File path")
    if _is_symlink(path, "file"):
        raise SvError(f"File must not be a symlink: {path}.")
    if not path.is_file():
        raise SvError(f"File was not found or is not a regular file: {path}.")


def _safe_relative_path(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise SvError(f"Path {path} is outside skill directory {root}.") from exc
    if relative.is_absolute() or not relative.parts:
        raise SvError(f"Unsafe skill path {path}.")
    for part in relative.parts:
        if part in {"", ".", ".."} or _contains_control_character(part):
            raise SvError(f"Unsafe skill path {path}.")
    return relative.as_posix()


def _reject_symlinked_ancestors(path: Path, label: str) -> None:
    for ancestor in reversed(path.parents):
        if _is_symlink(ancestor, label):
            raise SvError(f"{label} must not contain symlinks: {ancestor}.")


def _is_symlink(path: Path, label: str) -> bool:
    try:
        return path.is_symlink()
    except OSError as exc:
        raise SvError(f"Failed to inspect {label} {path}: {exc}") from exc


def _hash_file_mode(path: Path) -> str:
    """Return the Git-like file mode relevant to portable content hashes."""
    mode = stat.S_IMODE(_stat_path(path, "hash file mode").st_mode)
    return "100755" if mode & 0o111 else "100644"


def _stat_path(path: Path, action: str):
    try:
        return path.stat()
    except OSError as exc:
        raise SvError(f"Failed to {action} for {path}: {exc}") from exc


def _contains_control_character(value: str) -> bool:
    return any(ord(char) < 0x20 or 0x7F <= ord(char) < 0xA0 for char in value)

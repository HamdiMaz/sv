from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any
import os
import tomllib

from sv.errors import SvError

SchemaMigration = Callable[[dict[str, Any]], Mapping[str, Any]]
MAX_TOML_DOCUMENT_BYTES = 1024 * 1024


def load_toml_document(path: Path, document_name: str) -> dict[str, Any]:
    """Load a TOML document with user-facing parse, size, and IO errors."""
    try:
        content = _read_limited_toml_document(path, document_name)
        return tomllib.loads(content.decode("utf-8"))
    except SvError:
        raise
    except UnicodeDecodeError as exc:
        raise SvError(
            f"Failed to read {document_name} at {path}: not valid UTF-8"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise SvError(f"Failed to read {document_name} at {path}: {exc}") from exc


def _read_limited_toml_document(path: Path, document_name: str) -> bytes:
    try:
        size = path.stat().st_size
        if size > MAX_TOML_DOCUMENT_BYTES:
            raise SvError(
                f"Failed to read {document_name} at {path}: document exceeds size "
                f"limit ({MAX_TOML_DOCUMENT_BYTES} bytes)."
            )
        with path.open("rb") as file:
            content = file.read(MAX_TOML_DOCUMENT_BYTES + 1)
    except SvError:
        raise
    except OSError as exc:
        raise SvError(f"Failed to read {document_name} at {path}: {exc}") from exc

    if len(content) > MAX_TOML_DOCUMENT_BYTES:
        raise SvError(
            f"Failed to read {document_name} at {path}: document exceeds size "
            f"limit ({MAX_TOML_DOCUMENT_BYTES} bytes)."
        )
    return content


def require_schema_version(
    data: Mapping[str, Any],
    *,
    path: Path,
    document_name: str,
    current_version: int,
    migrations: Mapping[int, SchemaMigration] | None = None,
    require_present: bool = False,
) -> dict[str, Any]:
    """Validate or safely migrate a simple schema_version field.

    By default, missing schema_version is treated as the current version so
    existing legacy documents can opt in to this helper without a file-format
    rewrite. Set require_present for new versioned document formats that should
    reject unversioned files instead of silently accepting them.
    """
    migrated: dict[str, Any] = dict(data)
    migrations = {} if migrations is None else migrations

    if require_present and "schema_version" not in migrated:
        raise SvError(
            f"Invalid {document_name} at {path}: missing 'schema_version'."
        )

    while True:
        raw_version = migrated.get("schema_version", current_version)
        if not isinstance(raw_version, int) or isinstance(raw_version, bool):
            raise SvError(
                f"Invalid {document_name} at {path}: schema_version must be an integer."
            )

        if raw_version == current_version:
            if "schema_version" not in migrated and "schema_version" in data:
                migrated["schema_version"] = current_version
            return migrated

        if raw_version > current_version:
            raise SvError(
                f"Unsupported {document_name} schema_version {raw_version} at {path}. "
                f"This sv supports schema_version {current_version}; update sv to read this file."
            )

        migration = migrations.get(raw_version)
        if migration is None:
            raise SvError(
                f"Unsupported old {document_name} schema_version {raw_version} at {path}. "
                "No safe migration is available."
            )
        migrated = dict(migration(dict(migrated)))
        if "schema_version" not in migrated:
            raise SvError(
                f"Invalid migration for {document_name} schema_version {raw_version} "
                f"at {path}: migration must set schema_version."
            )
        next_version = migrated["schema_version"]
        if not isinstance(next_version, int) or isinstance(next_version, bool):
            raise SvError(
                f"Invalid {document_name} at {path}: schema_version must be an integer."
            )
        if next_version <= raw_version:
            raise SvError(
                f"Invalid migration for {document_name} schema_version {raw_version} "
                f"at {path}: migration did not advance schema_version."
            )


def toml_escape(value: str) -> str:
    """Escape a value for use inside TOML basic string quotes."""
    escaped: list[str] = []
    replacements = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
    for char in value:
        replacement = replacements.get(char)
        if replacement is not None:
            escaped.append(replacement)
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            escaped.append(f"\\u{ord(char):04x}")
        else:
            escaped.append(char)
    return "".join(escaped)


def atomic_write_text(
    path: Path,
    text: str,
    *,
    document_name: str,
    temp_name: str | None = None,
    temp_path_description: str | None = None,
    create_parent: bool = True,
) -> None:
    """Atomically write UTF-8 text through a same-directory temp file."""
    temp_path = path.with_name(temp_name or f".{path.name}.{os.getpid()}.tmp")
    try:
        _reject_symlinked_toml_path_or_ancestors(path, document_name)
        if create_parent:
            path.parent.mkdir(parents=True, exist_ok=True)
        _reject_symlinked_toml_path_or_ancestors(path, document_name)
        if temp_path.is_symlink():
            temp_description = temp_path_description or document_name
            raise SvError(
                f"Refusing to use symlinked temporary {temp_description} path at {temp_path}."
            )
        if temp_path.exists():
            temp_path.unlink()
        _write_new_file_no_follow(temp_path, text)
        temp_path.replace(path)
    except SvError:
        raise
    except OSError as exc:
        with suppress(OSError):
            if not temp_path.is_symlink():
                temp_path.unlink(missing_ok=True)
        raise SvError(f"Failed to write {document_name} at {path}: {exc}") from exc


def _reject_symlinked_toml_path_or_ancestors(path: Path, document_name: str) -> None:
    for candidate in (*reversed(path.parents), path):
        try:
            if candidate.is_symlink():
                raise SvError(
                    f"Refusing to write symlinked {document_name} path at {candidate}."
                )
        except OSError as exc:
            raise SvError(
                f"Failed to inspect {document_name} path {candidate}: {exc}"
            ) from exc


def _write_new_file_no_follow(path: Path, text: str) -> None:
    if path.is_symlink():
        raise SvError(f"Refusing to use symlinked temporary file at {path}.")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(text)
    except Exception:
        with suppress(OSError):
            path.unlink(missing_ok=True)
        raise

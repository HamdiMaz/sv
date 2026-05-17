from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import shutil
import unicodedata

from sv.errors import SvError
from sv.terminal import escape_terminal_controls

_MAX_MATERIALIZATION_FILES = 1000
_MAX_MATERIALIZATION_BYTES = 10 * 1024 * 1024
_MAX_MATERIALIZATION_DEPTH = 25
_MAX_MATERIALIZATION_ENTRIES = 2000


@dataclass
class _MaterializationStats:
    entries: int = 0
    files: int = 0
    bytes: int = 0


def copy_skill_folder_to_temp(
    source: Path, temp_target: Path, *, error_message: str
) -> Path:
    """Copy a skill folder into a temporary sibling path.

    The final target is not touched by this helper. If the copy fails, the
    temporary directory is removed so callers do not leave a partial skill tree.
    """
    remove_materialization_path(temp_target, ignore_errors=True)
    try:
        validate_materialization_source_tree(source)
        shutil.copytree(source, temp_target, symlinks=True)
        validate_materialization_source_tree(temp_target)
    except SvError:
        remove_materialization_path(temp_target, ignore_errors=True)
        raise
    except OSError as exc:
        remove_materialization_path(temp_target, ignore_errors=True)
        raise SvError(f"{error_message}: {exc}") from exc
    return temp_target


def install_materialized_skill_folder(
    materialized_target: Path,
    target: Path,
    *,
    error_message: str,
    after_install: Callable[[], None] | None = None,
) -> None:
    """Rename a materialized skill folder into a new target atomically.

    If the post-install callback fails, the newly installed target is removed.
    This lets manifest writes happen after filesystem changes while preserving
    the old all-or-nothing behavior for callers.
    """
    try:
        materialized_target.rename(target)
    except OSError as exc:
        remove_materialization_path(materialized_target, ignore_errors=True)
        raise SvError(f"{error_message}: {exc}") from exc

    try:
        if after_install is not None:
            after_install()
    except Exception as exc:
        try:
            remove_materialization_path(target)
        except OSError as rollback_exc:
            failure_label = _callback_failure_label(exc)
            raise SvError(
                f"{error_message}: {failure_label} ({exc}) "
                f"and rollback failed: {rollback_exc}"
            ) from rollback_exc
        raise


def replace_with_materialized_skill_folder(
    materialized_target: Path,
    target: Path,
    backup_target: Path,
    *,
    error_message: str,
    after_replace: Callable[[], None] | None = None,
) -> None:
    """Replace target with an already materialized skill folder.

    The existing target is first renamed to ``backup_target``. If the replacement
    rename or post-replace callback fails, the backup is restored when possible.
    On success, the backup is cleaned up on a best-effort basis.
    """
    remove_materialization_path(backup_target, ignore_errors=True)

    try:
        target.rename(backup_target)
        try:
            materialized_target.rename(target)
        except OSError as replace_exc:
            if target.exists() or target.is_symlink():
                remove_materialization_path(target)
            if backup_target.exists():
                try:
                    backup_target.rename(target)
                except OSError as rollback_exc:
                    raise SvError(
                        f"{error_message}: {replace_exc} "
                        f"and rollback failed: {rollback_exc}"
                    ) from rollback_exc
            raise

        try:
            if after_replace is not None:
                after_replace()
        except Exception as exc:
            try:
                restore_materialization_backup(target, backup_target)
            except OSError as rollback_exc:
                failure_label = _callback_failure_label(exc)
                raise SvError(
                    f"{error_message}: {failure_label} ({exc}) "
                    f"and rollback failed: {rollback_exc}"
                ) from rollback_exc
            raise

        remove_materialization_path(backup_target, ignore_errors=True)
    except OSError as exc:
        remove_materialization_path(materialized_target, ignore_errors=True)
        if backup_target.exists() and not target.exists():
            try:
                backup_target.rename(target)
            except OSError as rollback_exc:
                raise SvError(
                    f"{error_message}: {exc} and rollback failed: {rollback_exc}"
                ) from rollback_exc
        raise SvError(f"{error_message}: {exc}") from exc


def validate_materialization_source_tree(source: Path) -> None:
    _reject_symlinked_materialization_path_or_ancestors(source)
    if not source.is_dir():
        raise SvError(f"Source materialization path is not a directory: {source}.")

    stats = _MaterializationStats()
    try:
        for path in source.rglob("*"):
            if path.is_symlink():
                raise SvError(
                    f"Source materialization path must not contain symlinks; contains a symlink at {path}."
                )
            _record_materialization_entry(source, path, stats)
    except OSError as exc:
        raise SvError(f"Failed to inspect source materialization path {source}: {exc}") from exc


def _reject_symlinked_materialization_path_or_ancestors(source: Path) -> None:
    for path in (*reversed(source.parents), source):
        try:
            if path.is_symlink():
                raise SvError(
                    "Source materialization path must not contain symlinks; "
                    f"contains a symlink at {path}."
                )
        except OSError as exc:
            raise SvError(
                f"Failed to inspect source materialization path {path}: {exc}"
            ) from exc


def _record_materialization_entry(
    source: Path, path: Path, stats: _MaterializationStats
) -> None:
    relative_path = path.relative_to(source)
    _validate_materialization_relative_path(path, relative_path)
    depth = len(relative_path.parts)
    if depth > _MAX_MATERIALIZATION_DEPTH:
        raise SvError(f"Source materialization path exceeds depth limit: {source}.")
    if stats.entries >= _MAX_MATERIALIZATION_ENTRIES:
        raise SvError(f"Source materialization path exceeds entry limit: {source}.")
    stats.entries += 1

    if path.is_dir():
        return
    if not path.is_file():
        raise SvError(f"Source materialization path contains unsupported path {path}.")
    if stats.files >= _MAX_MATERIALIZATION_FILES:
        raise SvError(f"Source materialization path exceeds file limit: {source}.")

    stats.bytes += path.stat().st_size
    if stats.bytes > _MAX_MATERIALIZATION_BYTES:
        raise SvError(f"Source materialization path exceeds byte limit: {source}.")
    stats.files += 1


def _validate_materialization_relative_path(path: Path, relative_path: Path) -> None:
    if relative_path.is_absolute() or not relative_path.parts:
        raise SvError(
            "Source materialization path contains unsafe path name at "
            f"{escape_terminal_controls(path)}."
        )
    for part in relative_path.parts:
        if part in {"", ".", ".."} or _contains_unsafe_path_character(part):
            raise SvError(
                "Source materialization path contains unsafe path name at "
                f"{escape_terminal_controls(path)}."
            )


def _contains_unsafe_path_character(value: str) -> bool:
    return any(
        ord(char) < 0x20
        or 0x7F <= ord(char) < 0xA0
        or unicodedata.category(char) == "Cf"
        for char in value
    )


def restore_materialization_backup(target: Path, backup_target: Path) -> None:
    """Restore a backed-up skill folder over the current target."""
    if target.exists() or target.is_symlink():
        remove_materialization_path(target)
    backup_target.rename(target)


def _callback_failure_label(exc: Exception) -> str:
    if isinstance(exc, SvError):
        return "manifest update failed"
    return "post-operation callback failed"


def remove_materialization_path(path: Path, *, ignore_errors: bool = False) -> None:
    """Remove a temporary or backup materialization path without following symlinks."""
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    except OSError:
        if not ignore_errors:
            raise

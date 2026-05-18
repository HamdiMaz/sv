from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import shutil
import unicodedata
from typing import Protocol

from sv.errors import SvError
from sv.terminal import escape_terminal_controls

_MAX_SOURCE_INDEX_BYTES = 4 * 1024 * 1024
_MAX_SOURCE_SKILL_FILE_BYTES = 1024 * 1024

class SourceBackend(Protocol):
    """Lightweight source access interface for remote skill repositories.

    Implementations should read only metadata, candidate SKILL.md files, or a
    selected folder. They must not silently fall back to a persistent full clone.
    """

    name: str

    def read_file(self, path: str) -> bytes:
        """Read one repository-relative file."""

    def list_candidate_skill_files(
        self, configured_skills_paths: Sequence[str] = ()
    ) -> Sequence[str]:
        """List repository-relative candidate SKILL.md file paths."""

    def read_index(self) -> bytes | None:
        """Read .sv/index.toml when available, otherwise return None."""

    def materialize_folder(self, source_path: str, destination: Path) -> None:
        """Copy one selected repository-relative folder into destination."""


class SourceBackendError(SvError):
    """Failure reported by one lightweight source backend attempt."""

    def __init__(self, operation: str, detail: str, *, hint: str | None = None):
        super().__init__(detail)
        self.operation = operation
        self.detail = _escape_control_characters(detail)
        self.hint = _escape_control_characters(hint) if hint else None


@dataclass(frozen=True)
class SourceBackendFailure:
    repo_id: str
    repo_url: str
    backend: str
    operation: str
    detail: str
    hint: str | None = None

    @classmethod
    def from_error(
        cls,
        *,
        repo_id: str,
        repo_url: str,
        backend: str,
        error: SourceBackendError,
    ) -> "SourceBackendFailure":
        return cls(
            repo_id=repo_id,
            repo_url=repo_url,
            backend=backend,
            operation=error.operation,
            detail=error.detail,
            hint=error.hint,
        )

    @property
    def user_message(self) -> str:
        message = (
            f"{self.backend} backend failed for {self.repo_id} while "
            f"{self.operation}: {self.detail}."
        )
        if self.hint:
            message = f"{message} {self.hint}"
        return message


class FakeSourceBackend:
    """In-memory backend for tests and no-network source discovery fixtures."""

    name = "fake"

    def __init__(self, files: Mapping[str, str | bytes]):
        self._files: dict[str, bytes] = {}
        for path, content in files.items():
            normalized_path = _normalize_backend_relative_path(path)
            if normalized_path in self._files:
                raise SvError(f"Duplicate fake source file path: {path!r}.")
            if isinstance(content, str):
                self._files[normalized_path] = content.encode("utf-8")
            else:
                self._files[normalized_path] = bytes(content)

    def read_file(self, path: str) -> bytes:
        normalized_path = _normalize_backend_relative_path(path)
        try:
            content = self._files[normalized_path]
        except KeyError as exc:
            raise SourceBackendError(
                "reading remote file", f"{normalized_path} was not found"
            ) from exc
        return _enforce_source_metadata_size(
            content,
            normalized_path,
            operation="reading remote file",
        )

    def read_index(self) -> bytes | None:
        content = self._files.get(".sv/index.toml")
        if content is None:
            return None
        return _enforce_source_metadata_size(
            content,
            ".sv/index.toml",
            operation="reading .sv/index.toml",
        )

    def list_candidate_skill_files(
        self, configured_skills_paths: Sequence[str] = ()
    ) -> list[str]:
        configured_roots = tuple(
            _normalize_backend_relative_path(path) for path in configured_skills_paths
        )
        candidates: set[str] = set()
        for path in self._files:
            if not path.endswith("/SKILL.md"):
                continue
            if _is_default_candidate_skill_file(path) or any(
                _is_direct_child_skill_file(path, root) for root in configured_roots
            ):
                candidates.add(path)
        return sorted(candidates)

    def materialize_folder(self, source_path: str, destination: Path) -> None:
        normalized_source_path = _normalize_backend_relative_path(source_path)
        if destination.is_symlink():
            raise SvError(f"Materialization destination must not be a symlink: {destination}.")
        prefix = f"{normalized_source_path}/"
        files_to_write = [
            (path, content)
            for path, content in self._files.items()
            if path.startswith(prefix)
        ]
        if not files_to_write:
            raise SourceBackendError(
                "materializing selected folder",
                f"{normalized_source_path} does not contain files",
            )
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        for path, content in files_to_write:
            relative_path = path.removeprefix(prefix)
            relative = Path(*PurePosixPath(relative_path).parts)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)


def _read_limited_backend_file(
    target: Path, normalized_path: str, *, operation: str
) -> bytes:
    max_bytes, limit_label = _source_metadata_limit_for_path(normalized_path)
    try:
        with target.open("rb") as handle:
            content = handle.read(max_bytes + 1)
    except FileNotFoundError as exc:
        raise SourceBackendError(
            operation, f"{normalized_path} was not found and could not be read"
        ) from exc
    except OSError as exc:
        raise SourceBackendError(
            operation, f"{normalized_path} could not be read: {exc}"
        ) from exc
    return _enforce_source_metadata_size(
        content,
        normalized_path,
        operation=operation,
        max_bytes=max_bytes,
        limit_label=limit_label,
    )


def _enforce_source_metadata_size(
    content: bytes,
    normalized_path: str,
    *,
    operation: str,
    max_bytes: int | None = None,
    limit_label: str | None = None,
) -> bytes:
    if max_bytes is None or limit_label is None:
        max_bytes, limit_label = _source_metadata_limit_for_path(normalized_path)
    if len(content) > max_bytes:
        raise SourceBackendError(
            operation,
            f"{normalized_path} exceeds {limit_label}",
        )
    return content


def _source_metadata_limit_for_path(path: str) -> tuple[int, str]:
    normalized_path = _normalize_backend_relative_path(path)
    if normalized_path == ".sv/index.toml":
        return _MAX_SOURCE_INDEX_BYTES, "source index size limit"
    return _MAX_SOURCE_SKILL_FILE_BYTES, "source metadata size limit"

def _is_direct_child_path(path: str, root: str) -> bool:
    parts = PurePosixPath(path).parts
    root_parts = PurePosixPath(root).parts
    return len(parts) == len(root_parts) + 1 and parts[: len(root_parts)] == root_parts


def _is_path_inside(path: str, root: str) -> bool:
    parts = PurePosixPath(path).parts
    root_parts = PurePosixPath(root).parts
    return len(parts) > len(root_parts) and parts[: len(root_parts)] == root_parts


def _looks_like_not_found(detail: str) -> bool:
    normalized = detail.lower()
    return "not found" in normalized or "http 404" in normalized

def _normalize_backend_relative_path(path: str) -> str:
    if _contains_control_character(path):
        raise SvError(f"Source backend path contains control characters: {path!r}.")
    if _contains_unicode_format_character(path):
        raise SvError(
            f"Source backend path contains Unicode format characters: {path!r}."
        )
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or "\\" in path:
        raise SvError(f"Source backend path must be a relative POSIX path: {path!r}.")
    if ":" in path:
        raise SvError(f"Source backend path contains unsupported characters: {path!r}.")
    parts = candidate.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise SvError(f"Source backend path contains unsafe path components: {path!r}.")
    return candidate.as_posix()


def _contains_control_character(value: str) -> bool:
    return any(ord(char) < 0x20 or 0x7F <= ord(char) < 0xA0 for char in value)


def _contains_unicode_format_character(value: str) -> bool:
    return any(unicodedata.category(char) == "Cf" for char in value)


def _is_default_candidate_skill_file(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md":
        return not parts[1].startswith(".")
    if len(parts) == 4 and parts[1] == "skills" and parts[3] == "SKILL.md":
        return (
            parts[0] != "skills"
            and not parts[0].startswith(".")
            and not parts[2].startswith(".")
        )
    return False


def _is_direct_child_skill_file(path: str, root: str) -> bool:
    parts = PurePosixPath(path).parts
    root_parts = PurePosixPath(root).parts
    if len(parts) != len(root_parts) + 2:
        return False
    if parts[: len(root_parts)] != root_parts:
        return False
    return parts[-1] == "SKILL.md" and not parts[-2].startswith(".")

def _escape_control_characters(value: str) -> str:
    return escape_terminal_controls(value)

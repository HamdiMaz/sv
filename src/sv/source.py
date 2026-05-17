from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path, PurePosixPath
import base64
import binascii
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from typing import Any, Protocol, cast
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import quote, unquote, urlsplit

from sv.config import RepoConfig, SvPaths, repo_source_key
from sv.errors import SvError
from sv.materialization import remove_materialization_path, validate_materialization_source_tree
from sv.terminal import escape_terminal_controls

Runner = Callable[[Sequence[str], Path | None], subprocess.CompletedProcess[str]]

DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 60
_COMMAND_TIMEOUT_EXIT_CODE = 124
_ALLOWED_GIT_PROTOCOLS = "file:https:ssh"
_ALLOWED_REPO_URL_SCHEMES = frozenset(_ALLOWED_GIT_PROTOCOLS.split(":"))
_MAX_GITHUB_MATERIALIZATION_FILES = 1000
_MAX_GITHUB_MATERIALIZATION_ENTRIES = 2000
_MAX_GITHUB_MATERIALIZATION_BYTES = 10 * 1024 * 1024
_MAX_GITHUB_MATERIALIZATION_DEPTH = 25
_MAX_GITHUB_API_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_GITHUB_API_ERROR_BYTES = 64 * 1024
_MAX_SOURCE_INDEX_BYTES = 4 * 1024 * 1024
_MAX_SOURCE_SKILL_FILE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class GitHubHttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


GitHubHttpGet = Callable[[str, Mapping[str, str]], GitHubHttpResponse]

_GITHUB_SHORTHAND = re.compile(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)$")
_GITHUB_HTTPS = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_GITHUB_SSH = re.compile(
    r"^git@github\.com:(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$"
)
_GITHUB_SSH_URL = re.compile(
    r"^ssh://git@github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


@dataclass
class _GitHubMaterializationStats:
    files: int = 0
    entries: int = 0
    bytes: int = 0


@dataclass(frozen=True)
class GitHubRepoRef:
    owner: str
    repo: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


def parse_github_repo_ref(repo_url: str) -> GitHubRepoRef | None:
    """Parse GitHub shorthand/URL forms supported by sv source config."""

    value = repo_url.strip()
    for pattern in (_GITHUB_SHORTHAND, _GITHUB_HTTPS, _GITHUB_SSH, _GITHUB_SSH_URL):
        match = pattern.fullmatch(value)
        if match:
            return GitHubRepoRef(owner=match.group("owner"), repo=match.group("repo"))
    return None


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


class GitHubGhApiBackend:
    """GitHub source backend that reads repository contents via `gh api`."""

    name = "github-gh-api"

    def __init__(self, repo: GitHubRepoRef, runner: Runner | None = None):
        self.repo = repo
        self._runner = default_runner if runner is None else runner
        self._use_bounded_default_runner = runner is None or runner is default_runner

    def read_file(self, path: str) -> bytes:
        return self._read_file(
            path,
            operation="reading remote file",
            max_decoded_bytes=_MAX_SOURCE_SKILL_FILE_BYTES,
            size_limit_label="source metadata size limit",
        )

    def read_index(self) -> bytes | None:
        operation = "reading .sv/index.toml"
        try:
            return self._read_file(
                ".sv/index.toml",
                operation=operation,
                max_decoded_bytes=_MAX_SOURCE_INDEX_BYTES,
                size_limit_label="source index size limit",
            )
        except SourceBackendError as exc:
            if not _looks_like_not_found(exc.detail):
                raise
            self._list_directory("", operation=operation, missing_ok=False)
            return None

    def list_candidate_skill_files(
        self, configured_skills_paths: Sequence[str] = ()
    ) -> list[str]:
        operation = "listing candidate SKILL.md files"
        candidates: set[str] = set()
        root_items = self._list_directory("", operation=operation, missing_ok=False)
        for item in self._list_directory("skills", operation=operation, missing_ok=True):
            if _github_item_type(item) == "dir":
                path = _github_item_path(item, operation)
                parts = PurePosixPath(path).parts
                if (
                    len(parts) == 2
                    and parts[0] == "skills"
                    and not parts[1].startswith(".")
                    and self._skill_directory_has_skill_file(path, operation)
                ):
                    candidates.add(f"{path}/SKILL.md")

        for item in root_items:
            if _github_item_type(item) != "dir":
                continue
            name = _github_item_name(item, operation)
            if name.startswith(".") or name == "skills":
                continue
            try:
                skills_root = _normalize_backend_relative_path(f"{name}/skills")
            except SvError:
                continue
            for skill_item in self._list_directory(
                skills_root, operation=operation, missing_ok=True
            ):
                if _github_item_type(skill_item) != "dir":
                    continue
                skill_path = _github_item_path(skill_item, operation)
                skill_parts = PurePosixPath(skill_path).parts
                if (
                    len(skill_parts) == 3
                    and skill_parts[0] == name
                    and skill_parts[1] == "skills"
                    and not skill_parts[2].startswith(".")
                    and self._skill_directory_has_skill_file(skill_path, operation)
                ):
                    candidates.add(f"{skill_path}/SKILL.md")

        for root in configured_skills_paths:
            normalized_root = _normalize_backend_relative_path(root)
            for item in self._list_directory(
                normalized_root, operation=operation, missing_ok=True
            ):
                if _github_item_type(item) != "dir":
                    continue
                skill_path = _github_item_path(item, operation)
                if _is_direct_child_path(
                    skill_path, normalized_root
                ) and self._skill_directory_has_skill_file(skill_path, operation):
                    candidates.add(f"{skill_path}/SKILL.md")

        return sorted(candidates)

    def _skill_directory_has_skill_file(self, skill_path: str, operation: str) -> bool:
        for item in self._list_directory(skill_path, operation=operation, missing_ok=True):
            if _github_item_type(item) == "file" and _github_item_name(item, operation) == "SKILL.md":
                return True
        return False

    def materialize_folder(self, source_path: str, destination: Path) -> None:
        normalized_source_path = _normalize_backend_relative_path(source_path)
        if destination.is_symlink():
            raise SvError(f"Materialization destination must not be a symlink: {destination}.")
        temp_destination = destination.with_name(f".{destination.name}.sv-github-api-tmp")
        if temp_destination.exists() or temp_destination.is_symlink():
            if temp_destination.is_dir() and not temp_destination.is_symlink():
                shutil.rmtree(temp_destination)
            else:
                temp_destination.unlink()
        temp_destination.mkdir(parents=True)
        stats = _GitHubMaterializationStats()
        try:
            self._materialize_folder_contents(
                normalized_source_path,
                normalized_source_path,
                temp_destination,
                stats,
                depth=0,
            )
            if stats.files == 0:
                raise SourceBackendError(
                    "materializing selected folder",
                    f"{normalized_source_path} does not contain files",
                )
            if destination.exists():
                if destination.is_dir():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            temp_destination.rename(destination)
        except Exception:
            if temp_destination.exists() or temp_destination.is_symlink():
                if temp_destination.is_dir() and not temp_destination.is_symlink():
                    shutil.rmtree(temp_destination)
                else:
                    temp_destination.unlink()
            raise

    def _materialize_folder_contents(
        self,
        folder_path: str,
        source_path: str,
        destination: Path,
        stats: _GitHubMaterializationStats,
        *,
        depth: int,
    ) -> None:
        operation = "materializing selected folder"
        if depth > _MAX_GITHUB_MATERIALIZATION_DEPTH:
            raise SourceBackendError(
                operation,
                f"{folder_path} exceeds GitHub API materialization depth limit",
            )
        source_parts = PurePosixPath(source_path).parts
        for item in self._list_directory(folder_path, operation=operation, missing_ok=False):
            item_type = _github_item_type(item)
            item_path = _github_item_path(item, operation)
            if not _is_path_inside(item_path, folder_path):
                raise SourceBackendError(
                    operation,
                    f"{item_path} is not inside {folder_path}",
                )
            file_parts = PurePosixPath(item_path).parts
            if file_parts[: len(source_parts)] != source_parts:
                raise SourceBackendError(
                    operation,
                    f"{item_path} is not inside {source_path}",
                )
            relative_parts = file_parts[len(source_parts) :]
            if not relative_parts:
                raise SourceBackendError(
                    operation,
                    f"{item_path} is not inside {source_path}",
                )
            stats.entries += 1
            if stats.entries > _MAX_GITHUB_MATERIALIZATION_ENTRIES:
                raise SourceBackendError(
                    operation,
                    f"{source_path} exceeds GitHub API materialization entry limit",
                )
            if item_type == "file":
                if stats.files >= _MAX_GITHUB_MATERIALIZATION_FILES:
                    raise SourceBackendError(
                        operation,
                        f"{source_path} exceeds GitHub API materialization file limit",
                    )
                remaining_bytes = _MAX_GITHUB_MATERIALIZATION_BYTES - stats.bytes
                advertised_size = _github_item_size(item, operation)
                if advertised_size is not None and advertised_size > remaining_bytes:
                    raise SourceBackendError(
                        operation,
                        f"{source_path} exceeds GitHub API materialization byte limit",
                    )
                content = self._read_file(
                    item_path,
                    operation=operation,
                    max_decoded_bytes=remaining_bytes,
                    size_limit_label="GitHub API materialization byte limit",
                )
                if len(content) > remaining_bytes:
                    raise SourceBackendError(
                        operation,
                        f"{source_path} exceeds GitHub API materialization byte limit",
                    )
                target = destination / Path(*relative_parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                stats.files += 1
                stats.bytes += len(content)
            elif item_type == "dir":
                self._materialize_folder_contents(
                    item_path,
                    source_path,
                    destination,
                    stats,
                    depth=depth + 1,
                )
            elif item_type in {"symlink", "submodule"}:
                raise SourceBackendError(
                    operation,
                    f"{item_path} has unsupported GitHub content type {item_type}",
                )
            else:
                raise SourceBackendError(
                    operation,
                    f"{item_path} has unsupported GitHub content type {item_type or 'unknown'}",
                )

    def _read_file(
        self,
        path: str,
        *,
        operation: str,
        max_decoded_bytes: int | None = None,
        size_limit_label: str = "GitHub API file byte limit",
    ) -> bytes:
        normalized_path = _normalize_backend_relative_path(path)
        output = self._run_api(
            [_github_contents_endpoint(self.repo, normalized_path), "--jq", ".content"],
            operation=operation,
        )
        encoded = "".join(output.split())
        if encoded == "null":
            raise SourceBackendError(
                operation,
                f"GitHub API response for {normalized_path} did not contain file content",
            )
        if not encoded:
            self._validate_file_content_encoding(normalized_path, operation)
        if (
            max_decoded_bytes is not None
            and _max_base64_decoded_size(encoded) > max_decoded_bytes
        ):
            raise SourceBackendError(
                operation,
                f"{normalized_path} exceeds {size_limit_label}",
            )
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SourceBackendError(
                operation,
                f"GitHub API response for {normalized_path} did not contain valid base64 content",
            ) from exc
        if max_decoded_bytes is not None and len(content) > max_decoded_bytes:
            raise SourceBackendError(
                operation,
                f"{normalized_path} exceeds {size_limit_label}",
            )
        return content

    def _validate_file_content_encoding(self, normalized_path: str, operation: str) -> None:
        encoding = self._run_api(
            [_github_contents_endpoint(self.repo, normalized_path), "--jq", ".encoding"],
            operation=operation,
        ).strip()
        if encoding != "base64":
            raise SourceBackendError(
                operation,
                f"GitHub API response for {normalized_path} used unsupported file content encoding",
            )

    def _list_directory(
        self, path: str, *, operation: str, missing_ok: bool
    ) -> list[object]:
        normalized_path = "" if path == "" else _normalize_backend_relative_path(path)
        try:
            output = self._run_api(
                [_github_contents_endpoint(self.repo, normalized_path)], operation=operation
            )
        except SourceBackendError as exc:
            if missing_ok and _looks_like_not_found(exc.detail):
                return []
            raise
        if len(output.encode("utf-8")) > _MAX_GITHUB_API_RESPONSE_BYTES:
            raise SourceBackendError(
                operation,
                f"GitHub API response for {normalized_path or 'repository root'} exceeded size limit",
            )
        try:
            data = json.loads(output or "[]")
        except json.JSONDecodeError as exc:
            raise SourceBackendError(
                operation,
                f"GitHub API response for {normalized_path or 'repository root'} was not valid JSON",
            ) from exc
        if not isinstance(data, list):
            raise SourceBackendError(
                operation,
                f"GitHub API response for {normalized_path or 'repository root'} was not a directory listing",
            )
        return data

    def _run_api(self, args: Sequence[str], *, operation: str) -> str:
        command = ["gh", "api", *args]
        try:
            if self._use_bounded_default_runner:
                result = _run_gh_api_with_limited_output(command, operation=operation)
            else:
                result = self._runner(command, None)
        except FileNotFoundError as exc:
            raise SourceBackendError(
                operation,
                "gh was not found on PATH",
                hint="Install GitHub CLI and run 'gh auth login', or set GH_TOKEN, before retrying.",
            ) from exc
        except OSError as exc:
            raise SourceBackendError(
                operation,
                f"gh api failed: {_escape_control_characters(str(exc))}",
            ) from exc
        if result.returncode != 0:
            detail = _escape_control_characters(
                (result.stderr or result.stdout or "").strip()
            )
            if not detail:
                detail = f"gh api exited with status {result.returncode}"
            raise SourceBackendError(operation, detail, hint=_gh_failure_hint(detail))
        output = result.stdout or ""
        if len(output.encode("utf-8")) > _MAX_GITHUB_API_RESPONSE_BYTES:
            raise SourceBackendError(
                operation,
                "GitHub API response exceeded size limit",
            )
        return output


class GitHubHttpsApiBackend(GitHubGhApiBackend):
    """GitHub source backend that reads repository contents via HTTPS API."""

    name = "github-https-api"

    def __init__(
        self,
        repo: GitHubRepoRef,
        *,
        http_get: GitHubHttpGet | None = None,
        env: Mapping[str, str] | None = None,
    ):
        self.repo = repo
        self._http_get = _default_github_http_get if http_get is None else http_get
        self._env = os.environ if env is None else env

    def _validate_file_content_encoding(self, normalized_path: str, operation: str) -> None:
        """HTTPS content extraction already validated encoding before returning content."""

    def _run_api(self, args: Sequence[str], *, operation: str) -> str:
        if not args:
            raise SourceBackendError(operation, "GitHub HTTPS API request was empty")
        endpoint = args[0]
        body = self._request_endpoint(endpoint, operation=operation)
        if len(args) == 1:
            return body
        if len(args) == 3 and args[1] == "--jq" and args[2] == ".content":
            try:
                data = json.loads(body or "{}")
            except json.JSONDecodeError as exc:
                raise SourceBackendError(
                    operation,
                    "GitHub HTTPS API response was not valid JSON",
                ) from exc
            if not isinstance(data, dict) or not isinstance(data.get("content"), str):
                raise SourceBackendError(
                    operation,
                    "GitHub HTTPS API response did not contain file content",
                )
            if "encoding" in data and data.get("encoding") != "base64":
                raise SourceBackendError(
                    operation,
                    "GitHub HTTPS API response used unsupported file content encoding",
                )
            return data["content"]
        raise SourceBackendError(
            operation,
            f"Unsupported GitHub HTTPS API arguments: {' '.join(args)}",
        )

    def _request_endpoint(self, endpoint: str, *, operation: str) -> str:
        if not endpoint.startswith("/"):
            raise SourceBackendError(
                operation,
                f"GitHub HTTPS API endpoint must start with '/': {endpoint!r}",
            )
        url = f"https://api.github.com{endpoint}"
        headers = _github_https_headers(self._env)
        try:
            response = self._http_get(url, headers)
        except OSError as exc:
            raise SourceBackendError(
                operation,
                f"GitHub HTTPS request failed: {_escape_control_characters(str(exc))}",
            ) from exc
        if 200 <= response.status < 300:
            try:
                return response.body.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SourceBackendError(
                    operation,
                    "GitHub HTTPS API response was not valid UTF-8",
                ) from exc
        detail = _github_https_error_detail(response)
        raise SourceBackendError(
            operation,
            detail,
            hint=_github_https_failure_hint(response.status, response.headers, detail),
        )


class GitLocalSourceBackend:
    """Backend wrapper over an already-prepared local Git source cache."""

    name = "git-local"

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path

    def read_file(self, path: str) -> bytes:
        normalized_path = _normalize_backend_relative_path(path)
        target = self.repo_path / Path(*PurePosixPath(normalized_path).parts)
        _reject_symlinked_backend_path_or_ancestors(
            target, self.repo_path, "Source file path"
        )
        return _read_limited_backend_file(
            target,
            normalized_path,
            operation="reading remote file",
        )

    def read_index(self) -> bytes | None:
        normalized_path = ".sv/index.toml"
        target = self.repo_path / ".sv" / "index.toml"
        _reject_symlinked_backend_path_or_ancestors(
            target, self.repo_path, "Source file path"
        )
        try:
            return _read_limited_backend_file(
                target,
                normalized_path,
                operation="reading .sv/index.toml",
            )
        except SourceBackendError as exc:
            if _looks_like_not_found(exc.detail):
                return None
            raise

    def list_candidate_skill_files(
        self, configured_skills_paths: Sequence[str] = ()
    ) -> list[str]:
        candidates: set[str] = set()
        for path in self._candidate_roots(configured_skills_paths):
            root = self.repo_path / Path(*PurePosixPath(path).parts)
            _reject_symlinked_backend_path_or_ancestors(
                root, self.repo_path, "Source skills path"
            )
            if not root.is_dir():
                continue
            try:
                children = sorted(root.iterdir(), key=lambda child: child.name)
            except OSError as exc:
                raise SourceBackendError(
                    "listing candidate SKILL.md files",
                    f"{path} could not be listed: {exc}",
                ) from exc
            for child in children:
                if child.name.startswith(".") or child.is_symlink() or not child.is_dir():
                    continue
                skill_file = f"{path}/{child.name}/SKILL.md"
                if (child / "SKILL.md").is_file() and not (child / "SKILL.md").is_symlink():
                    candidates.add(skill_file)
        return sorted(candidates)

    def materialize_folder(self, source_path: str, destination: Path) -> None:
        normalized_source_path = _normalize_backend_relative_path(source_path)
        source = self.repo_path / Path(*PurePosixPath(normalized_source_path).parts)
        _reject_symlinked_backend_path_or_ancestors(
            source, self.repo_path, "Source skills path"
        )
        if not source.is_dir():
            raise SourceBackendError(
                "materializing selected folder",
                f"{normalized_source_path} does not contain files",
            )
        if destination.is_symlink():
            raise SvError(f"Materialization destination must not be a symlink: {destination}.")
        validate_materialization_source_tree(source)
        if destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        try:
            shutil.copytree(source, destination)
            validate_materialization_source_tree(destination)
        except (OSError, SvError):
            remove_materialization_path(destination, ignore_errors=True)
            raise

    def _candidate_roots(self, configured_skills_paths: Sequence[str]) -> list[str]:
        roots = ["skills"]
        if self.repo_path.is_dir():
            try:
                children = sorted(self.repo_path.iterdir(), key=lambda child: child.name)
            except OSError as exc:
                raise SourceBackendError(
                    "listing candidate SKILL.md files",
                    f"repository root could not be listed: {exc}",
                ) from exc
            for child in children:
                if (
                    child.name.startswith(".")
                    or child.name == "skills"
                    or child.is_symlink()
                    or not child.is_dir()
                ):
                    continue
                try:
                    roots.append(_normalize_backend_relative_path(f"{child.name}/skills"))
                except SvError:
                    continue
        roots.extend(configured_skills_paths)
        normalized_roots: list[str] = []
        for root in roots:
            normalized_root = _normalize_backend_relative_path(root)
            if normalized_root not in normalized_roots:
                normalized_roots.append(normalized_root)
        return normalized_roots


class LocalGitSourceBackend:
    """Direct backend for explicitly configured local Git working-tree sources."""

    name = "git-local-source"

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path

    def read_file(self, path: str) -> bytes:
        operation = "reading remote file"
        self._ensure_available(operation)
        return GitLocalSourceBackend(self.repo_path).read_file(path)

    def read_index(self) -> bytes | None:
        operation = "reading .sv/index.toml"
        self._ensure_available(operation)
        return GitLocalSourceBackend(self.repo_path).read_index()

    def list_candidate_skill_files(
        self, configured_skills_paths: Sequence[str] = ()
    ) -> list[str]:
        operation = "listing candidate SKILL.md files"
        self._ensure_available(operation)
        return GitLocalSourceBackend(self.repo_path).list_candidate_skill_files(
            configured_skills_paths
        )

    def materialize_folder(self, source_path: str, destination: Path) -> None:
        operation = "materializing selected folder"
        self._ensure_available(operation)
        GitLocalSourceBackend(self.repo_path).materialize_folder(source_path, destination)

    def local_path_for(self, source_path: str) -> Path:
        normalized_source_path = _normalize_backend_relative_path(source_path)
        return self.repo_path / Path(*PurePosixPath(normalized_source_path).parts)

    def _ensure_available(self, operation: str) -> None:
        try:
            _reject_symlinked_source_cache_ancestors(self.repo_path)
            _reject_symlinked_source_path(self.repo_path, "Local source repo path")
            _reject_symlinked_source_path(
                self.repo_path / ".git", "Local source Git metadata path"
            )
        except SvError as exc:
            raise SourceBackendError(operation, str(exc)) from exc
        if not self.repo_path.is_dir():
            raise SourceBackendError(
                operation,
                f"local source repo path is not a directory: {self.repo_path}",
            )
        if not (self.repo_path / ".git").exists():
            raise SourceBackendError(
                operation,
                "local source repo path is not a Git working tree: "
                f"{self.repo_path}",
            )


class GitSparseSourceBackend:
    """Lightweight Git fallback using partial clone plus sparse checkout."""

    name = "git-sparse"
    filter_spec = "blob:none"

    def __init__(
        self,
        repo_url: str,
        repo_path: Path,
        runner: Runner | None = None,
        *,
        update: bool = True,
        configured_skills_paths: Sequence[str] = (),
    ):
        self.repo_url = repo_url
        self.repo_path = repo_path
        self._runner = default_runner if runner is None else runner
        self._update = update
        self._configured_skills_paths = tuple(configured_skills_paths)

    def read_file(self, path: str) -> bytes:
        operation = "reading remote file"
        normalized_path = _normalize_backend_relative_path(path)
        self._prepare_checkout(
            [
                *_metadata_sparse_patterns(self._configured_skills_paths),
                _sparse_file_pattern(normalized_path),
            ],
            operation,
        )
        return GitLocalSourceBackend(self.repo_path).read_file(normalized_path)

    def read_index(self) -> bytes | None:
        operation = "reading .sv/index.toml"
        if not self._update and self.repo_path.exists():
            return GitLocalSourceBackend(self.repo_path).read_index()
        self._prepare_checkout([_sparse_file_pattern(".sv/index.toml")], operation)
        return GitLocalSourceBackend(self.repo_path).read_index()

    def list_candidate_skill_files(
        self, configured_skills_paths: Sequence[str] = ()
    ) -> list[str]:
        operation = "listing candidate SKILL.md files"
        if not self._update and self._local_metadata_checkout_available(
            configured_skills_paths
        ):
            return GitLocalSourceBackend(self.repo_path).list_candidate_skill_files(
                configured_skills_paths
            )
        patterns = _metadata_sparse_patterns(configured_skills_paths)
        self._prepare_checkout(patterns, operation)
        return GitLocalSourceBackend(self.repo_path).list_candidate_skill_files(
            configured_skills_paths
        )

    def _local_metadata_checkout_available(
        self, configured_skills_paths: Sequence[str]
    ) -> bool:
        if not self.repo_path.is_dir():
            return False
        if (self.repo_path / "skills").exists():
            return True
        try:
            for child in self.repo_path.iterdir():
                if child.name.startswith(".") or child.name == "skills":
                    continue
                if (child / "skills").exists():
                    return True
        except OSError:
            return False
        for root in configured_skills_paths:
            try:
                normalized_root = _normalize_backend_relative_path(root)
            except SvError:
                continue
            if (self.repo_path / Path(*PurePosixPath(normalized_root).parts)).exists():
                return True
        return False

    def materialize_folder(self, source_path: str, destination: Path) -> None:
        operation = "materializing selected folder"
        normalized_source_path = _normalize_backend_relative_path(source_path)
        self._prepare_checkout([_sparse_folder_pattern(normalized_source_path)], operation)
        copied = False
        try:
            GitLocalSourceBackend(self.repo_path).materialize_folder(
                normalized_source_path, destination
            )
            copied = True
        finally:
            cleanup_errors: list[str] = []
            try:
                _remove_backend_cache_folder(self.repo_path, normalized_source_path)
            except SvError as exc:
                cleanup_errors.append(str(exc))
            try:
                self._prepare_checkout(
                    _metadata_sparse_patterns(self._configured_skills_paths), operation
                )
            except SourceBackendError as exc:
                cleanup_errors.append(exc.detail)
            if copied and cleanup_errors:
                raise SourceBackendError(
                    operation,
                    "failed to restore metadata-only source cache after selected "
                    f"folder materialization: {'; '.join(cleanup_errors)}",
                )

    def _prepare_checkout(self, patterns: Sequence[str], operation: str) -> None:
        repo_existed_before = self.repo_path.exists() or self.repo_path.is_symlink()
        try:
            _ensure_sparse_git_repo(
                self.repo_url,
                self.repo_path,
                self._runner,
                filter_spec=self.filter_spec,
                sparse_patterns=patterns,
                update=self._update,
            )
        except SvError as exc:
            if not repo_existed_before:
                _remove_failed_lightweight_checkout(self.repo_path)
            raise SourceBackendError(
                operation,
                str(exc),
                hint=(
                    "sv did not run a full clone. Fix Git/auth/filter support or try "
                    "another configured source backend."
                ),
            ) from exc


class GitTreelessPartialBackend(GitSparseSourceBackend):
    """Treeless partial Git fallback, preferred before blobless sparse Git."""

    name = "git-treeless-partial"
    filter_spec = "tree:0"


class GitBloblessSparseBackend(GitSparseSourceBackend):
    """Blobless sparse Git fallback used when treeless partial clone fails."""

    name = "git-blobless-sparse"
    filter_spec = "blob:none"


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


def _run_gh_api_with_limited_output(
    command: Sequence[str], *, operation: str
) -> subprocess.CompletedProcess[str]:
    command_list = list(command)
    with tempfile.TemporaryDirectory(prefix="sv-gh-api-") as temp_dir:
        temp_path = Path(temp_dir)
        stdout_path = temp_path / "stdout"
        stderr_path = temp_path / "stderr"
        try:
            with stdout_path.open("w+b") as stdout_file, stderr_path.open(
                "w+b"
            ) as stderr_file:
                result = subprocess.run(
                    command_list,
                    cwd=None,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
                    env=_noninteractive_subprocess_env(),
                )
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(
                args=command_list,
                returncode=_COMMAND_TIMEOUT_EXIT_CODE,
                stdout="",
                stderr=_timeout_error_message(exc),
            )
        stdout = _read_limited_process_output_file(
            stdout_path,
            _MAX_GITHUB_API_RESPONSE_BYTES,
            "GitHub API response",
            operation,
        )
        stderr = _read_limited_process_output_file(
            stderr_path,
            _MAX_GITHUB_API_ERROR_BYTES,
            "GitHub API error output",
            operation,
        )
    return subprocess.CompletedProcess(
        args=command_list,
        returncode=result.returncode,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


def _read_limited_process_output_file(
    path: Path, max_bytes: int, label: str, operation: str
) -> bytes:
    try:
        if path.stat().st_size > max_bytes:
            raise SourceBackendError(operation, f"{label} exceeded size limit")
        return path.read_bytes()
    except OSError as exc:
        raise SourceBackendError(operation, f"Failed to read {label}: {exc}") from exc


def default_runner(
    args: Sequence[str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    command = list(args)
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
            env=_noninteractive_subprocess_env(),
        )
    except subprocess.TimeoutExpired as exc:
        stderr = _timeout_error_message(exc)
        return subprocess.CompletedProcess(
            args=command,
            returncode=_COMMAND_TIMEOUT_EXIT_CODE,
            stdout=_timeout_stream_text(exc.stdout),
            stderr=stderr,
        )
    except FileNotFoundError as exc:
        if args and args[0] == "git":
            raise SvError(_missing_git_guidance()) from exc
        raise


def _noninteractive_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSH_COMMAND"] = _ssh_batch_mode_command(env.get("GIT_SSH_COMMAND"))
    env["GH_PROMPT_DISABLED"] = "1"
    env["GIT_ALLOW_PROTOCOL"] = _ALLOWED_GIT_PROTOCOLS
    return env


def _ssh_batch_mode_command(command: str | None) -> str:
    batch_option = "-o BatchMode=yes"
    if not command:
        return f"ssh {batch_option}"
    if "BatchMode=yes" in command:
        return command
    return f"{command} {batch_option}"


def _timeout_error_message(exc: subprocess.TimeoutExpired) -> str:
    message = f"command timed out after {exc.timeout:g} seconds"
    stderr = _timeout_stream_text(exc.stderr).strip()
    if stderr:
        return f"{message}: {stderr}"
    return message


def _timeout_stream_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def ensure_source_repo(
    repo_url: str,
    repo_path: Path,
    runner: Runner = default_runner,
    *,
    update: bool = True,
    configured_skills_paths: Sequence[str] = (),
) -> None:
    _validate_repo_url(repo_url)
    _reject_symlinked_source_path(repo_path, "Source repo cache path")
    _reject_symlinked_source_cache_ancestors(repo_path)
    _reject_symlinked_source_path(repo_path / ".git", "Source Git metadata path")
    _run_git(["--version"], cwd=None, runner=runner, action="Checking Git availability")

    patterns = _metadata_sparse_patterns(configured_skills_paths)
    repo_existed_before_treeless = repo_path.exists() or repo_path.is_symlink()
    try:
        _ensure_sparse_git_repo_after_git_check(
            repo_url,
            repo_path,
            runner,
            filter_spec="tree:0",
            sparse_patterns=patterns,
            update=update,
        )
    except SvError as treeless_error:
        if repo_existed_before_treeless:
            raise
        _remove_failed_lightweight_checkout(repo_path)
        try:
            _ensure_sparse_git_repo_after_git_check(
                repo_url,
                repo_path,
                runner,
                filter_spec="blob:none",
                sparse_patterns=patterns,
                update=update,
            )
        except SvError as blobless_error:
            _remove_failed_lightweight_checkout(repo_path)
            raise SvError(
                "Lightweight Git source checkout failed without running a full clone. "
                f"Treeless partial Git failed: {treeless_error} "
                f"Blobless sparse Git failed: {blobless_error}"
            ) from blobless_error


def ensure_source_repos(
    repos: Sequence[RepoConfig],
    paths: SvPaths,
    runner: Runner = default_runner,
    *,
    update: bool = True,
) -> list[Path]:
    repo_paths: list[Path] = []
    for repo in repos:
        repo_path = paths.source_repo_for(repo.id)
        reject_symlinked_source_cache_path(repo_path, paths.sources_dir)
        ensure_source_repo(
            repo.url,
            repo_path,
            runner=runner,
            update=update,
            configured_skills_paths=repo.skills_paths,
        )
        repo_paths.append(repo_path)
    return repo_paths


def _ensure_sparse_git_repo(
    repo_url: str,
    repo_path: Path,
    runner: Runner,
    *,
    filter_spec: str,
    sparse_patterns: Sequence[str],
    update: bool,
) -> None:
    _validate_repo_url(repo_url)
    _reject_symlinked_source_path(repo_path, "Source repo cache path")
    _reject_symlinked_source_cache_ancestors(repo_path)
    _reject_symlinked_source_path(repo_path / ".git", "Source Git metadata path")
    _run_git(["--version"], cwd=None, runner=runner, action="Checking Git availability")
    _ensure_sparse_git_repo_after_git_check(
        repo_url,
        repo_path,
        runner,
        filter_spec=filter_spec,
        sparse_patterns=sparse_patterns,
        update=update,
    )


def _ensure_sparse_git_repo_after_git_check(
    repo_url: str,
    repo_path: Path,
    runner: Runner,
    *,
    filter_spec: str,
    sparse_patterns: Sequence[str],
    update: bool,
) -> None:
    normalized_patterns = _normalize_sparse_checkout_patterns(sparse_patterns)
    checkout_ref = "HEAD"
    if repo_path.exists():
        if not (repo_path / ".git").exists():
            raise SvError(
                f"Source path {repo_path} exists but is not a Git clone. Remove it and rerun sv."
            )

        current_remote = _run_git(
            ["remote", "get-url", "origin"],
            cwd=repo_path,
            runner=runner,
            action="Reading source repo remote",
        )
        if current_remote != repo_url and not _same_source_repo(current_remote, repo_url):
            safe_current_remote = _escape_control_characters(current_remote)
            raise SvError(
                f"Configured source repo is {repo_url}, but existing source clone uses {safe_current_remote}. "
                f"Remove {repo_path} and rerun sv."
            )
        if update:
            _run_git(
                ["fetch", "--depth=1", f"--filter={filter_spec}", "origin"],
                cwd=repo_path,
                runner=runner,
                action="Updating lightweight source repo",
                fail_on_ignored_lightweight_options=True,
            )
            checkout_ref = "FETCH_HEAD"
    else:
        try:
            repo_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SvError(
                f"Failed to prepare source cache directory {repo_path.parent}: {exc}"
            ) from exc

        _run_git(
            [
                "clone",
                f"--filter={filter_spec}",
                "--sparse",
                "--no-checkout",
                "--depth=1",
                "--",
                repo_url,
                str(repo_path),
            ],
            cwd=None,
            runner=runner,
            action=f"Preparing lightweight source repo with {filter_spec} filter",
            fail_on_ignored_lightweight_options=True,
        )

    _run_git(
        ["sparse-checkout", "init", "--no-cone"],
        cwd=repo_path,
        runner=runner,
        action="Initializing sparse source checkout",
    )
    _run_git(
        ["sparse-checkout", "set", "--no-cone", *normalized_patterns],
        cwd=repo_path,
        runner=runner,
        action="Selecting sparse source paths",
    )
    _run_git(
        ["checkout", "--force", checkout_ref],
        cwd=repo_path,
        runner=runner,
        action="Materializing sparse source checkout",
    )


def _remove_failed_lightweight_checkout(repo_path: Path) -> None:
    try:
        if repo_path.is_symlink() or repo_path.is_file():
            repo_path.unlink()
        elif repo_path.exists():
            shutil.rmtree(repo_path)
    except OSError as exc:
        raise SvError(
            f"Failed to remove incomplete lightweight source checkout {repo_path}: {exc}"
        ) from exc


def _remove_backend_cache_folder(repo_path: Path, source_path: str) -> None:
    normalized_source_path = _normalize_backend_relative_path(source_path)
    target = repo_path / Path(*PurePosixPath(normalized_source_path).parts)
    _reject_symlinked_backend_path_or_ancestors(
        target, repo_path, "Source skills path"
    )
    try:
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
    except OSError as exc:
        raise SvError(
            f"Failed to remove selected source cache folder {target}: {exc}"
        ) from exc


def _metadata_sparse_patterns(configured_skills_paths: Sequence[str]) -> list[str]:
    patterns = [
        _sparse_file_pattern(".sv/index.toml"),
        "/skills/*/SKILL.md",
        "/*/skills/*/SKILL.md",
        "!/skills/skills/*/SKILL.md",
    ]
    for root in configured_skills_paths:
        normalized_root = _normalize_backend_relative_path(root)
        pattern = f"/{normalized_root}/*/SKILL.md"
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns


def _sparse_file_pattern(path: str) -> str:
    normalized_path = _normalize_backend_relative_path(path)
    return f"/{normalized_path}"


def _sparse_folder_pattern(path: str) -> str:
    normalized_path = _normalize_backend_relative_path(path)
    return f"/{normalized_path}/**"


def _normalize_sparse_checkout_patterns(patterns: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    for pattern in patterns:
        if _contains_control_character(pattern):
            raise SvError(
                f"Sparse checkout pattern contains control characters: {pattern!r}."
            )
        if "\\" in pattern:
            raise SvError(
                f"Sparse checkout pattern must be repo-root relative: {pattern!r}."
            )
        if pattern.startswith("!/"):
            body = pattern[2:]
        elif pattern.startswith("/"):
            body = pattern[1:]
        else:
            raise SvError(
                f"Sparse checkout pattern must be repo-root relative: {pattern!r}."
            )
        if ":" in pattern:
            raise SvError(
                f"Sparse checkout pattern contains unsupported characters: {pattern!r}."
            )
        if body.endswith("/**"):
            body = body[:-3]
        elif body.endswith("/*/SKILL.md"):
            body = body[: -len("/*/SKILL.md")]
        elif body.endswith("/SKILL.md"):
            body = body[: -len("/SKILL.md")]
        if body:
            parts = PurePosixPath(body).parts
            if any(part in {"", ".", ".."} for part in parts):
                raise SvError(
                    "Sparse checkout pattern contains unsafe path components: "
                    f"{pattern!r}."
                )
        if pattern not in normalized:
            normalized.append(pattern)
    return normalized


def source_backends_for_repo(
    repo: RepoConfig,
    paths: SvPaths,
    runner: Runner = default_runner,
    *,
    update: bool = True,
) -> tuple[SourceBackend, ...]:
    """Return lightweight source backends in preference order for a repo."""

    _validate_repo_url(repo.url)
    local_repo_path = _local_source_repo_path(repo.url)
    if local_repo_path is not None:
        return (LocalGitSourceBackend(local_repo_path),)

    backends: list[SourceBackend] = []
    repo_path = paths.source_repo_for(repo.id)
    github_ref = parse_github_repo_ref(repo.url)
    if github_ref is not None:
        backends.append(GitHubGhApiBackend(github_ref, runner=runner))
        backends.append(GitHubHttpsApiBackend(github_ref))
    backends.append(
        GitTreelessPartialBackend(
            repo.url,
            repo_path,
            runner=runner,
            update=update,
            configured_skills_paths=repo.skills_paths,
        )
    )
    backends.append(
        GitBloblessSparseBackend(
            repo.url,
            repo_path,
            runner=runner,
            update=update,
            configured_skills_paths=repo.skills_paths,
        )
    )
    return tuple(backends)


def _local_source_repo_path(repo_url: str) -> Path | None:
    parsed = urlsplit(repo_url)
    scheme = parsed.scheme.lower()
    if scheme == "file":
        if parsed.netloc not in {"", "localhost"}:
            return None
        if not parsed.path:
            return None
        return Path(unquote(parsed.path)).expanduser().resolve()
    if scheme or repo_url.startswith("git@"):
        return None
    candidate = Path(repo_url).expanduser()
    if candidate.is_absolute() or repo_url.startswith((".", "~")) or candidate.exists():
        return candidate.resolve()
    return None


def _github_contents_endpoint(repo: GitHubRepoRef, path: str) -> str:
    endpoint = f"/repos/{quote(repo.owner, safe='')}/{quote(repo.repo, safe='')}/contents"
    if not path:
        return endpoint
    quoted_path = "/".join(quote(part, safe="") for part in PurePosixPath(path).parts)
    return f"{endpoint}/{quoted_path}"


def _default_github_http_get(
    url: str, headers: Mapping[str, str]
) -> GitHubHttpResponse:
    request = urllib_request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib_request.urlopen(request, timeout=15) as response:
            return GitHubHttpResponse(
                status=response.status,
                body=_read_limited_response_body(response),
                headers=dict(response.headers.items()),
            )
    except urllib_error.HTTPError as exc:
        return GitHubHttpResponse(
            status=exc.code,
            body=_read_limited_response_body(exc),
            headers=dict(exc.headers.items()),
        )


def _read_limited_response_body(response: Any) -> bytes:
    body = response.read(_MAX_GITHUB_API_RESPONSE_BYTES + 1)
    if len(body) > _MAX_GITHUB_API_RESPONSE_BYTES:
        raise SourceBackendError(
            "reading GitHub HTTPS API response",
            "GitHub HTTPS API response exceeded size limit",
        )
    return body


def _github_https_headers(env: Mapping[str, str]) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "sv",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _github_https_token(env)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_https_token(env: Mapping[str, str]) -> str | None:
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = env.get(name, "").strip()
        if token:
            return token
    return None


def _github_https_error_detail(response: GitHubHttpResponse) -> str:
    reason = _http_status_phrase(response.status)
    message = _github_https_error_message(response.body)
    detail = f"HTTP {response.status}"
    if reason:
        detail = f"{detail}: {reason}"
    if message and message.lower() != reason.lower():
        detail = f"{detail}: {message}"
    return _escape_control_characters(detail)


def _http_status_phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return ""


def _github_https_error_message(body: bytes) -> str:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        return text.strip()
    if isinstance(data, dict) and isinstance(data.get("message"), str):
        return data["message"].strip()
    return text.strip()


def _github_https_failure_hint(
    status: int, headers: Mapping[str, str], detail: str
) -> str | None:
    normalized = detail.lower()
    rate_limited = "rate limit" in normalized or (
        status == 403 and _header_value(headers, "x-ratelimit-remaining") == "0"
    )
    if rate_limited:
        return (
            "Set GH_TOKEN or GITHUB_TOKEN to raise GitHub API limits; sv will try "
            "Git fallback when available."
        )
    if status in {401, 403} or "authentication" in normalized:
        return (
            "Set GH_TOKEN or GITHUB_TOKEN to access private repos; sv will try "
            "Git fallback when available."
        )
    if status == 404 or "not found" in normalized:
        return (
            "If this is a private repo, set GH_TOKEN or GITHUB_TOKEN; sv will try "
            "Git fallback when available."
        )
    return None


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def _github_item_name(item: object, operation: str) -> str:
    if not isinstance(item, Mapping):
        raise SourceBackendError(operation, "GitHub API directory item was missing a name")
    mapping = cast("Mapping[str, object]", item)
    name = mapping.get("name")
    if not isinstance(name, str):
        raise SourceBackendError(operation, "GitHub API directory item was missing a name")
    return _escape_control_characters(name)


def _github_item_path(item: object, operation: str) -> str:
    if not isinstance(item, Mapping):
        raise SourceBackendError(operation, "GitHub API directory item was missing a path")
    mapping = cast("Mapping[str, object]", item)
    path = mapping.get("path")
    if not isinstance(path, str):
        raise SourceBackendError(operation, "GitHub API directory item was missing a path")
    try:
        return _normalize_backend_relative_path(path)
    except SvError as exc:
        raise SourceBackendError(operation, str(exc)) from exc


def _github_item_type(item: object) -> str | None:
    if not isinstance(item, Mapping):
        return None
    mapping = cast("Mapping[str, object]", item)
    value = mapping.get("type")
    if not isinstance(value, str):
        return None
    return value


def _github_item_size(item: object, operation: str) -> int | None:
    if not isinstance(item, Mapping):
        return None
    mapping = cast("Mapping[str, object]", item)
    value = mapping.get("size")
    if value is None:
        return None
    if not isinstance(value, int) or value < 0:
        raise SourceBackendError(operation, "GitHub API directory item had invalid size")
    return value


def _max_base64_decoded_size(encoded: str) -> int:
    if not encoded:
        return 0
    padding = len(encoded) - len(encoded.rstrip("="))
    return ((len(encoded) + 3) // 4) * 3 - min(padding, 2)


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


def _gh_failure_hint(detail: str) -> str | None:
    normalized = detail.lower()
    if "rate limit" in normalized:
        return "Run 'gh auth login' or set GH_TOKEN to raise GitHub API limits before retrying."
    if "not found" in normalized or "http 404" in normalized:
        return "If this is a private repo, run 'gh auth login' or set GH_TOKEN before retrying."
    if (
        "authentication" in normalized
        or "not logged in" in normalized
        or "gh auth login" in normalized
        or "http 401" in normalized
        or "http 403" in normalized
    ):
        return "Run 'gh auth login' or set GH_TOKEN to access private repos and raise API limits."
    return None


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


def _same_source_repo(left: str, right: str) -> bool:
    try:
        return repo_source_key(left) == repo_source_key(right)
    except ValueError:
        return False


def list_source_skills(repo_path: Path) -> list[str]:
    _reject_symlinked_source_path(repo_path, "Source repo cache path")
    skills_root = repo_path / "skills"
    _reject_symlinked_source_path(skills_root, "Source skills path")
    if not skills_root.is_dir():
        return []

    return sorted(
        path.name
        for path in skills_root.iterdir()
        if not path.is_symlink() and path.is_dir()
    )


def _validate_repo_url(repo_url: str) -> None:
    if repo_url.startswith("-"):
        raise SvError("Invalid source repo: repo cannot start with '-'.")
    if any(ord(char) < 0x20 or 0x7F <= ord(char) < 0xA0 for char in repo_url):
        raise SvError("Invalid source repo: repo cannot contain control characters.")
    parsed = urlsplit(repo_url)
    scheme = parsed.scheme.lower()
    if scheme == "http":
        raise SvError("Invalid source repo: repo URL must use HTTPS instead of cleartext HTTP.")
    if scheme and scheme not in _ALLOWED_REPO_URL_SCHEMES:
        raise SvError("Invalid source repo: repo URL scheme is not supported.")
    if parsed.password is not None:
        raise SvError("Invalid source repo: repo URL cannot contain credentials.")
    if scheme in {"http", "https"} and parsed.username is not None:
        raise SvError("Invalid source repo: repo URL cannot contain credentials.")


def _reject_symlinked_source_cache_ancestors(repo_path: Path) -> None:
    for ancestor in reversed(repo_path.parents):
        try:
            if ancestor.is_symlink():
                raise SvError(
                    f"Source cache path must not contain symlinks: {ancestor}."
                )
        except OSError as exc:
            raise SvError(f"Failed to inspect source path {ancestor}: {exc}") from exc


def reject_symlinked_source_cache_path(repo_path: Path, sources_dir: Path) -> None:
    try:
        relative_parts = repo_path.relative_to(sources_dir).parts
    except ValueError:
        relative_parts = repo_path.parts

    _reject_symlinked_source_path(sources_dir, "Source cache path")
    current = sources_dir
    for part in relative_parts:
        current = current / part
        try:
            if current.is_symlink():
                raise SvError(
                    f"Source cache path must not contain symlinks: {current}."
                )
        except OSError as exc:
            raise SvError(f"Failed to inspect source path {current}: {exc}") from exc


def _reject_symlinked_source_path(path: Path, label: str) -> None:
    try:
        if path.is_symlink():
            raise SvError(f"{label} must not be a symlink: {path}.")
    except OSError as exc:
        raise SvError(f"Failed to inspect source path {path}: {exc}") from exc


def _reject_symlinked_backend_path_or_ancestors(
    path: Path, ancestor: Path, label: str
) -> None:
    _reject_symlinked_source_path(path, label)
    try:
        relative_parts = path.relative_to(ancestor).parts
    except ValueError:
        relative_parts = path.parts
    current = ancestor
    for part in relative_parts:
        current = current / part
        _reject_symlinked_source_path(current, label)


def _run_git(
    args: Sequence[str],
    cwd: Path | None,
    runner: Runner,
    action: str,
    *,
    fail_on_ignored_lightweight_options: bool = False,
) -> str:
    command = ["git", *args]
    try:
        result = runner(command, cwd)
    except FileNotFoundError as exc:
        raise SvError(_missing_git_guidance()) from exc
    except OSError as exc:
        raise SvError(f"{action} failed: {_escape_control_characters(str(exc))}") from exc

    if result.returncode != 0:
        details = _escape_control_characters(
            (result.stderr or result.stdout or "").strip()
        )
        if details:
            raise SvError(f"{action} failed: {details}")
        raise SvError(f"{action} failed with exit code {result.returncode}.")

    if fail_on_ignored_lightweight_options:
        ignored_warning = _ignored_lightweight_option_warning(result.stderr or "")
        if ignored_warning:
            raise SvError(
                f"{action} failed: Git ignored requested lightweight clone options: "
                f"{ignored_warning}"
            )

    return (result.stdout or "").strip()


def _ignored_lightweight_option_warning(stderr: str) -> str | None:
    for line in stderr.splitlines():
        normalized = line.lower()
        ignored_filter = "filter" in normalized and "ignor" in normalized
        ignored_depth = "depth" in normalized and "ignor" in normalized
        if ignored_filter or ignored_depth:
            return _escape_control_characters(line.strip())
    return None


def _missing_git_guidance() -> str:
    return (
        "Git is required but was not found on PATH. Install Git from "
        "https://git-scm.com/downloads or add git to PATH, then retry."
    )


def _escape_control_characters(value: str) -> str:
    return escape_terminal_controls(value)

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path, PurePosixPath
import base64
import binascii
import json
import os
import shutil
import subprocess
import tempfile
from typing import Any, cast
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import quote
import re

from sv.errors import SvError
from sv.path_validation import normalize_executable_metadata_path
from sv.process import (
    DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
    Runner,
    default_runner,
    _COMMAND_TIMEOUT_EXIT_CODE,
    _noninteractive_subprocess_env,
    _timeout_error_message,
)
import sv.source_backends.base as base_module
from sv.source_backends.base import (
    SourceBackendError,
    _escape_control_characters,
    _is_direct_child_path,
    _is_path_inside,
    _looks_like_not_found,
    _normalize_backend_relative_path,
)

_MAX_GITHUB_MATERIALIZATION_FILES = 1000
_MAX_GITHUB_MATERIALIZATION_ENTRIES = 2000
_MAX_GITHUB_MATERIALIZATION_BYTES = 10 * 1024 * 1024
_MAX_GITHUB_MATERIALIZATION_DEPTH = 25
_MAX_GITHUB_API_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_GITHUB_API_ERROR_BYTES = 64 * 1024

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
            max_decoded_bytes=base_module._MAX_SOURCE_SKILL_FILE_BYTES,
            size_limit_label="source metadata size limit",
        )

    def read_index(self) -> bytes | None:
        operation = "reading .sv/index.toml"
        try:
            return self._read_file(
                ".sv/index.toml",
                operation=operation,
                max_decoded_bytes=base_module._MAX_SOURCE_INDEX_BYTES,
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

    def apply_executable_modes(self, source_path: str, destination: Path) -> None:
        executable_paths = self._executable_paths_from_tree(source_path)
        from sv.materialization import apply_skill_file_modes

        apply_skill_file_modes(destination, executable_paths)

    def _executable_paths_from_tree(self, source_path: str) -> tuple[str, ...]:
        operation = "reading GitHub tree modes"
        output = self._run_api([_github_tree_endpoint(self.repo, source_path)], operation=operation)
        try:
            data = json.loads(output or "{}")
        except json.JSONDecodeError as exc:
            raise SourceBackendError(operation, "GitHub tree response was not valid JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("tree"), list):
            raise SourceBackendError(operation, "GitHub tree response was missing tree entries")
        if data.get("truncated") is True:
            raise SourceBackendError(operation, "GitHub tree response was truncated")
        executable_paths: list[str] = []
        for item in data["tree"]:
            if not isinstance(item, Mapping):
                raise SourceBackendError(operation, "GitHub tree item was invalid")
            item_type = item.get("type")
            mode = item.get("mode")
            path = item.get("path")
            if item_type == "tree":
                continue
            if item_type != "blob":
                raise SourceBackendError(operation, f"GitHub tree item had unsupported type {item_type!r}")
            if not isinstance(mode, str) or mode not in {"100644", "100755"}:
                raise SourceBackendError(operation, "GitHub tree item had unsupported file mode")
            if not isinstance(path, str):
                raise SourceBackendError(operation, "GitHub tree item was missing a path")
            try:
                normalized_path = normalize_executable_metadata_path(path)
            except SvError as exc:
                raise SourceBackendError(operation, str(exc)) from exc
            if mode == "100755":
                executable_paths.append(normalized_path)
        return tuple(sorted(dict.fromkeys(executable_paths)))

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

def _github_tree_endpoint(repo: GitHubRepoRef, source_path: str) -> str:
    normalized_path = _normalize_backend_relative_path(source_path)
    tree_ref = quote(f"HEAD:{normalized_path}", safe="")
    return (
        f"/repos/{quote(repo.owner, safe='')}/{quote(repo.repo, safe='')}"
        f"/git/trees/{tree_ref}?recursive=1"
    )


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


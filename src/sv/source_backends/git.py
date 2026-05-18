from __future__ import annotations

from _thread import RLock as RLockType
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
import shutil
import threading
from urllib.parse import unquote, urlsplit

from sv.config import RepoConfig, SvPaths, repo_source_key
from sv.errors import SvError
from sv.materialization import remove_materialization_path, validate_materialization_source_tree
from sv.parallel import map_ordered
from sv.process import Runner, default_runner, _ALLOWED_GIT_PROTOCOLS, _missing_git_guidance
from sv.source_backends.base import (
    SourceBackendError,
    _contains_control_character,
    _escape_control_characters,
    _looks_like_not_found,
    _normalize_backend_relative_path,
    _read_limited_backend_file,
)

_ALLOWED_REPO_URL_SCHEMES = frozenset(_ALLOWED_GIT_PROTOCOLS.split(":"))
_SOURCE_REPO_LOCKS_GUARD = threading.Lock()
_SOURCE_REPO_LOCKS: dict[Path, RLockType] = {}

def _source_repo_lock_key(repo_path: Path) -> Path:
    try:
        return repo_path.resolve()
    except (OSError, RuntimeError):
        return repo_path.absolute()


def _source_repo_lock(repo_path: Path) -> RLockType:
    key = _source_repo_lock_key(repo_path)
    with _SOURCE_REPO_LOCKS_GUARD:
        lock = _SOURCE_REPO_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _SOURCE_REPO_LOCKS[key] = lock
        return lock

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
            shutil.copytree(source, destination, symlinks=True)
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
        with _source_repo_lock(self.repo_path):
            self._materialize_folder_locked(source_path, destination)

    def _materialize_folder_locked(self, source_path: str, destination: Path) -> None:
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
        with _source_repo_lock(self.repo_path):
            self._prepare_checkout_locked(patterns, operation)

    def _prepare_checkout_locked(self, patterns: Sequence[str], operation: str) -> None:
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


def ensure_source_repo(
    repo_url: str,
    repo_path: Path,
    runner: Runner = default_runner,
    *,
    update: bool = True,
    configured_skills_paths: Sequence[str] = (),
) -> None:
    with _source_repo_lock(repo_path):
        _ensure_source_repo_locked(
            repo_url,
            repo_path,
            runner=runner,
            update=update,
            configured_skills_paths=configured_skills_paths,
        )


def _ensure_source_repo_locked(
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
        _ensure_sparse_git_repo_after_git_check_locked(
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
            _ensure_sparse_git_repo_after_git_check_locked(
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
    jobs: int | None = None,
) -> list[Path]:
    repo_jobs = [(repo, paths.source_repo_for(repo.id)) for repo in repos]
    for _, repo_path in repo_jobs:
        reject_symlinked_source_cache_path(repo_path, paths.sources_dir)

    def worker(repo_job: tuple[RepoConfig, Path]) -> Path:
        repo, repo_path = repo_job
        reject_symlinked_source_cache_path(repo_path, paths.sources_dir)
        ensure_source_repo(
            repo.url,
            repo_path,
            runner=runner,
            update=update,
            configured_skills_paths=repo.skills_paths,
        )
        return repo_path

    return map_ordered(repo_jobs, worker, jobs=jobs)


def _ensure_sparse_git_repo(
    repo_url: str,
    repo_path: Path,
    runner: Runner,
    *,
    filter_spec: str,
    sparse_patterns: Sequence[str],
    update: bool,
) -> None:
    with _source_repo_lock(repo_path):
        _ensure_sparse_git_repo_locked(
            repo_url,
            repo_path,
            runner,
            filter_spec=filter_spec,
            sparse_patterns=sparse_patterns,
            update=update,
        )


def _ensure_sparse_git_repo_locked(
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
    _ensure_sparse_git_repo_after_git_check_locked(
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
    with _source_repo_lock(repo_path):
        _ensure_sparse_git_repo_after_git_check_locked(
            repo_url,
            repo_path,
            runner,
            filter_spec=filter_spec,
            sparse_patterns=sparse_patterns,
            update=update,
        )


def _ensure_sparse_git_repo_after_git_check_locked(
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


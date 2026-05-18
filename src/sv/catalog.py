from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
from pathlib import Path, PurePosixPath
import unicodedata

from sv.config import RepoConfig, SvPaths, repo_source_key
from sv.errors import SvError
from sv.parallel import map_ordered
from sv.project import normalize_skill_name
from sv.terminal import escape_terminal_controls
from sv.skills import InvalidSkillError, parse_skill_file, parse_skill_text
from sv.source import (
    LocalGitSourceBackend,
    SourceBackend,
    SourceBackendError,
    SourceBackendFailure,
    reject_symlinked_source_cache_path,
)


@dataclass(frozen=True)
class SourceSkill:
    name: str
    description: str
    repo_id: str
    repo_url: str
    repo_path: Path
    source_path: Path
    source_relative_path: str = ""
    repo_aliases: tuple[str, ...] = ()
    source_backend: str = "local-cache"
    source_content_hash: str | None = None
    source_skill_file_hash: str | None = None
    _materializer: Callable[[Path], None] | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.source_relative_path:
            return
        try:
            relative_path = self.source_path.relative_to(self.repo_path)
        except ValueError:
            relative = f"skills/{self.name}"
        else:
            relative = relative_path.as_posix()
        object.__setattr__(self, "source_relative_path", relative)

    def materialize_to(self, destination: Path) -> None:
        if self._materializer is not None:
            self._materializer(destination)
            return
        from sv.materialization import copy_skill_folder_to_temp

        copy_skill_folder_to_temp(
            self.source_path,
            destination,
            error_message=f"Failed to materialize skill '{self.name}'",
        )

    @property
    def display_label(self) -> str:
        source = self.repo_id
        if not self.is_default_source_path:
            source = f"{source}:{self.source_relative_path}"
        return f"{self.name}  {source}  {self.description}"

    @property
    def qualified_reference(self) -> str:
        skill_reference = (
            self.name if self.is_default_source_path else self.source_relative_path
        )
        return f"{self.repo_id}:{skill_reference}"

    @property
    def is_default_source_path(self) -> bool:
        return self.source_relative_path == f"skills/{self.name}"


@dataclass(frozen=True)
class SourceCatalogResult:
    entries: tuple[SourceSkill, ...]
    failures: tuple[SourceBackendFailure, ...] = ()
    refreshed_repo_ids: tuple[str, ...] = ()
    refreshed_backends_by_repo: dict[str, str] = field(default_factory=dict)
    index_hashes_by_repo: dict[str, str | None] = field(default_factory=dict)

    def failure_report(self) -> str:
        return "\n".join(failure.user_message for failure in self.failures)


@dataclass(frozen=True)
class _BackendCatalogResult:
    entries: list[SourceSkill]
    index_hash: str | None


@dataclass(frozen=True)
class _RepoBackendCatalogResult:
    repo_id: str
    entries: tuple[SourceSkill, ...]
    failures: tuple[SourceBackendFailure, ...]
    refreshed: bool
    refreshed_backend: str | None = None
    index_hash: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _SearchScore:
    score: int
    index: int
    entry: SourceSkill


def search_source_catalog(
    catalog: Sequence[SourceSkill], query: str
) -> list[SourceSkill]:
    """Return source skills matching a query, ordered by lightweight relevance.

    Search covers skill name, description, repo id/aliases, and source path. It
    prefers direct case-insensitive text matches and falls back to simple
    ordered-character fuzzy matches for short abbreviations.
    """

    terms = tuple(term for term in query.casefold().split() if term)
    if not terms:
        return []

    scored: list[_SearchScore] = []
    for index, entry in enumerate(catalog):
        score = _search_entry_score(entry, terms)
        if score is None:
            continue
        scored.append(_SearchScore(score, index, entry))

    return [
        match.entry
        for match in sorted(scored, key=lambda match: (match.score, match.index))
    ]


def _search_entry_score(entry: SourceSkill, terms: Sequence[str]) -> int | None:
    total = 0
    for term in terms:
        term_score = _search_term_score(entry, term)
        if term_score is None:
            return None
        total += term_score
    return total


def _search_term_score(entry: SourceSkill, term: str) -> int | None:
    fields: tuple[tuple[int, str, bool], ...] = (
        (0, entry.name, True),
        (20, entry.description, False),
        (40, entry.repo_id, True),
        *((42, alias, True) for alias in entry.repo_aliases),
        (50, entry.source_relative_path, True),
    )
    scores = [
        field_weight + match_score
        for field_weight, value, allow_fuzzy in fields
        if (match_score := _text_match_score(value, term, allow_fuzzy=allow_fuzzy))
        is not None
    ]
    if not scores:
        return None
    return min(scores)


def _text_match_score(value: str, term: str, *, allow_fuzzy: bool) -> int | None:
    text = value.casefold()
    if term == text:
        return 0
    if text.startswith(term):
        return 2
    position = text.find(term)
    if position >= 0:
        return 6 + min(position, 20)
    fuzzy = _subsequence_match_score(text, term) if allow_fuzzy else None
    if fuzzy is not None:
        return 100 + fuzzy
    return None


def _subsequence_match_score(text: str, term: str) -> int | None:
    if len(term) < 2:
        return None
    search_from = 0
    positions: list[int] = []
    for char in term:
        position = text.find(char, search_from)
        if position < 0:
            return None
        positions.append(position)
        search_from = position + 1
    span = positions[-1] - positions[0] if positions else 0
    gaps = max(span - len(term) + 1, 0)
    return positions[0] + gaps


def _unique_catalog_repos(
    repos: Iterable[RepoConfig],
) -> tuple[list[RepoConfig], dict[str, tuple[str, ...]]]:
    unique_repos: list[RepoConfig] = []
    aliases_by_source: dict[str, tuple[str, ...]] = {}
    seen_repos: dict[str, str] = {}
    seen_sources: set[str] = set()
    for repo in repos:
        source_key = repo_source_key(repo.url)
        existing_url = seen_repos.get(repo.id)
        if existing_url is not None:
            if existing_url == repo.url or repo_source_key(existing_url) == source_key:
                continue
            raise SvError(
                f"Configured source repo id {repo.id!r} is listed more than once with different URLs."
            )
        seen_repos[repo.id] = repo.url
        if source_key in seen_sources:
            aliases_by_source[source_key] = (
                *aliases_by_source[source_key],
                repo.id,
                *repo.aliases,
            )
            continue
        seen_sources.add(source_key)
        aliases_by_source[source_key] = repo.aliases
        unique_repos.append(repo)
    return unique_repos, aliases_by_source


def _catalog_from_one_repo_backends(
    repo: RepoConfig,
    paths: SvPaths,
    repo_aliases: tuple[str, ...],
    backends: Sequence[SourceBackend],
) -> _RepoBackendCatalogResult:
    repo_path = paths.source_repo_for(repo.id)
    reject_symlinked_source_cache_path(repo_path, paths.sources_dir)
    warnings: list[str] = []
    if not backends:
        return _RepoBackendCatalogResult(
            repo_id=repo.id,
            entries=(),
            failures=(
                SourceBackendFailure(
                    repo_id=repo.id,
                    repo_url=repo.url,
                    backend="none",
                    operation="selecting source backend",
                    detail="no lightweight source backend was configured",
                ),
            ),
            refreshed=False,
        )

    failures: list[SourceBackendFailure] = []
    for backend in backends:
        try:
            backend_result = _catalog_entries_from_backend(
                repo,
                repo_path,
                repo_aliases,
                backend,
                warnings.append,
            )
        except SourceBackendError as exc:
            failures.append(
                SourceBackendFailure.from_error(
                    repo_id=repo.id,
                    repo_url=repo.url,
                    backend=backend.name,
                    error=exc,
                )
            )
            if exc.operation == "parsing .sv/index.toml":
                break
            continue
        return _RepoBackendCatalogResult(
            repo_id=repo.id,
            entries=tuple(backend_result.entries),
            failures=tuple(failures),
            refreshed=True,
            refreshed_backend=backend.name,
            index_hash=backend_result.index_hash,
            warnings=tuple(warnings),
        )

    return _RepoBackendCatalogResult(
        repo_id=repo.id,
        entries=(),
        failures=tuple(failures),
        refreshed=False,
        warnings=tuple(warnings),
    )


def build_source_catalog(
    repos: Iterable[RepoConfig],
    paths: SvPaths,
    warn: Callable[[str], None] | None = None,
) -> list[SourceSkill]:
    entries: list[SourceSkill] = []
    unique_repos, aliases_by_source = _unique_catalog_repos(repos)

    for repo in unique_repos:
        source_key = repo_source_key(repo.url)
        repo_path = paths.source_repo_for(repo.id)
        reject_symlinked_source_cache_path(repo_path, paths.sources_dir)
        for skills_root in _candidate_skills_roots(repo_path, repo.skills_paths):
            _reject_symlinked_source_path_or_ancestors(
                skills_root, repo_path, "Source skills path"
            )
            if not skills_root.is_dir():
                continue
            try:
                skill_dirs = sorted(skills_root.iterdir(), key=lambda path: path.name)
            except OSError as exc:
                raise SvError(
                    f"Failed to list source skills in {skills_root}: {exc}"
                ) from exc

            for skill_dir in skill_dirs:
                if skill_dir.is_symlink() or not skill_dir.is_dir():
                    continue
                try:
                    metadata = parse_skill_file(
                        skill_dir / "SKILL.md", expected_folder=skill_dir.name
                    )
                except InvalidSkillError as exc:
                    _warn_invalid_skill(warn, skill_dir, exc)
                    continue
                entries.append(
                    SourceSkill(
                        name=metadata.name,
                        description=metadata.description,
                        repo_id=repo.id,
                        repo_url=repo.url,
                        repo_path=repo_path,
                        source_path=skill_dir,
                        source_relative_path=(
                            skill_dir.relative_to(repo_path).as_posix()
                        ),
                        repo_aliases=aliases_by_source[source_key],
                    )
                )
    return sorted(
        entries,
        key=lambda entry: (entry.name, entry.repo_id, entry.source_relative_path),
    )


def build_source_catalog_from_backends(
    repos: Iterable[RepoConfig],
    paths: SvPaths,
    backends_by_repo: Mapping[str, Sequence[SourceBackend]],
    warn: Callable[[str], None] | None = None,
    *,
    jobs: int | None = None,
) -> SourceCatalogResult:
    """Build a source catalog through lightweight backend interfaces.

    Repos are independent and may refresh concurrently. Backends for one repo are
    still attempted in the configured fallback order.
    """

    unique_repos, aliases_by_source = _unique_catalog_repos(repos)

    def worker(repo: RepoConfig) -> _RepoBackendCatalogResult:
        source_key = repo_source_key(repo.url)
        return _catalog_from_one_repo_backends(
            repo,
            paths,
            aliases_by_source[source_key],
            tuple(backends_by_repo.get(repo.id, ())),
        )

    repo_results = map_ordered(unique_repos, worker, jobs=jobs)

    entries: list[SourceSkill] = []
    failures: list[SourceBackendFailure] = []
    refreshed_repo_ids: list[str] = []
    refreshed_backends_by_repo: dict[str, str] = {}
    index_hashes_by_repo: dict[str, str | None] = {}

    for repo_result in repo_results:
        if warn is not None:
            for message in repo_result.warnings:
                warn(message)
        entries.extend(repo_result.entries)
        failures.extend(repo_result.failures)
        if repo_result.refreshed:
            refreshed_repo_ids.append(repo_result.repo_id)
            if repo_result.refreshed_backend is not None:
                refreshed_backends_by_repo[repo_result.repo_id] = (
                    repo_result.refreshed_backend
                )
            index_hashes_by_repo[repo_result.repo_id] = repo_result.index_hash

    return SourceCatalogResult(
        entries=tuple(
            sorted(
                entries,
                key=lambda entry: (
                    entry.name,
                    entry.repo_id,
                    entry.source_relative_path,
                ),
            )
        ),
        failures=tuple(failures),
        refreshed_repo_ids=tuple(refreshed_repo_ids),
        refreshed_backends_by_repo=refreshed_backends_by_repo,
        index_hashes_by_repo=index_hashes_by_repo,
    )


def _catalog_entries_from_backend(
    repo: RepoConfig,
    repo_path: Path,
    repo_aliases: tuple[str, ...],
    backend: SourceBackend,
    warn: Callable[[str], None] | None,
) -> _BackendCatalogResult:
    index_content = backend.read_index()
    if index_content is not None:
        from sv.index import load_index_bytes

        try:
            index = load_index_bytes(index_content, repo_path / ".sv" / "index.toml")
        except SvError as exc:
            raise SourceBackendError("parsing .sv/index.toml", str(exc)) from exc
        return _BackendCatalogResult(
            entries=_catalog_entries_from_index(
                repo,
                repo_path,
                repo_aliases,
                backend,
                index,
            ),
            index_hash=f"sha256:{hashlib.sha256(index_content).hexdigest()}",
        )

    entries: list[SourceSkill] = []
    for skill_file_path in backend.list_candidate_skill_files(repo.skills_paths):
        try:
            normalized_skill_file_path = normalize_source_relative_path(skill_file_path)
        except SvError as exc:
            raise SourceBackendError(
                "validating candidate SKILL.md file path", str(exc)
            ) from exc
        skill_file_parts = PurePosixPath(normalized_skill_file_path).parts
        if len(skill_file_parts) < 2 or skill_file_parts[-1] != "SKILL.md":
            continue
        expected_folder = skill_file_parts[-2]
        source_relative_path = PurePosixPath(*skill_file_parts[:-1]).as_posix()
        try:
            skill_text = backend.read_file(normalized_skill_file_path).decode("utf-8")
            metadata = parse_skill_text(skill_text, expected_folder=expected_folder)
        except UnicodeDecodeError as exc:
            raise SourceBackendError(
                "reading candidate SKILL.md file",
                f"Failed to read SKILL.md for skill '{expected_folder}': "
                f"{normalized_skill_file_path} is not valid UTF-8",
            ) from exc
        except InvalidSkillError as exc:
            _warn_invalid_skill(warn, PurePosixPath(source_relative_path), exc)
            continue
        source_path, source_root = _source_path_for_backend(
            backend, repo_path, source_relative_path
        )
        _reject_symlinked_source_path_or_ancestors(
            source_path, source_root, "Source skills path"
        )
        entries.append(
            SourceSkill(
                name=metadata.name,
                description=metadata.description,
                repo_id=repo.id,
                repo_url=repo.url,
                repo_path=repo_path,
                source_path=source_path,
                source_relative_path=source_relative_path,
                repo_aliases=repo_aliases,
                source_backend=backend.name,
                _materializer=_backend_materializer(backend, source_relative_path),
            )
        )
    return _BackendCatalogResult(entries=entries, index_hash=None)


def _catalog_entries_from_index(
    repo: RepoConfig,
    repo_path: Path,
    repo_aliases: tuple[str, ...],
    backend: SourceBackend,
    index,
) -> list[SourceSkill]:
    entries: list[SourceSkill] = []
    for index_entry in index.skills:
        source_relative_path = normalize_source_relative_path(index_entry.source_path)
        source_path, source_root = _source_path_for_backend(
            backend, repo_path, source_relative_path
        )
        _reject_symlinked_source_path_or_ancestors(
            source_path, source_root, "Source skills path"
        )
        entries.append(
            SourceSkill(
                name=normalize_skill_name(index_entry.name),
                description=index_entry.description,
                repo_id=repo.id,
                repo_url=repo.url,
                repo_path=repo_path,
                source_path=source_path,
                source_relative_path=source_relative_path,
                repo_aliases=repo_aliases,
                source_backend=backend.name,
                source_content_hash=index_entry.content_hash,
                source_skill_file_hash=index_entry.skill_file_hash,
                _materializer=_backend_materializer(backend, source_relative_path),
            )
        )
    return entries


def _source_path_for_backend(
    backend: SourceBackend, repo_path: Path, source_relative_path: str
) -> tuple[Path, Path]:
    if isinstance(backend, LocalGitSourceBackend):
        return backend.local_path_for(source_relative_path), backend.repo_path
    return repo_path / Path(*PurePosixPath(source_relative_path).parts), repo_path


def _backend_materializer(
    backend: SourceBackend, source_relative_path: str
) -> Callable[[Path], None]:
    def materialize(destination: Path) -> None:
        backend.materialize_folder(source_relative_path, destination)

    return materialize


def _candidate_skills_roots(
    repo_path: Path, configured_skills_paths: Sequence[str]
) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    def add_root(relative_path: str) -> None:
        normalized = normalize_source_relative_path(relative_path)
        if normalized in seen:
            return
        seen.add(normalized)
        roots.append(repo_path / Path(*PurePosixPath(normalized).parts))

    add_root("skills")
    if repo_path.is_dir():
        try:
            children = sorted(repo_path.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise SvError(f"Failed to list source repo {repo_path}: {exc}") from exc
        for child in children:
            if (
                child.name.startswith(".")
                or child.name == "skills"
                or child.is_symlink()
                or not child.is_dir()
            ):
                continue
            try:
                add_root(f"{child.name}/skills")
            except SvError:
                continue

    for configured_path in configured_skills_paths:
        add_root(configured_path)

    return roots


def normalize_source_relative_path(path: str) -> str:
    if _contains_control_character(path):
        raise SvError(f"Source skills path contains control characters: {path!r}.")
    if _contains_unicode_format_character(path):
        raise SvError(
            f"Source skills path contains Unicode format characters: {path!r}."
        )
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or "\\" in path:
        raise SvError(f"Source skills path must be a relative POSIX path: {path!r}.")
    if ":" in path:
        raise SvError(f"Source skills path contains unsupported characters: {path!r}.")
    parts = candidate.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise SvError(f"Source skills path contains unsafe path components: {path!r}.")
    return candidate.as_posix()


def _contains_control_character(value: str) -> bool:
    return any(ord(char) < 0x20 or 0x7F <= ord(char) < 0xA0 for char in value)


def _contains_unicode_format_character(value: str) -> bool:
    return any(unicodedata.category(char) == "Cf" for char in value)


def _warn_invalid_skill(
    warn: Callable[[str], None] | None,
    skill_dir: Path | PurePosixPath,
    error: Exception,
) -> None:
    if warn is None:
        return
    warn(
        "warning: skipping invalid skill at "
        f"{_escape_control_characters(str(skill_dir))}: "
        f"{_escape_control_characters(str(error))}"
    )


def _escape_control_characters(value: str) -> str:
    return escape_terminal_controls(value)


def _reject_symlinked_source_path_or_ancestors(
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
        try:
            if current.is_symlink():
                raise SvError(f"{label} must not contain symlinks: {current}.")
        except OSError as exc:
            raise SvError(f"Failed to inspect source path {current}: {exc}") from exc


def _reject_symlinked_source_path(path: Path, label: str) -> None:
    try:
        if path.is_symlink():
            raise SvError(f"{label} must not be a symlink: {path}.")
    except OSError as exc:
        raise SvError(f"Failed to inspect source path {path}: {exc}") from exc


def find_catalog_matches(
    catalog: Sequence[SourceSkill], skill: str
) -> list[SourceSkill]:
    skill_name = normalize_skill_name(skill)
    return [entry for entry in catalog if entry.name == skill_name]


def find_qualified_catalog_entry(
    catalog: Sequence[SourceSkill], reference: str
) -> SourceSkill | None:
    if ":" not in reference:
        return None
    repo_id, source_reference = reference.rsplit(":", 1)
    if "/" in source_reference:
        source_relative_path = normalize_source_relative_path(source_reference)
        for entry in catalog:
            if entry.source_relative_path == source_relative_path and (
                entry.repo_id == repo_id or repo_id in entry.repo_aliases
            ):
                return entry
        return None

    skill_name = normalize_skill_name(source_reference)
    matches = [
        entry
        for entry in catalog
        if entry.name == skill_name
        and (entry.repo_id == repo_id or repo_id in entry.repo_aliases)
    ]
    if len(matches) == 1:
        return matches[0]
    for entry in matches:
        if entry.is_default_source_path:
            return entry
    return None

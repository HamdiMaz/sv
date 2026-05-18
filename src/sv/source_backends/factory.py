from __future__ import annotations

from sv.config import RepoConfig, SvPaths
from sv.process import Runner, default_runner
from sv.source_backends.base import SourceBackend
from sv.source_backends.github import GitHubGhApiBackend, GitHubHttpsApiBackend, parse_github_repo_ref
from sv.source_backends.git import (
    GitBloblessSparseBackend,
    GitTreelessPartialBackend,
    LocalGitSourceBackend,
    _local_source_repo_path,
    _validate_repo_url,
)

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


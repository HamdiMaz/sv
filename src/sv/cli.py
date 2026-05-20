from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
import inspect
import os
from pathlib import Path
import shutil
import sys
from typing import Any
import unicodedata

from sv.agents import PiAdapter
from sv.catalog import (
    SourceSkill,
    build_source_catalog,
    build_source_catalog_from_backends,
    find_catalog_matches,
    find_qualified_catalog_entry,
    normalize_source_relative_path,
    search_source_catalog,
)
from sv.config import (
    RepoChangeResult,
    RepoConfig,
    SvConfig,
    SvPaths,
    recommended_sources,
    repo_source_key,
)
from sv.errors import SvError
from sv.hashformat import SHA256_PREFIX, is_sha256_digest
from sv.index import (
    IndexDocument,
    IndexKind,
    IndexSkillEntry,
    index_path,
    load_index,
    load_index_scan_config,
    readme_path,
    readme_skill_table_is_fresh,
    save_index,
    scan_repo_for_index,
    update_readme_skill_table,
    validate_index_scan_config_paths,
)
from sv.manifest import (
    GlobalSourceState,
    ManifestEntry,
    project_manifest_path,
)
from sv.parallel import configured_jobs, map_ordered
from sv.process import default_process_runner, default_runner
from sv.project import (
    AddSkillResult,
    RemoveSkillResult,
    SyncResult,
    add_all_project_skills,
    add_all_vault_skills,
    add_project_skill,
    add_vault_skill,
    normalize_skill_name,
    refresh_project_skill_local_states,
    refresh_project_skill_states,
    refresh_vault_skill_local_states,
    refresh_vault_skill_states,
    remove_project_skill,
    remove_vault_skill,
    sync_project_skills,
    sync_vault_skills,
    update_project_skills,
    update_vault_skills,
    validate_project_skills_for_run,
)
from sv.source_backends.factory import source_backends_for_repo
from sv.stores import ConfigStore, GlobalManifestStore, ProjectManifestStore
from sv.source_backends.git import ensure_source_repo, reject_symlinked_source_cache_path
from sv.source_cache import (
    cache_summary,
    clean_cache,
    CacheMode,
    CachePolicy,
    CacheRefreshResult,
    get_catalog_with_cache,
    record_cached_skill_body_hash,
    wrap_catalog_with_skill_body_cache,
)
from sv.runtime import Runtime
from sv.terminal import escape_terminal_controls
from sv.tomlutil import load_toml_document
from sv.ui import (
    CellWidths,
    browse_tty_table,
    format_plain_table as format_table,
    select_tty_items as select_skills,
)

SkillSelector = Callable[..., list[Any]]
SkillChooser = Callable[[Sequence[SourceSkill]], SourceSkill | None]
_PROJECT_STATE_IN_GLOBAL_MANIFEST = "must not contain project skill state"
_TTY_DETAIL_RESET = "\x1b[0m"
_TTY_DETAIL_BOLD = "\x1b[1m"
_TTY_DETAIL_HEADER = "\x1b[38;5;183m"
_TTY_DETAIL_RULE = "\x1b[38;5;60m"


@dataclass(frozen=True)
class LocalContext:
    repo_root: Path
    index_kind: str | None = None

    @property
    def is_skill_vault(self) -> bool:
        return self.index_kind == "skill-vault"

    @property
    def should_refresh_index(self) -> bool:
        return self.index_kind is not None

    @property
    def vault_skills_dir(self) -> Path:
        return self.repo_root / "skills"


def _add_cache_policy_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--refresh",
        action="store_true",
        help="Force refresh source metadata before using the cache.",
    )
    group.add_argument(
        "--cached",
        action="store_true",
        help="Use cached source metadata only and do not refresh sources.",
    )


def _cache_policy_from_args(
    args: argparse.Namespace,
    *,
    default_mode: CacheMode = CacheMode.NORMAL,
    allow_stale_on_error: bool = True,
) -> CachePolicy:
    if getattr(args, "refresh", False):
        return CachePolicy.force_refresh(allow_stale_on_error=allow_stale_on_error)
    if getattr(args, "cached", False):
        return CachePolicy.cache_only()
    return CachePolicy(mode=default_mode, allow_stale_on_error=allow_stale_on_error)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sv",
        description=(
            "Manage project-local AI agent skills. Also supports skill-vault "
            "repositories and global source repos."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list",
        help="List skills available in configured source repos.",
        description=(
            "List skills available in configured source repos. In a TTY this opens "
            "an interactive browser; otherwise it prints a plain table."
        ),
    )
    _add_cache_policy_args(list_parser)
    search_parser = subparsers.add_parser(
        "search",
        help="Search skills available in configured source repos.",
        description="Search source skill names, descriptions, repo ids, and source paths.",
    )
    search_parser.add_argument("query", help="Search query.")
    _add_cache_policy_args(search_parser)
    init_parser = subparsers.add_parser(
        "init",
        help="Create a skill-vault repository scaffold.",
        description="Create a skill-vault repository scaffold.",
    )
    init_parser.add_argument(
        "folder",
        nargs="?",
        help="Target folder to initialize. Defaults to the current directory.",
    )
    index_parser = subparsers.add_parser(
        "index",
        help="Scan this repo for skills and write .sv/index.toml.",
        description=(
            "Scan this repo for valid skills, warn about invalid skills, and write "
            ".sv/index.toml. Skill-vault repos also refresh the generated README "
            "skill table."
        ),
    )
    index_parser.add_argument(
        "--include",
        action="append",
        default=[],
        dest="include_paths",
        metavar="PATH",
        help="Repo-relative path to scan. Can be repeated.",
    )
    index_parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        dest="exclude_paths",
        metavar="PATH",
        help="Repo-relative path to skip. Can be repeated.",
    )
    status_parser = subparsers.add_parser(
        "status",
        help="Show sv-managed skill status.",
        description=(
            "Show sv-managed skill status for the current project, skill-vault, or global source context.\n"
            "Includes modified, update-available, and orphan states when available."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_cache_policy_args(status_parser)

    add_parser = subparsers.add_parser(
        "add",
        help="Add source skills to this project or skill-vault.",
        description=(
            "Add one or more source skills. In normal projects, skills are installed "
            "to this project's .pi/skills directory. In a skill-vault repo, skills "
            "are installed to skills/<name>."
        ),
        epilog=(
            "Examples:\n"
            "  sv add find-docs\n"
            "  sv add HamdiMaz/Skills:find-docs\n"
            "  sv add -l\n"
            "  sv add --all\n"
            "  sv add --all --repo HamdiMaz/Skills\n\n"
            "Use repo:skill, or repo:path/to/skill for same-repo duplicates, to "
            "choose a source when multiple entries provide the same skill name."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_cache_policy_args(add_parser)
    add_parser.add_argument(
        "skill",
        nargs="?",
        help="Skill name or repo:skill reference; also accepts repo:path/to/skill.",
    )
    add_parser.add_argument(
        "--all", action="store_true", help="Add every skill from source repos."
    )
    add_parser.add_argument(
        "--repo",
        dest="repo_id",
        help="Repo ID to install from when used with --all.",
    )
    add_parser.add_argument(
        "-l",
        "--list",
        dest="interactive",
        action="store_true",
        help="Choose skills from an interactive list.",
    )
    add_parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace existing skill-vault targets. Required for non-TTY vault replacement.",
    )

    remove_parser = subparsers.add_parser(
        "remove",
        help="Remove sv-managed local skills.",
        description=(
            "Remove sv-managed local skills. In normal projects this removes Pi "
            "skills from .pi/skills; in skill-vault repos this removes vault skills "
            "from skills/<name>. Manual/unmanaged skills are not removed by --all."
        ),
    )
    remove_parser.add_argument(
        "skill", nargs="?", help="Skill name to remove."
    )
    remove_parser.add_argument(
        "-l",
        "--list",
        dest="interactive",
        action="store_true",
        help="Choose sv-managed skills from an interactive list.",
    )
    remove_parser.add_argument(
        "--all",
        action="store_true",
        help="Remove every sv-managed local skill.",
    )
    remove_parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm selected or bulk removal without prompting.",
    )

    sync_parser = subparsers.add_parser(
        "sync",
        help="Force-sync sv-managed skills from source repos.",
        description=(
            "Force-sync sv-managed project or vault skills from configured source "
            "repos, replacing local edits with the latest source content."
        ),
    )
    _add_cache_policy_args(sync_parser)
    update_parser = subparsers.add_parser(
        "update",
        help="Update sources and unchanged sv-managed skills.",
        description=(
            "Update source metadata and update sv-managed project or vault skills "
            "only when local files have not been modified."
        ),
    )
    _add_cache_policy_args(update_parser)

    run_parser = subparsers.add_parser(
        "run", help="Run Pi with only project skills enabled."
    )
    run_parser.add_argument(
        "pi_args", nargs=argparse.REMAINDER, help="Arguments forwarded to pi after --."
    )

    cache_parser = subparsers.add_parser(
        "cache",
        help="Inspect or clean the global sv cache.",
        description="Inspect or clean the global sv cache.",
    )
    cache_subparsers = cache_parser.add_subparsers(
        dest="cache_command", required=True
    )
    cache_subparsers.add_parser("status", help="Show global cache usage.")
    cache_subparsers.add_parser(
        "clean", help="Prune expired and over-budget cached skill bodies."
    )

    repo_parser = subparsers.add_parser(
        "repo",
        help="Manage global skill source repos.",
        description=(
            "Manage global skill source repos. Use 'sv repo list' or 'sv repo -l' "
            "to browse repos in a TTY; in non-TTY both print a plain repo table."
        ),
    )
    repo_parser.add_argument(
        "-l",
        "--list",
        dest="repo_list_alias",
        action="store_true",
        help="Alias for 'sv repo list'; browse configured skill source repos in a TTY.",
    )
    repo_subparsers = repo_parser.add_subparsers(dest="repo_command", required=False)
    repo_add_parser = repo_subparsers.add_parser(
        "add",
        help="Add a skill source repo.",
        description="Add a skill source repo. Also available as svx <repo>.",
    )
    repo_add_parser.add_argument(
        "repo", help="GitHub owner/repo, Git URL, or local Git repo path."
    )
    repo_add_parser.add_argument(
        "--skills-path",
        dest="skills_paths",
        action="append",
        default=[],
        help="Relative POSIX path to a skills root in the repo. May be repeated.",
    )
    repo_remove_parser = repo_subparsers.add_parser(
        "remove",
        help="Remove a skill source repo.",
        description=(
            "Remove a configured skill source repo by repo ID, or use -l/--list to "
            "choose repos from an interactive list. Use --yes to confirm selected "
            "repo removals without an extra prompt."
        ),
    )
    repo_remove_parser.add_argument(
        "repo_id", nargs="?", help="Repo ID shown by 'sv repo list'."
    )
    repo_remove_parser.add_argument(
        "-l",
        "--list",
        dest="interactive",
        action="store_true",
        help="Choose repos from an interactive list.",
    )
    repo_remove_parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm selected repo removals without prompting.",
    )
    repo_subparsers.add_parser("list", help="List configured skill source repos.")

    return parser


def handle(
    args: argparse.Namespace,
    cwd: Path,
    home: Path,
    git_runner=default_runner,
    process_runner=default_process_runner,
    skill_selector: SkillSelector = select_skills,
    skill_chooser: SkillChooser | None = None,
    *,
    runtime: Runtime | None = None,
) -> int:
    runtime = (
        Runtime(
            cwd=cwd,
            home=home,
            env=os.environ,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        if runtime is None
        else runtime
    )
    cwd = runtime.cwd
    home = runtime.home
    paths = SvPaths.from_home(home)
    adapter = PiAdapter()
    chooser = _choose_skill if skill_chooser is None else skill_chooser
    record_global_source_state = _should_record_global_source_state(cwd, home)

    try:
        configured_jobs(runtime.env)

        if args.command == "cache":
            return _handle_cache(args, paths)

        if args.command == "repo":
            return _handle_repo(
                args,
                paths=paths,
                cwd=cwd,
                adapter=adapter,
                git_runner=git_runner,
                skill_selector=skill_selector,
                record_global_source_state=record_global_source_state,
                can_browse_tty=_can_browse_tty(runtime.stdin, runtime.stdout),
            )

        if args.command == "init":
            return _handle_init(args.folder, cwd=cwd, git_runner=git_runner)

        if args.command == "index":
            return _handle_index(cwd, args=args)

        if args.command == "run":
            local_context = _detect_local_context(cwd)
            return _handle_run(
                args.pi_args,
                cwd=cwd,
                adapter=adapter,
                process_runner=process_runner,
                context=local_context,
            )

        if args.command == "status":
            local_context = _detect_local_context(cwd)
            status_policy = _cache_policy_from_args(
                args,
                default_mode=CacheMode.FORCE_REFRESH,
                allow_stale_on_error=False,
            )
            return _handle_status(
                cwd=cwd,
                paths=paths,
                adapter=adapter,
                git_runner=git_runner,
                record_global_source_state=record_global_source_state,
                cache_policy=status_policy,
                context=local_context,
            )

        if args.command == "add":
            local_context = _detect_local_context(cwd)
            if args.interactive:
                if args.all or args.skill is not None or args.repo_id is not None:
                    raise SvError("Use -l by itself, or provide a skill name/--all.")
                add_policy = _cache_policy_from_args(args)
                config = _load_config_for_source_command(paths)
                catalog = _catalog_for_source_command(
                    config.repos,
                    paths,
                    git_runner,
                    policy=add_policy,
                    record_global_source_state=record_global_source_state,
                    lightweight_discovery=True,
                    update=(not args.interactive) or args.refresh,
                )
                return _handle_add_interactive(
                    catalog,
                    cwd=cwd,
                    adapter=adapter,
                    skill_selector=skill_selector,
                    skill_chooser=chooser,
                    context=local_context,
                    replace_existing=args.replace,
                )

            if args.repo_id is not None and not args.all:
                raise SvError("Use --repo only with --all.")
            if args.all and args.skill is not None:
                raise SvError("Use either a skill name or --all, not both.")
            if args.all:
                add_policy = _cache_policy_from_args(args)
                config = _load_config_for_source_command(paths)
                selected_repos = config.repos
                if args.repo_id is not None:
                    safe_repo_id = _escape_control_characters(args.repo_id)
                    selected_repos = [repo for repo in config.repos if repo.id == args.repo_id]
                    if not selected_repos:
                        raise SvError(
                            f"Repo '{safe_repo_id}' was not found in configured source repos."
                        )
                catalog = _catalog_for_source_command(
                    selected_repos,
                    paths,
                    git_runner,
                    policy=add_policy,
                    record_global_source_state=record_global_source_state,
                    lightweight_discovery=True,
                    update=(not args.interactive) or args.refresh,
                )
                return _handle_add_all(
                    catalog,
                    cwd=cwd,
                    adapter=adapter,
                    context=local_context,
                    skill_chooser=chooser,
                    replace_existing=args.replace,
                )
            if args.skill is None:
                raise SvError("Specify a skill name or use --all.")

            _validate_skill_reference(args.skill)
            add_policy = _cache_policy_from_args(args)
            config = _load_config_for_source_command(paths)
            catalog = _catalog_for_source_command(
                config.repos,
                paths,
                git_runner,
                policy=add_policy,
                record_global_source_state=record_global_source_state,
                lightweight_discovery=True,
                update=(not args.interactive) or args.refresh,
            )
            return _handle_add(
                args.skill,
                catalog,
                cwd=cwd,
                adapter=adapter,
                skill_chooser=chooser,
                context=local_context,
                replace_existing=args.replace,
            )

        if args.command == "remove":
            local_context = _detect_local_context(cwd)
            if args.interactive:
                if args.skill is not None or args.all:
                    raise SvError("Use -l by itself, or provide a skill name/--all.")
                return _handle_remove_interactive(
                    cwd=cwd,
                    adapter=adapter,
                    skill_selector=skill_selector,
                    context=local_context,
                    yes=args.yes,
                )

            if args.all:
                if args.skill is not None:
                    raise SvError("Use either a skill name or --all, not both.")
                return _handle_remove_all(
                    cwd=cwd,
                    adapter=adapter,
                    context=local_context,
                    yes=args.yes,
                )

            if args.yes:
                raise SvError("Use --yes only with -l or --all.")
            if args.skill is None:
                raise SvError("Specify a skill name or use -l/--all.")

            skill_name = normalize_skill_name(args.skill)
            return _handle_remove(
                skill_name, cwd=cwd, adapter=adapter, context=local_context
            )

        if args.command == "list":
            config = _load_config_for_source_command(paths)
            if not config.repos:
                _print_no_source_repos_configured()
                return 0
            catalog = _catalog_for_source_command(
                config.repos,
                paths,
                git_runner,
                policy=_cache_policy_from_args(args),
                record_global_source_state=record_global_source_state,
                lightweight_discovery=True,
            )
            return _handle_list(
                catalog,
                can_browse_tty=_can_browse_tty(runtime.stdin, runtime.stdout),
            )

        if args.command == "search":
            config = _load_config_for_source_command(paths)
            if not config.repos:
                _print_no_source_repos_configured()
                return 0
            catalog = _catalog_for_source_command(
                config.repos,
                paths,
                git_runner,
                policy=_cache_policy_from_args(args),
                record_global_source_state=record_global_source_state,
                lightweight_discovery=True,
            )
            return _handle_search(
                args.query,
                catalog,
                can_browse_tty=_can_browse_tty(runtime.stdin, runtime.stdout),
            )

        if args.command == "sync":
            local_context = _detect_local_context(cwd)
            config = _load_config_for_source_command(paths)
            sync_policy = _cache_policy_from_args(
                args,
                default_mode=CacheMode.FORCE_REFRESH,
                allow_stale_on_error=False,
            )
            catalog = _catalog_for_source_command(
                config.repos,
                paths,
                git_runner,
                policy=sync_policy,
                record_global_source_state=record_global_source_state,
                allow_partial_failures=False,
            )
            return _handle_sync(catalog, cwd=cwd, adapter=adapter, context=local_context)

        if args.command == "update":
            local_context = _detect_local_context(cwd)
            update_policy = _cache_policy_from_args(
                args,
                default_mode=CacheMode.FORCE_REFRESH,
                allow_stale_on_error=False,
            )
            return _handle_update(
                cwd=cwd,
                paths=paths,
                adapter=adapter,
                git_runner=git_runner,
                record_global_source_state=record_global_source_state,
                cache_policy=update_policy,
                context=local_context,
            )

        raise SvError(f"Unknown command: {args.command}")
    except SvError as exc:
        print(f"error: {exc}", file=runtime.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    runtime = Runtime.from_process()
    return handle(args, cwd=runtime.cwd, home=runtime.home, runtime=runtime)


def svx_main(argv: Sequence[str] | None = None) -> int:
    svx_args = _build_svx_parser().parse_args(sys.argv[1:] if argv is None else argv)
    forwarded = ["repo", "add", svx_args.repo]
    for skills_path in svx_args.skills_paths:
        forwarded.extend(["--skills-path", skills_path])
    args = build_parser().parse_args(forwarded)
    runtime = Runtime.from_process()
    return handle(args, cwd=runtime.cwd, home=runtime.home, runtime=runtime)


def _build_svx_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="svx",
        description="Add a skill source repo (alias for 'sv repo add').",
    )
    parser.add_argument(
        "repo", help="GitHub owner/repo, Git URL, or local Git repo path."
    )
    parser.add_argument(
        "--skills-path",
        dest="skills_paths",
        action="append",
        default=[],
        help="Relative POSIX path to a skills root in the repo. May be repeated.",
    )
    return parser


def _detect_local_context(
    cwd: Path, *, global_manifest_file: Path | None = None
) -> LocalContext:
    repo_root = _find_nearest_git_root(cwd)
    if repo_root is None:
        cwd_root = cwd.resolve()
        non_git_context = _find_nearest_non_git_project_context(
            cwd_root, global_manifest_file=global_manifest_file
        )
        if non_git_context is not None:
            return non_git_context
        return LocalContext(repo_root=cwd_root)
    _reject_symlinked_sv_metadata_dir(repo_root)
    path = index_path(repo_root)
    if path.is_symlink():
        raise SvError(f"Refusing to use symlinked sv index at {_escape_output_path(path)}.")
    if not path.is_file():
        return LocalContext(repo_root=repo_root)
    return LocalContext(repo_root=repo_root, index_kind=load_index(path).kind)


def _find_nearest_non_git_project_context(
    cwd_root: Path, *, global_manifest_file: Path | None = None
) -> LocalContext | None:
    for candidate in (cwd_root, *cwd_root.parents):
        metadata_dir = candidate / ".sv"
        if metadata_dir.is_symlink():
            raise SvError(
                "Refusing to use symlinked sv metadata directory at "
                f"{_escape_output_path(metadata_dir)}."
            )
        path = index_path(candidate)
        if path.is_symlink():
            raise SvError(f"Refusing to use symlinked sv index at {_escape_output_path(path)}.")
        if path.is_file():
            index = load_index(path)
            if index.kind == "project-index":
                return LocalContext(repo_root=candidate, index_kind="project-index")
        manifest = project_manifest_path(candidate)
        if manifest.is_symlink():
            raise SvError(
                f"Refusing to use symlinked sv project manifest at {_escape_output_path(manifest)}."
            )
        if manifest.is_file() and _is_non_git_project_manifest(
            manifest, global_manifest_file=global_manifest_file
        ):
            return LocalContext(repo_root=candidate)
    return None


def _is_non_git_project_manifest(
    manifest: Path, *, global_manifest_file: Path | None = None
) -> bool:
    data = load_toml_document(manifest, "sv manifest")
    if "skills" in data:
        return True
    if global_manifest_file is not None and manifest == global_manifest_file:
        return False
    return "sources" not in data


def _reject_symlinked_sv_metadata_dir(repo_root: Path) -> None:
    metadata_dir = repo_root / ".sv"
    if metadata_dir.is_symlink():
        raise SvError(
            "Refusing to use symlinked sv metadata directory at "
            f"{_escape_output_path(metadata_dir)}."
        )


def _find_nearest_git_root(cwd: Path) -> Path | None:
    current = cwd.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _should_record_global_source_state(cwd: Path, home: Path) -> bool:
    return True


def _load_config_for_source_command(paths: SvPaths) -> SvConfig:
    if not paths.config_file.exists():
        return _load_or_prompt_for_initial_sources(paths)

    config = ConfigStore(paths).load()
    if config.repos or _config_explicitly_disables_sources(paths):
        return config
    return _load_or_prompt_for_initial_sources(paths)


def _load_or_prompt_for_initial_sources(paths: SvPaths) -> SvConfig:
    if _can_prompt_for_initial_sources():
        return _prompt_for_initial_sources(paths)
    raise SvError(_missing_source_config_guidance())


def _config_explicitly_disables_sources(paths: SvPaths) -> bool:
    data = load_toml_document(paths.config_file, "sv config")
    return "repos" in data


def _can_prompt_for_initial_sources() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _missing_source_config_guidance() -> str:
    return (
        "No skill source repos are configured. "
        "Add one with 'sv repo add <repo>' before running this command."
    )


def _print_no_source_repos_configured() -> None:
    print("No skill source repos configured. Add one with 'sv repo add <owner/repo>'.")


def _prompt_for_initial_sources(paths: SvPaths) -> SvConfig:
    sources = recommended_sources()
    print("No skill source repos are configured.")
    if sources:
        print("Choose a recommended source repo to add:")
        for index, source in enumerate(sources, start=1):
            description = f" - {source.description}" if source.description else ""
            print(f"  {index}. {source.repo}{description}")
        print("  m. Enter another repo")
        prompt = "Choose a source number, m to enter another repo, or q to cancel: "
    else:
        print("Enter a source repo to add, or q to cancel.")
        prompt = "Source repo: "

    while True:
        choice = input(prompt).strip()
        if choice.lower() == "q":
            raise SvError(_missing_source_config_guidance())
        if sources and choice.lower() == "m":
            repo = input("Repo: ").strip()
        elif sources and choice.isdigit() and 1 <= int(choice) <= len(sources):
            repo = sources[int(choice) - 1].repo
        elif not sources:
            repo = choice
        else:
            print("Enter a listed source number, m, or q.")
            continue

        try:
            result = ConfigStore(paths).add_repo(repo)
        except (SvError, ValueError) as exc:
            print(f"Could not add source repo: {exc}")
            continue
        _print_repo_change_result(result)
        return ConfigStore(paths).load()


def _print_repo_change_result(result: RepoChangeResult) -> None:
    repo_id = _escape_control_characters(result.repo.id)
    repo_url = _escape_control_characters(result.repo.url)
    if result.status == "exists":
        print(f"Repo {repo_id} is already configured.")
    elif result.status == "updated":
        print(f"Updated repo {repo_id} ({repo_url})")
    else:
        print(f"Added repo {repo_id} ({repo_url})")


def _update_sources_and_catalog(
    paths: SvPaths,
    git_runner,
    *,
    update: bool = True,
    record_global_source_state: bool = True,
    lightweight_discovery: bool = True,
    allow_partial_failures: bool = False,
) -> list[SourceSkill]:
    config = _load_config_for_source_command(paths)
    return _update_sources_and_catalog_from_repos(
        config.repos,
        paths,
        git_runner,
        update=update,
        record_global_source_state=record_global_source_state,
        lightweight_discovery=lightweight_discovery,
        allow_partial_failures=allow_partial_failures,
    )


def _update_sources_and_catalog_for_add_all(
    paths: SvPaths,
    git_runner,
    *,
    repo_id: str | None,
    record_global_source_state: bool,
) -> list[SourceSkill]:
    if repo_id is None:
        return _update_sources_and_catalog(
            paths,
            git_runner,
            update=True,
            record_global_source_state=record_global_source_state,
        )

    safe_repo_id = _escape_control_characters(repo_id)
    config = _load_config_for_source_command(paths)
    repos = [repo for repo in config.repos if repo.id == repo_id]
    if not repos:
        raise SvError(f"Repo '{safe_repo_id}' was not found in configured source repos.")
    return _update_sources_and_catalog_from_repos(
        repos,
        paths,
        git_runner,
        update=True,
        record_global_source_state=record_global_source_state,
        lightweight_discovery=True,
    )


def _update_sources_and_catalog_from_repos(
    repos: Sequence[RepoConfig],
    paths: SvPaths,
    git_runner,
    *,
    update: bool = True,
    record_global_source_state: bool = True,
    lightweight_discovery: bool = False,
    warn: Callable[[str], None] | None = None,
    allow_partial_failures: bool = False,
) -> list[SourceSkill]:
    return list(
        _update_sources_and_catalog_cache_refresh_from_repos(
            repos,
            paths,
            git_runner,
            update=update,
            record_global_source_state=record_global_source_state,
            lightweight_discovery=lightweight_discovery,
            warn=warn,
            allow_partial_failures=allow_partial_failures,
        ).entries
    )


def _update_sources_and_catalog_cache_refresh_from_repos(
    repos: Sequence[RepoConfig],
    paths: SvPaths,
    git_runner,
    *,
    update: bool = True,
    record_global_source_state: bool = True,
    lightweight_discovery: bool = False,
    warn: Callable[[str], None] | None = None,
    allow_partial_failures: bool = False,
) -> CacheRefreshResult:
    started_at = _utc_now()
    source_state_recorded = False
    refreshed_backends_by_repo: Mapping[str, str] = {}
    index_hashes_by_repo: Mapping[str, str | None] = {}
    refreshed_repo_ids: frozenset[str] | None = None
    try:
        if lightweight_discovery:
            backends_by_repo = {
                repo.id: source_backends_for_repo(
                    repo, paths, runner=git_runner, update=update
                )
                for repo in repos
            }
            result = build_source_catalog_from_backends(
                repos, paths, backends_by_repo, warn=warn
            )
            blocking_failures = _unrefreshed_source_failures(
                result.failures, result.refreshed_repo_ids
            )
            if blocking_failures:
                index_failure = _first_index_parse_failure(blocking_failures)
                if index_failure is not None:
                    if record_global_source_state:
                        _record_mixed_source_refresh(
                            paths,
                            repos,
                            result.entries,
                            result.refreshed_repo_ids,
                            blocking_failures,
                            started_at,
                            refreshed_backends_by_repo=result.refreshed_backends_by_repo,
                            index_hashes_by_repo=result.index_hashes_by_repo,
                        )
                        source_state_recorded = True
                    raise SvError(index_failure)
                if not allow_partial_failures:
                    if record_global_source_state:
                        _record_mixed_source_refresh(
                            paths,
                            repos,
                            result.entries,
                            result.refreshed_repo_ids,
                            blocking_failures,
                            started_at,
                            refreshed_backends_by_repo=result.refreshed_backends_by_repo,
                            index_hashes_by_repo=result.index_hashes_by_repo,
                        )
                        source_state_recorded = True
                    raise SvError(_failure_report(blocking_failures))
            if blocking_failures and not result.entries:
                metadata_failure = _first_skill_metadata_failure(blocking_failures)
                if record_global_source_state:
                    _record_mixed_source_refresh(
                        paths,
                        repos,
                        result.entries,
                        result.refreshed_repo_ids,
                        blocking_failures,
                        started_at,
                        refreshed_backends_by_repo=result.refreshed_backends_by_repo,
                        index_hashes_by_repo=result.index_hashes_by_repo,
                    )
                    source_state_recorded = True
                if metadata_failure is not None:
                    raise SvError(metadata_failure)
                raise SvError(_failure_report(blocking_failures))
            catalog = list(result.entries)
            refreshed_backends_by_repo = result.refreshed_backends_by_repo
            index_hashes_by_repo = result.index_hashes_by_repo
            refreshed_repo_ids = frozenset(result.refreshed_repo_ids)
            if blocking_failures and record_global_source_state:
                _record_mixed_source_refresh(
                    paths,
                    repos,
                    catalog,
                    result.refreshed_repo_ids,
                    blocking_failures,
                    started_at,
                    refreshed_backends_by_repo=result.refreshed_backends_by_repo,
                    index_hashes_by_repo=result.index_hashes_by_repo,
                )
                source_state_recorded = True
            if record_global_source_state and not source_state_recorded:
                _record_global_source_refresh(
                    paths,
                    repos,
                    catalog,
                    started_at,
                    refreshed_backends_by_repo=result.refreshed_backends_by_repo,
                    index_hashes_by_repo=result.index_hashes_by_repo,
                )
                source_state_recorded = True
        else:
            failure = _ensure_source_repos_for_refresh(
                repos,
                paths,
                git_runner,
                update=update,
            )
            if failure is not None:
                failed_repo, error = failure
                if record_global_source_state:
                    _record_global_source_refresh_failure(
                        paths, (failed_repo,), error, started_at
                    )
                    source_state_recorded = True
                raise SvError(error)
            catalog = build_source_catalog(repos, paths)
            refreshed_repo_ids = frozenset(repo.id for repo in repos)
    except SvError as exc:
        if record_global_source_state and not source_state_recorded:
            _record_global_source_refresh_failure(paths, repos, str(exc), started_at)
        raise
    if record_global_source_state and not source_state_recorded:
        _record_global_source_refresh(paths, repos, catalog, started_at)
    return CacheRefreshResult(
        entries=tuple(catalog),
        refreshed_backends_by_repo=refreshed_backends_by_repo,
        index_hashes_by_repo=index_hashes_by_repo,
        refreshed_repo_ids=refreshed_repo_ids,
    )


def _catalog_for_source_command(
    repos: Sequence[RepoConfig],
    paths: SvPaths,
    git_runner,
    *,
    policy: CachePolicy,
    record_global_source_state: bool,
    lightweight_discovery: bool = True,
    allow_partial_failures: bool = False,
    update: bool = True,
) -> list[SourceSkill]:
    def refresh(selected_repos: Sequence[RepoConfig]) -> CacheRefreshResult:
        return _update_sources_and_catalog_cache_refresh_from_repos(
            selected_repos,
            paths,
            git_runner,
            update=update,
            record_global_source_state=record_global_source_state,
            lightweight_discovery=lightweight_discovery,
            allow_partial_failures=allow_partial_failures,
            warn=lambda message: print(message, file=sys.stderr),
        )

    catalog = get_catalog_with_cache(
        repos,
        paths,
        policy=policy,
        now=datetime.now(UTC),
        refresh_catalog=refresh,
        warn=lambda message: print(message, file=sys.stderr),
    )
    allow_source_fallback = policy.mode is not CacheMode.CACHE_ONLY
    repos_by_id = {repo.id: repo for repo in repos}

    def refresh_entry_on_body_miss(entry: SourceSkill) -> SourceSkill | None:
        repo = repos_by_id.get(entry.repo_id)
        if repo is None:
            return None
        refreshed_catalog = get_catalog_with_cache(
            [repo],
            paths,
            policy=CachePolicy.force_refresh(allow_stale_on_error=False),
            now=datetime.now(UTC),
            refresh_catalog=refresh,
            warn=lambda message: print(message, file=sys.stderr),
        )
        for candidate in refreshed_catalog:
            if (
                candidate.name == entry.name
                and candidate.source_relative_path == entry.source_relative_path
            ):
                return candidate
        return None

    def after_store(
        entry: SourceSkill, content_hash: str, skill_file_hash: str
    ) -> None:
        repo = repos_by_id.get(entry.repo_id)
        if repo is None:
            return
        record_cached_skill_body_hash(
            paths,
            repo,
            entry,
            content_hash=content_hash,
            skill_file_hash=skill_file_hash,
        )

    return wrap_catalog_with_skill_body_cache(
        catalog,
        paths,
        now=lambda: datetime.now(UTC),
        after_store=after_store,
        allow_source_fallback=allow_source_fallback,
        refresh_entry_on_body_miss=(
            refresh_entry_on_body_miss if allow_source_fallback else None
        ),
        warn=lambda message: print(message, file=sys.stderr),
    )


def _ensure_source_repos_for_refresh(
    repos: Sequence[RepoConfig],
    paths: SvPaths,
    git_runner,
    *,
    update: bool,
) -> tuple[RepoConfig, str] | None:
    repo_paths: dict[str, Path] = {}
    for repo in repos:
        repo_path = paths.source_repo_for(repo.id)
        try:
            reject_symlinked_source_cache_path(repo_path, paths.sources_dir)
        except SvError as exc:
            return repo, str(exc)
        repo_paths[repo.id] = repo_path

    def worker(repo: RepoConfig) -> tuple[RepoConfig, str | None]:
        repo_path = repo_paths[repo.id]
        try:
            reject_symlinked_source_cache_path(repo_path, paths.sources_dir)
            ensure_source_repo(
                repo.url,
                repo_path,
                runner=git_runner,
                update=update,
                configured_skills_paths=repo.skills_paths,
            )
        except SvError as exc:
            return repo, str(exc)
        return repo, None

    results = map_ordered(list(repos), worker)
    for repo, error in results:
        if error is not None:
            return repo, error
    return None


def _record_mixed_source_refresh(
    paths: SvPaths,
    repos: Sequence[RepoConfig],
    catalog: Sequence[SourceSkill],
    refreshed_repo_ids: Sequence[str],
    blocking_failures,
    started_at: str,
    *,
    refreshed_backends_by_repo: dict[str, str] | None = None,
    index_hashes_by_repo: dict[str, str | None] | None = None,
) -> None:
    refreshed = set(refreshed_repo_ids)
    failed = {failure.repo_id for failure in blocking_failures}
    refreshed_repos = [repo for repo in repos if repo.id in refreshed]
    failed_repos = [repo for repo in repos if repo.id in failed and repo.id not in refreshed]
    if refreshed_repos:
        _record_global_source_refresh(
            paths,
            refreshed_repos,
            catalog,
            started_at,
            refreshed_backends_by_repo=refreshed_backends_by_repo,
            index_hashes_by_repo=index_hashes_by_repo,
        )
    if failed_repos:
        _record_global_source_refresh_failure(
            paths, failed_repos, _failure_report(blocking_failures), started_at
        )


def _unrefreshed_source_failures(failures, refreshed_repo_ids) -> tuple:
    refreshed = set(refreshed_repo_ids)
    return tuple(failure for failure in failures if failure.repo_id not in refreshed)


def _failure_report(failures) -> str:
    messages = [*_source_failure_summaries(failures)]
    messages.extend(failure.user_message for failure in failures)
    return "\n".join(messages)


def _source_failure_summaries(failures) -> list[str]:
    by_repo: dict[tuple[str, str], list] = {}
    for failure in failures:
        by_repo.setdefault((failure.repo_id, failure.repo_url), []).append(failure)

    summaries: list[str] = []
    for (repo_id, _repo_url), repo_failures in by_repo.items():
        backends = {failure.backend for failure in repo_failures}
        tried_github_api = any(backend.startswith("github-") for backend in backends)
        tried_git = any(backend.startswith("git-") for backend in backends)
        auth_or_rate_limited = any(
            _failure_mentions_auth_or_rate_limit(failure)
            for failure in repo_failures
            if failure.backend.startswith("github-")
        )
        if tried_github_api and tried_git and auth_or_rate_limited:
            summaries.append(
                f"sv could not refresh {_escape_control_characters(repo_id)}. "
                "GitHub API auth/rate limits may require 'gh auth login' or "
                "GH_TOKEN; sv tried lightweight Git fallback but it also failed. "
                "Details:"
            )
    return summaries


def _failure_mentions_auth_or_rate_limit(failure) -> bool:
    text = f"{failure.detail} {failure.hint or ''}".casefold()
    return any(
        phrase in text
        for phrase in (
            "rate limit",
            "auth",
            "private repo",
            "gh_token",
            "gh auth login",
            "http 401",
            "http 403",
        )
    )


def _first_index_parse_failure(failures) -> str | None:
    for failure in failures:
        if failure.operation == "parsing .sv/index.toml":
            return failure.user_message
    return None


def _first_skill_metadata_failure(failures) -> str | None:
    for failure in failures:
        if failure.operation == "reading candidate SKILL.md file" and failure.detail.startswith(
            "Failed to read SKILL.md"
        ):
            return failure.detail
    return None


def _record_global_source_refresh(
    paths: SvPaths,
    repos: Sequence[RepoConfig],
    catalog: Sequence[SourceSkill],
    started_at: str,
    *,
    refreshed_backends_by_repo: dict[str, str] | None = None,
    index_hashes_by_repo: dict[str, str | None] | None = None,
) -> None:
    states = _load_global_manifest_for_source_state(paths)
    if states is None:
        return
    refreshed_at = _utc_now()
    entries_by_repo = _catalog_entries_by_repo(catalog)
    for repo in repos:
        repo_entries = _catalog_entries_for_repo(repo, catalog, entries_by_repo)
        backend = _source_refresh_metadata_for_repo(
            repo, repos, refreshed_backends_by_repo
        )
        source_repo_path = _source_repo_path_for_refresh_metadata(
            paths, repo, repos, refreshed_backends_by_repo
        )
        if backend == "git-local-source":
            source_repo_path = _local_source_root_from_catalog(repo_entries) or source_repo_path
        source_commit, source_tree = _git_source_metadata(source_repo_path)
        index_hash = _source_refresh_metadata_for_repo(repo, repos, index_hashes_by_repo)
        states[repo.id] = GlobalSourceState(
            repo_id=repo.id,
            repo_url=repo.url,
            backend=backend or "git",
            last_refresh_started_at=started_at,
            last_refresh_finished_at=refreshed_at,
            last_refresh_status="ok",
            source_commit=source_commit,
            source_tree=source_tree,
            index_hash=index_hash,
            catalog_hash=_catalog_hash(repo_entries),
            catalog_skill_count=len(repo_entries),
            health_status="ok",
            health_details=f"catalog contains {len(repo_entries)} skills",
        )
    GlobalManifestStore(paths).save(states)


def _catalog_entries_for_repo(
    repo: RepoConfig,
    catalog: Sequence[SourceSkill],
    entries_by_repo: Mapping[str, tuple[SourceSkill, ...]],
) -> tuple[SourceSkill, ...]:
    direct_entries = entries_by_repo.get(repo.id)
    if direct_entries is not None:
        return direct_entries
    try:
        source_key = repo_source_key(repo.url)
    except ValueError:
        return ()
    equivalent_entries = [
        entry for entry in catalog if repo_source_key(entry.repo_url) == source_key
    ]
    return tuple(sorted(equivalent_entries, key=_catalog_hash_key))


def _local_source_root_from_catalog(entries: Sequence[SourceSkill]) -> Path | None:
    roots: set[Path] = set()
    for entry in entries:
        root = entry.source_path
        for _part in entry.source_relative_path.split("/"):
            root = root.parent
        roots.add(root)
    if len(roots) == 1:
        return next(iter(roots))
    return None


def _source_repo_path_for_refresh_metadata(
    paths: SvPaths,
    repo: RepoConfig,
    repos: Sequence[RepoConfig],
    refreshed_backends_by_repo: Mapping[str, str | None] | None,
) -> Path:
    repo_id = _source_refresh_repo_id_for_repo(repo, repos, refreshed_backends_by_repo)
    return paths.source_repo_for(repo_id or repo.id)


def _source_refresh_metadata_for_repo(
    repo: RepoConfig,
    repos: Sequence[RepoConfig],
    metadata_by_repo: Mapping[str, str | None] | None,
) -> str | None:
    repo_id = _source_refresh_repo_id_for_repo(repo, repos, metadata_by_repo)
    if repo_id is None or metadata_by_repo is None:
        return None
    return metadata_by_repo[repo_id]


def _source_refresh_repo_id_for_repo(
    repo: RepoConfig,
    repos: Sequence[RepoConfig],
    metadata_by_repo: Mapping[str, str | None] | None,
) -> str | None:
    if not metadata_by_repo:
        return None
    if repo.id in metadata_by_repo:
        return repo.id
    try:
        source_key = repo_source_key(repo.url)
    except ValueError:
        return None
    for candidate in repos:
        if candidate.id not in metadata_by_repo:
            continue
        try:
            if repo_source_key(candidate.url) == source_key:
                return candidate.id
        except ValueError:
            continue
    return None


def _record_global_source_refresh_failure(
    paths: SvPaths, repos: Sequence[RepoConfig], error: str, started_at: str
) -> None:
    states = _load_global_manifest_for_source_state(paths)
    if states is None:
        return
    refreshed_at = _utc_now()
    for repo in repos:
        previous = _previous_state_for_same_source(states.get(repo.id), repo.url)
        states[repo.id] = GlobalSourceState(
            repo_id=repo.id,
            repo_url=repo.url,
            backend="git",
            last_refresh_started_at=started_at,
            last_refresh_finished_at=refreshed_at,
            last_refresh_status="error",
            last_refresh_error=error,
            source_commit=previous.source_commit if previous else None,
            source_tree=previous.source_tree if previous else None,
            index_hash=previous.index_hash if previous else None,
            catalog_hash=previous.catalog_hash if previous else None,
            catalog_skill_count=previous.catalog_skill_count if previous else None,
            health_status="error",
            health_details=error,
        )
    GlobalManifestStore(paths).save(states)


def _previous_state_for_same_source(
    previous: GlobalSourceState | None, repo_url: str
) -> GlobalSourceState | None:
    if previous is None:
        return None
    try:
        if repo_source_key(previous.repo_url) == repo_source_key(repo_url):
            return previous
    except ValueError:
        return None
    return None


def _remove_global_source_state(paths: SvPaths, repo_id: str) -> None:
    if not paths.global_manifest_file.exists():
        return
    states = _load_global_manifest_for_source_state(paths)
    if states is None or repo_id not in states:
        return
    del states[repo_id]
    GlobalManifestStore(paths).save(states)


def _load_global_manifest_for_source_state(
    paths: SvPaths,
) -> dict[str, GlobalSourceState] | None:
    if _global_manifest_path_has_project_only_shape(paths):
        return None
    try:
        return GlobalManifestStore(paths).load()
    except SvError as exc:
        if _PROJECT_STATE_IN_GLOBAL_MANIFEST in str(exc):
            return None
        raise


def _global_manifest_path_has_project_only_shape(paths: SvPaths) -> bool:
    path = paths.global_manifest_file
    if not path.is_file():
        return False
    data = load_toml_document(path, "sv manifest")
    return "sources" not in data and "skills" not in data


def _git_source_metadata(repo_path: Path) -> tuple[str | None, str | None]:
    commit = _run_git_metadata(["git", "rev-parse", "HEAD"], repo_path)
    tree = _run_git_metadata(["git", "rev-parse", "HEAD^{tree}"], repo_path)
    return commit, tree


def _run_git_metadata(command: list[str], repo_path: Path) -> str | None:
    try:
        result = default_runner(command, repo_path)
    except (OSError, SvError):
        return None
    if result.returncode != 0:
        return None
    value = (result.stdout or "").strip()
    return value or None


def _catalog_entries_by_repo(
    catalog: Sequence[SourceSkill],
) -> dict[str, tuple[SourceSkill, ...]]:
    entries_by_repo: dict[str, list[SourceSkill]] = {}
    for entry in catalog:
        entries_by_repo.setdefault(entry.repo_id, []).append(entry)
    return {
        repo_id: tuple(sorted(entries, key=_catalog_hash_key))
        for repo_id, entries in entries_by_repo.items()
    }


def _catalog_hash(entries: Sequence[SourceSkill]) -> str:
    digest = hashlib.sha256()
    digest.update(b"sv-source-catalog-v1\0")
    for entry in sorted(entries, key=_catalog_hash_key):
        for value in (entry.name, entry.source_relative_path, entry.description):
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _catalog_hash_key(entry: SourceSkill) -> tuple[str, str, str]:
    return (entry.name, entry.source_relative_path, entry.description)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_skill_reference(reference: str) -> None:
    if ":" in reference:
        repo_id, skill = reference.rsplit(":", 1)
        if not repo_id.strip():
            raise SvError(f"Invalid skill reference {reference!r}. Use repo:skill.")
        if _contains_control_characters(repo_id):
            safe_reference = _escape_control_characters(reference)
            raise SvError(
                f"Invalid skill reference '{safe_reference}'. "
                "Repo id cannot contain control characters."
            )
        if _contains_unicode_format_character(repo_id):
            safe_reference = _escape_control_characters(reference)
            raise SvError(
                f"Invalid skill reference '{safe_reference}'. "
                "Repo id cannot contain Unicode format controls."
            )
        if "/" in skill:
            normalize_source_relative_path(skill)
        else:
            normalize_skill_name(skill)
        return
    normalize_skill_name(reference)


def _contains_control_characters(value: str) -> bool:
    return any(ord(char) < 0x20 or 0x7F <= ord(char) < 0xA0 for char in value)


def _contains_unicode_format_character(value: str) -> bool:
    return any(unicodedata.category(char) == "Cf" for char in value)


def _table_width() -> int:
    return shutil.get_terminal_size(fallback=(120, 24)).columns


def _print_wrapped(message: str, *, leading_blank_line: bool = False) -> None:
    if leading_blank_line:
        print()
    width = _table_width()
    indent = "     " if width > 5 else ""
    for line in _wrap_message_to_display_width(message, width, subsequent_indent=indent):
        print(line)


def _wrap_message_to_display_width(
    message: str, width: int, *, subsequent_indent: str = ""
) -> list[str]:
    line_width = max(width, 1)
    subsequent_width = max(line_width - _display_width(subsequent_indent), 1)
    lines: list[str] = []
    current = ""
    current_width = line_width
    current_indent = ""

    for word in message.split():
        remaining = word
        while remaining:
            separator_width = 1 if current else 0
            available = current_width - _display_width(current) - separator_width
            if _display_width(remaining) <= available:
                current = f"{current} {remaining}" if current else remaining
                break
            if current:
                lines.append(f"{current_indent}{current}")
                current = ""
                current_width = subsequent_width
                current_indent = subsequent_indent
                continue

            chunk, remaining = _split_display_width(remaining, current_width)
            lines.append(f"{current_indent}{chunk}")
            current_width = subsequent_width
            current_indent = subsequent_indent

    if current:
        lines.append(f"{current_indent}{current}")
    return lines or [""]


def _split_display_width(value: str, width: int) -> tuple[str, str]:
    chunk: list[str] = []
    current_width = 0
    for index, char in enumerate(value):
        char_width = _character_width(char)
        if char_width == 0:
            chunk.append(char)
            continue
        if char_width > width and not chunk:
            return "?" * width, value[index + 1 :]
        if current_width + char_width > width:
            return "".join(chunk), value[index:]
        chunk.append(char)
        current_width += char_width
    return "".join(chunk), ""


def _display_width(value: str) -> int:
    return sum(_character_width(char) for char in value)


def _print_tty_detail(title: str, rows: Sequence[tuple[str, str]]) -> None:
    width = max(
        [
            _display_width(title),
            *(_display_width(f"{label}: {value}") for label, value in rows),
        ],
        default=_display_width(title),
    )
    rule = "-" * width
    print(f"{_TTY_DETAIL_RULE}{rule}{_TTY_DETAIL_RESET}")
    print(f"{_TTY_DETAIL_HEADER}{_TTY_DETAIL_BOLD}{title}{_TTY_DETAIL_RESET}")
    print(f"{_TTY_DETAIL_RULE}{rule}{_TTY_DETAIL_RESET}")
    for label, value in rows:
        print(f"{label}: {value}")


def _character_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    if unicodedata.east_asian_width(char) in {"F", "W"}:
        return 2
    return 1


def _handle_init(folder: str | None, cwd: Path, git_runner) -> int:
    target = cwd if folder is None else cwd / folder
    _prepare_init_target(target)
    _ensure_init_git_repo(target, git_runner)
    _ensure_init_directory(target / "skills", "skills directory")
    _reject_symlinked_init_metadata_dir(target)

    document, should_write_index = _init_index_document(target)
    update_readme_skill_table(readme_path(target), document)
    if should_write_index:
        save_index(index_path(target), document)
    _ensure_init_manifest(target)

    print(f"Initialized skill-vault repo at {_escape_output_path(target)}")
    return 0


def _prepare_init_target(target: Path) -> None:
    if target.is_symlink():
        raise SvError(
            f"Refusing to initialize symlinked target folder at {_escape_output_path(target)}."
        )
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        details = _escape_control_characters(str(exc))
        raise SvError(
            f"Failed to create target folder {_escape_output_path(target)}: {details}"
        ) from exc
    if not target.is_dir():
        raise SvError(
            f"Target path {_escape_output_path(target)} exists but is not a directory."
        )


def _ensure_init_directory(path: Path, description: str) -> None:
    if path.is_symlink():
        raise SvError(
            f"Refusing to use symlinked {description} at {_escape_output_path(path)}."
        )
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        details = _escape_control_characters(str(exc))
        raise SvError(
            f"Failed to create {description} at {_escape_output_path(path)}: {details}"
        ) from exc
    if not path.is_dir():
        raise SvError(
            f"{description.capitalize()} path {_escape_output_path(path)} exists but is not a directory."
        )


def _reject_symlinked_init_metadata_dir(target: Path) -> None:
    path = target / ".sv"
    if path.is_symlink():
        raise SvError(
            f"Refusing to use symlinked sv metadata directory at {_escape_output_path(path)}."
        )
    if path.exists() and not path.is_dir():
        raise SvError(
            f"Sv metadata path {_escape_output_path(path)} exists but is not a directory."
        )


def _init_index_document(target: Path) -> tuple[IndexDocument, bool]:
    path = index_path(target)
    if path.is_symlink():
        raise SvError(f"Refusing to use symlinked sv index at {_escape_output_path(path)}.")
    if path.exists():
        document = load_index(path)
        if document.kind != "skill-vault":
            raise SvError(
                f"Existing sv index at {_escape_output_path(path)} is not a skill-vault index."
            )
        return document, False
    return (
        IndexDocument(
            kind="skill-vault",
            generated_by="sv",
            generated_at=_utc_now(),
            skills=(),
        ),
        True,
    )


def _ensure_init_manifest(target: Path) -> None:
    path = target / ".sv" / "manifest.toml"
    if path.is_symlink():
        raise SvError(f"Refusing to use symlinked sv manifest at {_escape_output_path(path)}.")
    if path.exists():
        if not path.is_file():
            raise SvError(
                f"Sv manifest path {_escape_output_path(path)} exists but is not a file."
            )
        return
    ProjectManifestStore(target).save({})


def _ensure_init_git_repo(target: Path, git_runner) -> None:
    git_metadata = target / ".git"
    if git_metadata.is_symlink():
        raise SvError(
            f"Refusing to use symlinked Git metadata path at {_escape_output_path(git_metadata)}."
        )

    if git_metadata.exists():
        try:
            probe = git_runner(["git", "rev-parse", "--is-inside-work-tree"], target)
        except FileNotFoundError as exc:
            raise SvError(_missing_git_guidance()) from exc
        except OSError as exc:
            raise SvError(
                "Checking Git availability failed: "
                f"{_escape_control_characters(str(exc))}"
            ) from exc
        if probe.returncode == 0 and (probe.stdout or "").strip() == "true":
            return
        raise SvError(
            "Existing Git metadata could not be validated at "
            f"{_escape_output_path(git_metadata)}."
        )

    try:
        result = git_runner(["git", "init"], target)
    except FileNotFoundError as exc:
        raise SvError(_missing_git_guidance()) from exc
    except OSError as exc:
        raise SvError(
            f"Failed to initialize Git repo at {_escape_output_path(target)}: "
            f"{_escape_control_characters(str(exc))}"
        ) from exc
    if result.returncode != 0:
        details = _escape_control_characters(
            (result.stderr or result.stdout or "").strip()
        )
        suffix = f": {details}" if details else ""
        raise SvError(
            f"Failed to initialize Git repo at {_escape_output_path(target)}{suffix}"
        )


def _missing_git_guidance() -> str:
    return (
        "Git is required but was not found on PATH. Install Git from "
        "https://git-scm.com/downloads or add git to PATH, then retry."
    )


def _handle_index(cwd: Path, *, args: argparse.Namespace) -> int:
    repo_root = _find_repo_root(cwd)
    path = index_path(repo_root)
    if path.is_symlink():
        raise SvError(f"Refusing to use symlinked sv index at {_escape_output_path(path)}.")
    kind: IndexKind = "project-index"
    if path.exists():
        kind = load_index(path).kind
    document = _scan_repo_for_configured_index(
        repo_root,
        kind=kind,
        include_paths=args.include_paths,
        exclude_paths=args.exclude_paths,
    )
    save_index(path, document)
    if document.kind == "skill-vault":
        update_readme_skill_table(readme_path(repo_root), document)
    skill_count = len(document.skills)
    suffix = "" if skill_count == 1 else "s"
    print(f"Wrote sv index with {skill_count} skill{suffix} to {_escape_output_path(path)}")
    return 0


def _find_repo_root(cwd: Path) -> Path:
    current = cwd.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def _scan_repo_for_configured_index(
    repo_root: Path,
    *,
    kind: IndexKind,
    include_paths: Sequence[str] = (),
    exclude_paths: Sequence[str] = (),
) -> IndexDocument:
    scan_config = load_index_scan_config(repo_root)
    return scan_repo_for_index(
        repo_root,
        kind=kind,
        generated_at=_utc_now(),
        warn=lambda message: print(message, file=sys.stderr),
        include_paths=(*scan_config.include_paths, *include_paths),
        exclude_paths=(*scan_config.exclude_paths, *exclude_paths),
    )


def _handle_cache(args: argparse.Namespace, paths: SvPaths) -> int:
    if args.cache_command == "status":
        summary = cache_summary(paths)
        print("Global sv cache")
        print(
            format_table(
                ["Catalog files", "Skill bodies", "Skill body bytes"],
                [
                    [
                        str(summary.catalog_files),
                        str(summary.skill_bodies),
                        str(summary.skill_body_bytes),
                    ]
                ],
                max_widths={
                    "Catalog files": 16,
                    "Skill bodies": 16,
                    "Skill body bytes": 18,
                },
                min_widths={
                    "Catalog files": 13,
                    "Skill bodies": 12,
                    "Skill body bytes": 16,
                },
                max_table_width=_table_width(),
            )
        )
        return 0
    if args.cache_command == "clean":
        summary = clean_cache(paths, now=datetime.now(UTC))
        print(
            f"Pruned global sv skill body cache. Skill bodies: {summary.skill_bodies}; bytes: {summary.skill_body_bytes}."
        )
        return 0
    raise SvError(f"Unknown cache command: {args.cache_command}")


def _handle_repo(
    args: argparse.Namespace,
    paths: SvPaths,
    *,
    cwd: Path,
    adapter: PiAdapter,
    git_runner,
    skill_selector: SkillSelector = select_skills,
    record_global_source_state: bool = True,
    can_browse_tty: bool | None = None,
) -> int:
    repo_list_alias = getattr(args, "repo_list_alias", False)
    if repo_list_alias and args.repo_command not in {None, "list"}:
        raise SvError("Use 'sv repo -l' by itself or use a repo subcommand.")

    if args.repo_command == "add":
        try:
            result = ConfigStore(paths).add_repo(args.repo, skills_paths=args.skills_paths)
        except ValueError as exc:
            raise SvError(str(exc)) from exc
        _print_repo_change_result(result)
        return 0

    if args.repo_command == "remove":
        if args.interactive:
            if args.repo_id is not None:
                raise SvError("Use repo remove -l by itself, or provide a repo id.")
            return _handle_repo_remove_interactive(
                paths, skill_selector=skill_selector, yes=args.yes
            )
        if args.yes:
            raise SvError("Use --yes only with repo remove -l.")
        if args.repo_id is None:
            raise SvError("Specify a repo id or use -l.")
        try:
            removed = ConfigStore(paths).remove_repo(args.repo_id)
        except ValueError as exc:
            raise SvError(str(exc)) from exc
        _remove_global_source_state(paths, removed.id)
        print(f"Removed repo {removed.id}")
        return 0

    if args.repo_command == "list" or repo_list_alias:
        config = _load_config_for_source_command(paths)
        if not config.repos:
            _print_no_source_repos_configured()
            return 0

        if can_browse_tty is None:
            can_browse_tty = _can_browse_tty()
        if can_browse_tty:
            try:
                return _handle_repo_browser(
                    config.repos,
                    paths=paths,
                    cwd=cwd,
                    adapter=adapter,
                    git_runner=git_runner,
                    record_global_source_state=record_global_source_state,
                )
            except SvError as exc:
                if not _is_tty_browser_unavailable_error(exc):
                    raise

        _print_repo_table(config.repos, paths)
        return 0

    if args.repo_command is None:
        raise SvError("Specify a repo command or use 'sv repo -l'.")

    raise SvError(f"Unknown repo command: {args.repo_command}")


def _print_repo_table(repos: Sequence[RepoConfig], paths: SvPaths) -> None:
    rows = [[repo.id, repo.url, str(paths.source_repo_for(repo.id))] for repo in repos]
    print(
        format_table(
            ["Repo", "URL", "Cache"],
            rows,
            max_widths={"Repo": 32, "URL": 64, "Cache": 72},
            min_widths={"Repo": 12, "URL": 20, "Cache": 20},
            max_table_width=_table_width(),
        )
    )


def _handle_repo_browser(
    repos: Sequence[RepoConfig],
    *,
    paths: SvPaths,
    cwd: Path,
    adapter: PiAdapter,
    git_runner,
    record_global_source_state: bool,
) -> int:
    rows = [[repo.id, repo.url, str(paths.source_repo_for(repo.id))] for repo in repos]
    repos_by_id = {repo.id: repo for repo in repos}

    def open_repo_skills(row: Sequence[str]) -> None:
        repo_id = str(row[0])
        repo = repos_by_id.get(repo_id)
        if repo is None:
            return
        catalog = _update_sources_and_catalog_from_repos(
            [repo],
            paths,
            git_runner,
            update=True,
            record_global_source_state=record_global_source_state,
            lightweight_discovery=True,
            warn=lambda message: print(message, file=sys.stderr),
        )
        _browse_repo_source_skills(catalog, cwd=cwd, adapter=adapter)

    _browse_tty_table(
        ["Repo", "URL", "Cache"],
        rows,
        on_detail=open_repo_skills,
    )
    return 0


def _browse_repo_source_skills(
    catalog: Sequence[SourceSkill], *, cwd: Path, adapter: PiAdapter
) -> None:
    if not catalog:
        print("No valid skills found in this source repo.")
        return

    context = _detect_local_context(cwd)
    rows, entries = _interactive_source_skill_rows(catalog)

    def install_one(row: Sequence[str]) -> None:
        entry = _entry_for_interactive_row(row, rows, entries)
        if entry is None:
            return
        result = _add_skill_to_context(entry, adapter, context)
        _print_add_result(result)
        _refresh_local_index_if_needed(context)

    def install_all(_row: Sequence[str]) -> None:
        _handle_add_all(catalog, cwd=cwd, adapter=adapter, context=context)

    _browse_tty_table(
        _source_skill_headers(),
        rows,
        on_detail=install_one,
        key_actions={"a": install_all},
        key_help="a install all",
        clear_on_exit=True,
        row_ranker=_source_skill_ranker(entries),
    )


def _handle_repo_remove_interactive(
    paths: SvPaths,
    *,
    skill_selector: SkillSelector,
    yes: bool,
) -> int:
    config = ConfigStore(paths).load()
    if not config.repos:
        _print_no_source_repos_configured()
        return 0

    repo_ids = [repo.id for repo in config.repos]
    repos_by_id = {repo.id: repo for repo in config.repos}
    selected_repo_ids = _call_selector(
        skill_selector,
        repo_ids,
        item_label=lambda repo_id: _repo_removal_label(repos_by_id[repo_id]),
    )
    if not selected_repo_ids:
        print("No repos selected.")
        return 0

    selected_repos = [repos_by_id[repo_id] for repo_id in selected_repo_ids]
    if not yes and not _can_prompt_for_confirmation():
        raise SvError("Non-TTY 'sv repo remove -l' requires --yes.")
    if not _confirm_repo_removal(selected_repos, yes=yes):
        print("No repos removed.")
        return 0

    for repo in selected_repos:
        try:
            removed = ConfigStore(paths).remove_repo(repo.id)
        except ValueError as exc:
            raise SvError(str(exc)) from exc
        _remove_global_source_state(paths, removed.id)
        print(f"Removed repo {removed.id}")
    return 0


def _repo_removal_label(repo: RepoConfig) -> str:
    repo_id = _escape_control_characters(repo.id)
    url = _escape_control_characters(repo.url)
    return f"Repo: {repo_id} | URL: {url}"


def _confirm_repo_removal(repos: Sequence[RepoConfig], *, yes: bool) -> bool:
    if yes:
        return True
    if not _can_prompt_for_confirmation():
        return True
    rows = [[repo.id, repo.url] for repo in repos]
    print("The following repos will be removed:")
    print(
        format_table(
            ["Repo", "URL"],
            rows,
            max_widths={"Repo": 32, "URL": 64},
            min_widths={"Repo": 12, "URL": 20},
            max_table_width=_table_width(),
        )
    )
    return _confirm_prompt(f"Remove {len(repos)} repo(s)? [y/N]: ")


def _handle_run(
    args: Sequence[str], cwd: Path, adapter: PiAdapter, process_runner, context: LocalContext
) -> int:
    """Run Pi and turn launch failures into user-facing errors."""
    project_skills_dir = adapter.project_skill_dir(context.repo_root)
    validate_project_skills_for_run(project_skills_dir)
    skills_path = (
        ".pi/skills"
        if cwd.resolve() == context.repo_root.resolve()
        else str(project_skills_dir)
    )
    command = adapter.run_command(_strip_arg_separator(args), skills_path=skills_path)
    try:
        return process_runner(command)
    except FileNotFoundError as exc:
        raise SvError(
            f"Unable to run '{command[0]}'. Make sure it is installed and on PATH."
        ) from exc
    except OSError as exc:
        raise SvError(
            f"Unable to run '{command[0]}': {_escape_control_characters(str(exc))}"
        ) from exc


def _escape_control_characters(value: str) -> str:
    return escape_terminal_controls(value)


def _escape_output_path(path: Path) -> str:
    return _escape_control_characters(str(path))


def _handle_search(
    query: str,
    catalog: Sequence[SourceSkill],
    *,
    can_browse_tty: bool | None = None,
) -> int:
    if not catalog:
        print("No valid skills found in configured source repos.")
        return 0

    matches = search_source_catalog(catalog, query)
    if not matches:
        safe_query = _escape_control_characters(query)
        print(f"No matching skills found for '{safe_query}'.")
        return 0

    if can_browse_tty is None:
        can_browse_tty = _can_browse_tty()
    if can_browse_tty:
        try:
            return _browse_source_skills(matches)
        except SvError as exc:
            if not _is_tty_browser_unavailable_error(exc):
                raise

    rows = [
        [
            str(rank),
            entry.name,
            entry.repo_id,
            entry.source_relative_path,
            entry.description,
        ]
        for rank, entry in enumerate(matches, start=1)
    ]
    print(
        format_table(
            ["Rank", "Skill", "Repo", "Path", "Description"],
            rows,
            max_widths={
                "Rank": 4,
                "Skill": 28,
                "Repo": 32,
                "Path": 48,
                "Description": 72,
            },
            min_widths={
                "Rank": 4,
                "Skill": 10,
                "Repo": 12,
                "Path": 12,
                "Description": 24,
            },
            max_table_width=_table_width(),
        )
    )
    return 0


def _handle_list(
    catalog: Sequence[SourceSkill], *, can_browse_tty: bool | None = None
) -> int:
    if not catalog:
        print("No valid skills found in configured source repos.")
        return 0

    if can_browse_tty is None:
        can_browse_tty = _can_browse_tty()
    if can_browse_tty:
        try:
            return _browse_source_skills(catalog)
        except SvError as exc:
            if not _is_tty_browser_unavailable_error(exc):
                raise

    duplicates = _duplicate_skill_names(catalog)
    print(
        format_table(
            _source_skill_headers(),
            _source_skill_rows(catalog),
            max_widths=_source_skill_max_widths(),
            min_widths=_source_skill_min_widths(),
            max_table_width=_table_width(),
        )
    )
    if duplicates:
        duplicate_names = set(duplicates)
        print()
        _print_wrapped("Duplicate skill names:")
        print(
            format_table(
                ["Skill", "Repo", "Description", "Add as"],
                _duplicate_source_skill_rows(catalog, duplicate_names=duplicate_names),
                max_widths={
                    "Skill": 28,
                    "Repo": 32,
                    "Description": 56,
                    "Add as": 48,
                },
                min_widths={
                    "Skill": 10,
                    "Repo": 12,
                    "Description": 20,
                    "Add as": 16,
                },
                max_table_width=_table_width(),
            )
        )
        _print_wrapped(
            "Tip: use the exact 'Add as' value with 'sv add <repo>:<skill>' "
            "to choose a source explicitly.",
            leading_blank_line=True,
        )
    return 0


def _browse_source_skills(catalog: Sequence[SourceSkill]) -> int:
    rows, entries = _interactive_source_skill_rows(catalog)

    def show_details(row: Sequence[str]) -> None:
        entry = _entry_for_interactive_row(row, rows, entries)
        if entry is not None:
            _print_source_skill_detail(entry)

    _browse_tty_table(
        _source_skill_headers(),
        rows,
        on_detail=show_details,
        row_ranker=_source_skill_ranker(entries),
    )
    return 0


def _source_skill_ranker(entries: Sequence[SourceSkill]) -> Callable[[str], list[int]]:
    positions_by_id: dict[int, list[int]] = {}
    for index, entry in enumerate(entries):
        positions_by_id.setdefault(id(entry), []).append(index)

    def rank(query: str) -> list[int]:
        used_by_id: dict[int, int] = {}
        ranked_indices: list[int] = []
        for entry in search_source_catalog(entries, query):
            entry_id = id(entry)
            used = used_by_id.get(entry_id, 0)
            positions = positions_by_id.get(entry_id, [])
            if used < len(positions):
                ranked_indices.append(positions[used])
                used_by_id[entry_id] = used + 1
        return ranked_indices

    return rank


def _interactive_source_skill_rows(
    entries: Sequence[SourceSkill],
) -> tuple[list[list[str]], list[SourceSkill]]:
    rows = [
        [
            entry.name,
            _interactive_source_label(entry, entries),
            entry.description,
            str(index),
        ]
        for index, entry in enumerate(entries)
    ]
    return rows, list(entries)


def _interactive_source_label(
    entry: SourceSkill, entries: Sequence[SourceSkill]
) -> str:
    same_skill_and_repo = [
        other
        for other in entries
        if other.name == entry.name and other.repo_id == entry.repo_id
    ]
    if len({other.source_relative_path for other in same_skill_and_repo}) > 1:
        return f"{entry.repo_id}:{entry.source_relative_path}"
    return entry.repo_id


def _entry_for_interactive_row(
    row: Sequence[str], rows: Sequence[Sequence[str]], entries: Sequence[SourceSkill]
) -> SourceSkill | None:
    row_values = [str(value) for value in row]
    if len(row_values) > len(_source_skill_headers()):
        try:
            row_index = int(row_values[len(_source_skill_headers())])
        except ValueError:
            row_index = -1
        if 0 <= row_index < len(entries):
            return entries[row_index]

    for candidate_row, entry in zip(rows, entries, strict=False):
        if list(candidate_row) == row_values:
            return entry
    return None


def _print_source_skill_detail(entry: SourceSkill) -> None:
    print()
    _print_tty_detail(
        "Skill details",
        [
            ("Skill", _escape_control_characters(entry.name)),
            ("Source", _escape_control_characters(entry.repo_id)),
            ("Path", _escape_control_characters(entry.source_relative_path)),
            ("Description", _escape_control_characters(entry.description)),
        ],
    )


def _browse_tty_table(headers: Sequence[str], rows: Sequence[Sequence[str]], **kwargs):
    return browse_tty_table(headers, rows, **kwargs)


def _is_tty_browser_unavailable_error(exc: SvError) -> bool:
    return str(exc).startswith("Interactive table browsing ")


def _handle_add(
    skill_reference: str,
    catalog: Sequence[SourceSkill],
    cwd: Path,
    adapter: PiAdapter,
    skill_chooser: SkillChooser,
    context: LocalContext | None = None,
    *,
    replace_existing: bool = False,
) -> int:
    qualified = find_qualified_catalog_entry(catalog, skill_reference)
    context = _detect_local_context(cwd) if context is None else context
    if qualified is not None:
        result = _add_skill_to_context(
            qualified, adapter, context, replace_existing=replace_existing
        )
        _print_add_result(result)
        _refresh_local_index_if_needed(context)
        return 0

    if ":" in skill_reference:
        safe_reference = _escape_control_characters(skill_reference)
        raise SvError(
            f"Skill '{safe_reference}' was not found in configured source repos."
        )

    matches = find_catalog_matches(catalog, skill_reference)
    if not matches:
        raise SvError(
            f"Skill '{skill_reference}' was not found in configured source repos."
        )
    if len(matches) == 1:
        result = _add_skill_to_context(
            matches[0], adapter, context, replace_existing=replace_existing
        )
        _print_add_result(result)
        _refresh_local_index_if_needed(context)
        return 0

    if skill_chooser is _choose_skill and not _can_prompt_for_skill_choice():
        raise SvError(_ambiguous_skill_error(skill_reference, matches))

    print(f"Multiple source skills match '{skill_reference}':")
    if _source_choice_table_needed(skill_chooser):
        print(_format_source_skill_choices(matches))
    chosen = skill_chooser(matches)
    if chosen is None:
        print(
            "No skill selected. "
            f"Rerun with {_source_skill_reference(matches[0])} to choose explicitly."
        )
        return 0

    result = _add_skill_to_context(
        chosen, adapter, context, replace_existing=replace_existing
    )
    _print_add_result(result)
    _refresh_local_index_if_needed(context)
    return 0


def _handle_add_all(
    catalog: Sequence[SourceSkill],
    cwd: Path,
    adapter: PiAdapter,
    context: LocalContext | None = None,
    *,
    skill_chooser: SkillChooser | None = None,
    replace_existing: bool = False,
) -> int:
    skill_chooser = _choose_skill if skill_chooser is None else skill_chooser
    context = _detect_local_context(cwd) if context is None else context
    duplicate_names = _duplicate_skill_names(catalog)
    if duplicate_names:
        if skill_chooser is _choose_skill and not _can_prompt_for_skill_choice():
            _raise_on_duplicate_source_skills(catalog)
        resolved_catalog = _resolve_selected_duplicate_source_skills(
            catalog, skill_chooser=skill_chooser
        )
        if resolved_catalog is None:
            return 0
        catalog = resolved_catalog
    _validate_local_index_refresh_config(context)
    result = _add_all_skills_to_context(
        catalog, cwd, adapter, context, replace_existing=replace_existing
    )
    if not result.results:
        print("No valid skills found in configured source repos.")
        return 0

    for skill_result in result.results:
        _print_add_result(skill_result)
    _refresh_local_index_if_needed(context)
    return 0


def _handle_add_interactive(
    catalog: Sequence[SourceSkill],
    cwd: Path,
    adapter: PiAdapter,
    skill_selector: SkillSelector,
    skill_chooser: SkillChooser | None = None,
    context: LocalContext | None = None,
    *,
    replace_existing: bool = False,
) -> int:
    skill_chooser = _choose_skill if skill_chooser is None else skill_chooser
    context = _detect_local_context(cwd) if context is None else context
    if not catalog:
        print("No valid skills found in configured source repos.")
        return 0

    selected_skills = _call_selector(
        skill_selector,
        catalog,
        item_columns=_source_skill_picker_columns,
        header_columns=["Skill", "Source", "Description"],
        item_ranker=_source_skill_ranker(catalog),
        enter_action_label="add",
    )
    if not selected_skills:
        print("No skills selected.")
        return 0

    resolved_skills = _resolve_selected_duplicate_source_skills(
        selected_skills, skill_chooser=skill_chooser
    )
    if resolved_skills is None:
        return 0

    for entry in resolved_skills:
        result = _add_skill_to_context(
            entry, adapter, context, replace_existing=replace_existing
        )
        _print_add_result(result)
    _refresh_local_index_if_needed(context)
    return 0


def _add_skill_to_context(
    entry: SourceSkill,
    adapter: PiAdapter,
    context: LocalContext,
    *,
    replace_existing: bool = False,
) -> AddSkillResult:
    _validate_local_index_refresh_config(context)
    if context.is_skill_vault:
        should_replace = _resolve_vault_replacement(
            entry, context.vault_skills_dir, replace_existing=replace_existing
        )
        return add_vault_skill(
            entry, context.vault_skills_dir, replace_existing=should_replace
        )
    return add_project_skill(entry, _project_skills_dir(adapter, context))


def _add_all_skills_to_context(
    catalog: Sequence[SourceSkill],
    cwd: Path,
    adapter: PiAdapter,
    context: LocalContext,
    *,
    replace_existing: bool = False,
):
    if context.is_skill_vault:
        replace_indexes: set[int] = set()
        for index, entry in enumerate(catalog):
            should_replace = _resolve_vault_replacement(
                entry,
                context.vault_skills_dir,
                replace_existing=replace_existing,
            )
            if should_replace:
                replace_indexes.add(index)
        return add_all_vault_skills(
            catalog,
            context.vault_skills_dir,
            replace_existing=replace_existing,
            replace_existing_indexes=frozenset(replace_indexes),
        )
    return add_all_project_skills(catalog, _project_skills_dir(adapter, context))


def _project_skills_dir(adapter: PiAdapter, context: LocalContext) -> Path:
    return adapter.project_skill_dir(context.repo_root)


def _resolve_vault_replacement(
    entry: SourceSkill, vault_skills_dir: Path, *, replace_existing: bool
) -> bool:
    _ensure_safe_vault_skills_dir_for_replacement(vault_skills_dir)
    skill_name = normalize_skill_name(entry.name)
    target = vault_skills_dir / skill_name
    if not target.exists() and not target.is_symlink():
        return False
    if target.is_symlink() or not target.is_dir():
        return replace_existing

    if replace_existing:
        _warn_if_vault_skill_has_local_edits(skill_name, target, vault_skills_dir)
        return True

    if not _can_prompt_for_vault_replacement():
        raise SvError(
            f"Vault skill '{skill_name}' already exists at {_escape_output_path(target)}. "
            "Use --replace to replace it."
        )

    print(
        f"Vault skill '{skill_name}' already exists at {_escape_output_path(target)}."
    )
    _warn_if_vault_skill_has_local_edits(skill_name, target, vault_skills_dir)
    choice = input(f"Replace existing vault skill '{skill_name}'? [y/N]: ").strip()
    return choice.lower() in {"y", "yes"}


def _can_prompt_for_vault_replacement() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ensure_safe_vault_skills_dir_for_replacement(vault_skills_dir: Path) -> None:
    for path in (vault_skills_dir.parent, vault_skills_dir):
        try:
            if path.is_symlink():
                raise SvError(
                    f"Refusing to use symlinked vault skills path at {_escape_output_path(path)}."
                )
        except OSError as exc:
            raise SvError(
                f"Failed to inspect vault skills path {_escape_output_path(path)}: {exc}"
            ) from exc


def _warn_if_vault_skill_has_local_edits(
    skill_name: str, target: Path, vault_skills_dir: Path
) -> None:
    warning = _vault_skill_replacement_warning(skill_name, target, vault_skills_dir)
    if warning is None:
        return
    print(warning, file=sys.stderr)


def _vault_skill_replacement_warning(
    skill_name: str, target: Path, vault_skills_dir: Path
) -> str | None:
    from sv.hashing import sha256_skill_directory

    for entry in ProjectManifestStore(vault_skills_dir).load().values():
        if (
            entry.name == skill_name
            and entry.target_kind == "skill-vault"
            and entry.target_agent is None
            and entry.target_path == f"skills/{skill_name}"
        ):
            if entry.installed_content_hash is None:
                return (
                    f"warning: vault skill '{skill_name}' has no recorded baseline; "
                    "replacing it may discard local edits."
                )
            local_hash = sha256_skill_directory(target, expected_name=skill_name)
            if local_hash != entry.installed_content_hash:
                return (
                    f"warning: vault skill '{skill_name}' has local edits that "
                    "will be replaced."
                )
            return None
    return (
        f"warning: vault skill '{skill_name}' is not sv-managed; replacing it may "
        "discard local edits."
    )


def _validate_local_index_refresh_config(context: LocalContext) -> None:
    if context.should_refresh_index:
        scan_config = load_index_scan_config(context.repo_root)
        validate_index_scan_config_paths(context.repo_root, scan_config)


def _refresh_local_index_if_needed(context: LocalContext) -> None:
    if not context.should_refresh_index:
        return
    kind: IndexKind = "skill-vault" if context.is_skill_vault else "project-index"
    document = _scan_repo_for_configured_index(context.repo_root, kind=kind)
    save_index(index_path(context.repo_root), document)
    if document.kind == "skill-vault":
        update_readme_skill_table(readme_path(context.repo_root), document)


def _print_add_result(result: AddSkillResult) -> None:
    requested_repo = (
        _escape_control_characters(result.repo_id) if result.repo_id else None
    )
    existing_repo = (
        _escape_control_characters(result.existing_repo_id)
        if result.existing_repo_id
        else None
    )
    requested_source = (
        _escape_control_characters(result.source_reference)
        if result.source_reference
        else requested_repo
    )
    existing_source = (
        _escape_control_characters(result.existing_source_reference)
        if result.existing_source_reference
        else existing_repo
    )
    target = _escape_output_path(result.target)
    source = f" from {requested_source}" if requested_source else ""
    skill_label = _result_skill_label(result.target_kind)
    if result.status == "exists":
        message = f"{skill_label.capitalize()} '{result.skill}' already exists at {target}"
        if existing_source and requested_source == existing_source:
            message += f" from {existing_source}."
        elif existing_source and requested_source:
            message += (
                f" (currently from {existing_source}; requested {requested_source}). "
                f"Run 'sv remove {result.skill}' first if you want to switch sources."
            )
        elif requested_source:
            message += (
                f" (no sv origin recorded; requested {requested_source}). "
                f"Run 'sv remove {result.skill}' first if you want to replace it."
            )
        print(message)
        return

    if result.status == "replaced":
        print(f"Replaced {skill_label} '{result.skill}'{source} at {target}")
        return

    print(f"Added {skill_label} '{result.skill}'{source} to {target}")


def _result_skill_label(target_kind: str) -> str:
    if target_kind == "skill-vault":
        return "vault skill"
    return "Pi skill"


def _result_skills_label(target_kind: str) -> str:
    if target_kind == "skill-vault":
        return "vault skills"
    return "Pi skills"


def _handle_remove(
    skill: str, cwd: Path, adapter: PiAdapter, context: LocalContext | None = None
) -> int:
    context = _detect_local_context(cwd) if context is None else context
    _validate_local_index_refresh_config(context)
    if context.is_skill_vault:
        result = remove_vault_skill(skill, context.vault_skills_dir)
    else:
        result = remove_project_skill(skill, _project_skills_dir(adapter, context))
    _print_remove_result(result)
    _refresh_local_index_if_needed(context)
    return 0


def _handle_remove_all(
    cwd: Path,
    adapter: PiAdapter,
    context: LocalContext | None = None,
    *,
    yes: bool,
) -> int:
    context = _detect_local_context(cwd) if context is None else context
    project_skills_dir = _removal_skills_dir(adapter, context)
    entries = _managed_removal_entries(project_skills_dir, context)
    if not entries:
        print(f"No sv-managed {_result_skills_label(_context_target_kind(context))} found to remove.")
        return 0

    if not yes and not _can_prompt_for_confirmation():
        raise SvError("Non-TTY 'sv remove --all' requires --yes.")
    if not _confirm_skill_removal(entries, context=context, yes=yes):
        print("No skills removed.")
        return 0

    _validate_local_index_refresh_config(context)
    _remove_managed_entries(entries, project_skills_dir, context)
    _refresh_local_index_if_needed(context)
    return 0


def _handle_remove_interactive(
    cwd: Path,
    adapter: PiAdapter,
    skill_selector: SkillSelector,
    context: LocalContext | None = None,
    *,
    yes: bool = False,
) -> int:
    context = _detect_local_context(cwd) if context is None else context
    project_skills_dir = _removal_skills_dir(adapter, context)
    entries = _managed_removal_entries(project_skills_dir, context)
    empty_message = (
        "No vault skills found to remove."
        if context.is_skill_vault
        else "No Pi skills found to remove."
    )
    if not entries:
        print(empty_message)
        return 0

    entries_by_name = {entry.name: entry for entry in entries}
    skills = [entry.name for entry in entries]
    selected_skills = _call_selector(
        skill_selector,
        skills,
        item_label=lambda skill: _skill_removal_label(entries_by_name[skill]),
        item_columns=lambda skill: _skill_removal_columns(entries_by_name[skill]),
        header_columns=["Skill", "Target", "Source/Status"],
        enter_action_label="remove",
    )
    if not selected_skills:
        print("No skills selected.")
        return 0

    selected_entries = [entries_by_name[skill] for skill in selected_skills]
    if not yes and not _can_prompt_for_confirmation():
        raise SvError("Non-TTY 'sv remove -l' requires --yes.")
    if not _confirm_skill_removal(selected_entries, context=context, yes=yes):
        print("No skills removed.")
        return 0

    _validate_local_index_refresh_config(context)
    _remove_managed_entries(selected_entries, project_skills_dir, context)
    _refresh_local_index_if_needed(context)
    return 0


def _removal_skills_dir(adapter: PiAdapter, context: LocalContext) -> Path:
    if context.is_skill_vault:
        return context.vault_skills_dir
    return _project_skills_dir(adapter, context)


def _managed_removal_entries(
    project_skills_dir: Path, context: LocalContext
) -> list[ManifestEntry]:
    if context.is_skill_vault:
        return _vault_status_entries(project_skills_dir)
    return _project_status_entries(project_skills_dir)


def _context_target_kind(context: LocalContext) -> str:
    if context.is_skill_vault:
        return "skill-vault"
    return "project-agent"


def _skill_removal_label(entry: ManifestEntry) -> str:
    name, target, source_status = _skill_removal_columns(entry)
    return f"Skill: {name} | Target: {target} | Source/Status: {source_status}"


def _skill_removal_columns(entry: ManifestEntry) -> list[str]:
    return [
        _escape_control_characters(entry.name),
        _escape_control_characters(entry.target_path or ""),
        _removal_source_status(entry),
    ]


def _removal_source_status(entry: ManifestEntry) -> str:
    status = _manifest_source_reference(entry)
    flags = _status_state_label(entry)
    if flags != "ok":
        status = f"{status} ({flags})"
    return status


def _confirm_skill_removal(
    entries: Sequence[ManifestEntry], *, context: LocalContext, yes: bool
) -> bool:
    if yes:
        return True
    if not _can_prompt_for_confirmation():
        return True
    target_kind = _context_target_kind(context)
    label = _result_skills_label(target_kind)
    print(f"The following sv-managed {label} will be removed:")
    print(
        format_table(
            ["Skill", "Target", "Source/Status"],
            [
                [entry.name, entry.target_path or "", _removal_source_status(entry)]
                for entry in entries
            ],
            max_widths={"Skill": 28, "Target": 48, "Source/Status": 64},
            min_widths={"Skill": 10, "Target": 16, "Source/Status": 18},
            max_table_width=_table_width(),
        )
    )
    return _confirm_prompt(f"Remove {len(entries)} {label}? [y/N]: ")


def _remove_managed_entries(
    entries: Sequence[ManifestEntry], project_skills_dir: Path, context: LocalContext
) -> None:
    for entry in entries:
        if _manifest_target_is_unavailable(entry):
            _prune_unavailable_manifest_entry(project_skills_dir, entry)
            _print_pruned_unavailable_result(entry, project_skills_dir, context)
            continue
        if context.is_skill_vault:
            result = remove_vault_skill(entry.name, project_skills_dir)
        else:
            result = remove_project_skill(entry.name, project_skills_dir)
        _print_remove_result(result)


def _call_selector(
    selector: SkillSelector,
    items: Sequence[Any],
    **kwargs: Any,
) -> list[Any]:
    if kwargs and not _selector_accepts_keyword_arguments(selector, kwargs):
        if _selector_accepts_items_only(selector):
            return selector(items)
    return selector(items, **kwargs)


def _selector_accepts_keyword_arguments(
    selector: SkillSelector, kwargs: Mapping[str, Any]
) -> bool:
    try:
        signature = inspect.signature(selector)
    except (TypeError, ValueError):
        return True

    parameters = signature.parameters
    if any(param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters.values()):
        return True
    return all(
        name in parameters
        and parameters[name].kind
        in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
        for name in kwargs
    )


def _selector_accepts_items_only(selector: SkillSelector) -> bool:
    try:
        inspect.signature(selector).bind(object())
    except (TypeError, ValueError):
        return False
    return True


def _can_prompt_for_confirmation() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _confirm_prompt(prompt: str) -> bool:
    return input(prompt).strip().lower() in {"y", "yes"}


def _prune_unavailable_manifest_entry(
    project_skills_dir: Path, entry: ManifestEntry
) -> None:
    manifest = ProjectManifestStore(project_skills_dir).load()
    for key, existing_entry in list(manifest.items()):
        if _same_manifest_target(existing_entry, entry):
            del manifest[key]
            ProjectManifestStore(project_skills_dir).save(manifest)
            return


def _same_manifest_target(first: ManifestEntry, second: ManifestEntry) -> bool:
    return (
        first.name == second.name
        and first.target_kind == second.target_kind
        and first.target_agent == second.target_agent
        and first.target_path == second.target_path
    )


def _print_remove_result(result: RemoveSkillResult) -> None:
    target = _escape_output_path(result.target)
    print(f"Removed {_result_skill_label(result.target_kind)} '{result.skill}' from {target}")


def _manifest_target_is_unavailable(entry: ManifestEntry) -> bool:
    return entry.target_missing or entry.target_invalid


def _print_pruned_unavailable_result(
    entry: ManifestEntry, project_skills_dir: Path, context: LocalContext
) -> None:
    target = _escape_output_path(project_skills_dir / entry.name)
    reason = "invalid" if entry.target_invalid else "missing"
    detail = "is not a directory" if entry.target_invalid else "was already gone"
    label = _result_skill_label(_context_target_kind(context))
    print(
        f"Pruned {reason} {label} '{entry.name}' from sv manifest "
        f"(target {target} {detail})."
    )


def _handle_sync(
    catalog: Sequence[SourceSkill],
    cwd: Path,
    adapter: PiAdapter,
    context: LocalContext | None = None,
) -> int:
    context = _detect_local_context(cwd) if context is None else context
    _validate_local_index_refresh_config(context)
    if context.is_skill_vault:
        result = sync_vault_skills(catalog, context.vault_skills_dir)
    else:
        result = sync_project_skills(catalog, _project_skills_dir(adapter, context))
    _print_sync_result(result)
    _refresh_local_index_if_needed(context)
    return 0


def _handle_status(
    cwd: Path,
    paths: SvPaths,
    adapter: PiAdapter,
    git_runner,
    record_global_source_state: bool,
    *,
    cache_policy: CachePolicy,
    context: LocalContext | None = None,
) -> int:
    context = (
        _detect_local_context(cwd, global_manifest_file=paths.global_manifest_file)
        if context is None
        else context
    )
    if context.is_skill_vault:
        return _handle_vault_status(
            context,
            paths=paths,
            git_runner=git_runner,
            record_global_source_state=record_global_source_state,
            cache_policy=cache_policy,
        )
    manifest = project_manifest_path(context.repo_root)
    has_project_manifest = manifest.is_file() and _is_non_git_project_manifest(
        manifest, global_manifest_file=paths.global_manifest_file
    )
    if (
        not (context.repo_root / ".git").exists()
        and context.index_kind is None
        and not has_project_manifest
    ):
        return _handle_global_status(paths)

    project_skills_dir = adapter.project_skill_dir(context.repo_root)
    _reject_symlinked_status_project_skills_path(project_skills_dir)
    entries = _project_status_entries(project_skills_dir)
    if not entries:
        print("No sv-managed Pi skills found in this project.")
        return 0

    if _entries_require_source_status_refresh(entries):
        catalog = _status_catalog_if_configured(
            paths,
            git_runner,
            record_global_source_state=record_global_source_state,
            cache_policy=cache_policy,
        )
        if catalog is None:
            refresh_project_skill_local_states(project_skills_dir)
        else:
            refresh_project_skill_states(catalog, project_skills_dir)
        entries = _project_status_entries(project_skills_dir)

    print("Project sv-managed Pi skills")
    print(
        format_table(
            ["Skill", "Agent", "Target", "Source", "State"],
            _project_status_rows(entries),
            max_widths={
                "Skill": 28,
                "Agent": 8,
                "Target": 48,
                "Source": 48,
                "State": 36,
            },
            min_widths={
                "Skill": 10,
                "Agent": 5,
                "Target": 16,
                "Source": 16,
                "State": 8,
            },
            max_table_width=_table_width(),
        )
    )
    return 0


def _handle_vault_status(
    context: LocalContext,
    *,
    paths: SvPaths,
    git_runner,
    record_global_source_state: bool,
    cache_policy: CachePolicy,
) -> int:
    vault_skills_dir = context.vault_skills_dir
    _reject_symlinked_status_vault_skills_path(vault_skills_dir)
    entries = _vault_status_entries(vault_skills_dir)
    if _entries_require_source_status_refresh(entries):
        catalog = _status_catalog_if_configured(
            paths,
            git_runner,
            record_global_source_state=record_global_source_state,
            cache_policy=cache_policy,
        )
        if catalog is None:
            refresh_vault_skill_local_states(vault_skills_dir)
        else:
            refresh_vault_skill_states(catalog, vault_skills_dir)
        entries = _vault_status_entries(vault_skills_dir)

    freshness = _vault_freshness_status(context.repo_root)
    print("Skill-vault status")
    print(f"Index: {freshness.index_status}")
    print(f"README: {freshness.readme_status}")

    if not entries:
        print("No sv-managed vault skills found in this skill-vault.")
        return 0

    print()
    print("Skill-vault sv-managed skills")
    print(
        format_table(
            ["Skill", "Target", "Source", "State"],
            _vault_status_rows(entries),
            max_widths={
                "Skill": 28,
                "Target": 48,
                "Source": 48,
                "State": 36,
            },
            min_widths={
                "Skill": 10,
                "Target": 16,
                "Source": 16,
                "State": 8,
            },
            max_table_width=_table_width(),
        )
    )
    return 0


@dataclass(frozen=True)
class _VaultFreshnessStatus:
    index_status: str
    readme_status: str


def _vault_freshness_status(repo_root: Path) -> _VaultFreshnessStatus:
    path = index_path(repo_root)
    current = _scan_repo_for_configured_index(repo_root, kind="skill-vault")
    if not path.is_file():
        index_status = "missing"
    else:
        existing = load_index(path)
        index_status = "fresh" if _index_skills_are_fresh(existing, current) else "stale"
    readme_status = (
        "fresh"
        if readme_skill_table_is_fresh(readme_path(repo_root), current)
        else "stale"
    )
    return _VaultFreshnessStatus(index_status=index_status, readme_status=readme_status)


def _index_skills_are_fresh(existing: IndexDocument, current: IndexDocument) -> bool:
    return (
        existing.kind == current.kind
        and _canonical_index_skills(existing.skills) == _canonical_index_skills(current.skills)
    )


def _canonical_index_skills(
    skills: Sequence[IndexSkillEntry],
) -> tuple[IndexSkillEntry, ...]:
    return tuple(
        sorted(
            skills,
            key=lambda entry: (
                entry.name,
                entry.source_path,
                entry.description,
                entry.content_hash,
                entry.skill_file_hash,
            ),
        )
    )


def _handle_global_status(paths: SvPaths) -> int:
    config = ConfigStore(paths).load() if paths.config_file.exists() else SvConfig()
    states = _load_global_manifest_for_source_state(paths) or {}
    repo_ids = sorted({repo.id for repo in config.repos} | set(states))
    if not repo_ids:
        print("No global skill sources configured.")
        return 0

    rows = []
    config_by_id = {repo.id: repo for repo in config.repos}
    for repo_id in repo_ids:
        repo = config_by_id.get(repo_id)
        state = states.get(repo_id)
        repo_url = state.repo_url if state is not None else (repo.url if repo else "")
        rows.append(
            [
                _escape_control_characters(repo_id),
                _escape_control_characters(repo_url),
                _escape_control_characters(state.backend if state and state.backend else "unknown"),
                _escape_control_characters(_global_refresh_label(state)),
                _escape_control_characters(_global_hash_label(state.index_hash if state else None)),
                _escape_control_characters(_global_hash_label(state.catalog_hash if state else None)),
                str(state.catalog_skill_count) if state and state.catalog_skill_count is not None else "-",
                _escape_control_characters(_global_health_label(state)),
            ]
        )

    print("Global skill source status")
    print(
        format_table(
            ["Repo", "URL", "Backend", "Refresh", "Index", "Catalog", "Skills", "Health"],
            rows,
            max_widths={
                "Repo": 32,
                "URL": 48,
                "Backend": 16,
                "Refresh": 24,
                "Index": 24,
                "Catalog": 24,
                "Skills": 6,
                "Health": 32,
            },
            min_widths={
                "Repo": 12,
                "URL": 16,
                "Backend": 8,
                "Refresh": 8,
                "Index": 6,
                "Catalog": 8,
                "Skills": 6,
                "Health": 8,
            },
            max_table_width=_table_width(),
        )
    )
    return 0


def _global_refresh_label(state: GlobalSourceState | None) -> str:
    if state is None:
        return "never"
    status = state.last_refresh_status or "unknown"
    if state.last_refresh_finished_at:
        return f"{status} {state.last_refresh_finished_at}"
    return status


def _global_health_label(state: GlobalSourceState | None) -> str:
    if state is None:
        return "unknown"
    status = state.health_status or "unknown"
    if state.health_details:
        return f"{status}: {state.health_details}"
    if state.last_refresh_error:
        return f"{status}: {state.last_refresh_error}"
    return status


def _global_hash_label(value: str | None) -> str:
    if not value:
        return "-"
    if not is_sha256_digest(value):
        return value
    visible_hex = 12
    return f"{value.removeprefix(SHA256_PREFIX)[:visible_hex]}..."


def _status_catalog_if_configured(
    paths: SvPaths,
    git_runner,
    *,
    record_global_source_state: bool,
    cache_policy: CachePolicy | None = None,
) -> list[SourceSkill] | None:
    if not paths.config_file.exists():
        return None
    config = ConfigStore(paths).load()
    if not config.repos:
        return None
    return _catalog_for_source_command(
        config.repos,
        paths,
        git_runner,
        policy=cache_policy
        or CachePolicy.force_refresh(allow_stale_on_error=False),
        record_global_source_state=record_global_source_state,
        lightweight_discovery=True,
        allow_partial_failures=False,
    )


def _reject_symlinked_status_project_skills_path(project_skills_dir: Path) -> None:
    for path in (project_skills_dir.parent, project_skills_dir):
        if path.is_symlink():
            raise SvError(
                f"Refusing to inspect symlinked Pi skills path at {_escape_output_path(path)}."
            )


def _reject_symlinked_status_vault_skills_path(vault_skills_dir: Path) -> None:
    if vault_skills_dir.is_symlink():
        raise SvError(
            f"Refusing to inspect symlinked vault skills path at {_escape_output_path(vault_skills_dir)}."
        )


def _entries_require_source_status_refresh(
    entries: Sequence[ManifestEntry],
) -> bool:
    return any(not _manifest_target_is_unavailable(entry) for entry in entries)


def _project_status_entries(project_skills_dir: Path) -> list[ManifestEntry]:
    entries: list[ManifestEntry] = []
    for entry in ProjectManifestStore(project_skills_dir).load().values():
        if not _is_pi_project_manifest_entry(entry):
            continue
        target = project_skills_dir / entry.name
        if target.is_symlink():
            raise SvError(
                f"Refusing to inspect symlinked Pi skill '{entry.name}' at {_escape_output_path(target)}."
            )
        if target.is_dir():
            entries.append(entry)
        elif target.exists():
            entries.append(replace(entry, target_invalid=True))
        else:
            entries.append(replace(entry, target_missing=True))
    return sorted(entries, key=lambda item: item.name)


def _is_pi_project_manifest_entry(entry: ManifestEntry) -> bool:
    if entry.target_kind != "project-agent" or entry.target_agent != "pi":
        return False
    try:
        normalize_skill_name(entry.name)
    except SvError as exc:
        raise SvError(f"Invalid Pi skill directory in sv manifest: {exc}") from exc
    return entry.target_path == f".pi/skills/{entry.name}"


def _vault_status_entries(vault_skills_dir: Path) -> list[ManifestEntry]:
    entries: list[ManifestEntry] = []
    for entry in ProjectManifestStore(vault_skills_dir).load().values():
        if not _is_vault_manifest_entry(entry):
            continue
        target = vault_skills_dir / entry.name
        if target.is_symlink():
            raise SvError(
                f"Refusing to inspect symlinked vault skill '{entry.name}' at {_escape_output_path(target)}."
            )
        if target.is_dir():
            entries.append(entry)
        elif target.exists():
            entries.append(replace(entry, target_invalid=True))
        else:
            entries.append(replace(entry, target_missing=True))
    return sorted(entries, key=lambda item: item.name)


def _is_vault_manifest_entry(entry: ManifestEntry) -> bool:
    if entry.target_kind != "skill-vault" or entry.target_agent is not None:
        return False
    try:
        normalize_skill_name(entry.name)
    except SvError as exc:
        raise SvError(f"Invalid vault skill directory in sv manifest: {exc}") from exc
    return entry.target_path == f"skills/{entry.name}"


def _project_status_rows(entries: Sequence[ManifestEntry]) -> list[list[str]]:
    return [
        [
            _escape_control_characters(entry.name),
            _escape_control_characters(entry.target_agent or ""),
            _escape_control_characters(entry.target_path or ""),
            _manifest_source_reference(entry),
            _status_state_label(entry),
        ]
        for entry in entries
    ]


def _vault_status_rows(entries: Sequence[ManifestEntry]) -> list[list[str]]:
    return [
        [
            _escape_control_characters(entry.name),
            _escape_control_characters(entry.target_path or ""),
            _manifest_source_reference(entry),
            _status_state_label(entry),
        ]
        for entry in entries
    ]


def _manifest_source_reference(entry: ManifestEntry) -> str:
    return _escape_control_characters(f"{entry.repo_id}:{entry.source_path}")


def _status_state_label(entry: ManifestEntry) -> str:
    flags: list[str] = []
    if entry.target_missing:
        flags.append("missing")
    if entry.target_invalid:
        flags.append("invalid target")
    if entry.modified:
        flags.append("modified")
    if entry.update_available:
        flags.append("update available")
    if entry.orphan:
        flags.append("orphan")
    return ", ".join(flags) if flags else "ok"


def _handle_update(
    cwd: Path,
    paths: SvPaths,
    adapter: PiAdapter,
    git_runner,
    record_global_source_state: bool,
    *,
    cache_policy: CachePolicy,
    context: LocalContext | None = None,
) -> int:
    config = _load_config_for_source_command(paths)
    print("Updating source repos...")
    catalog = _catalog_for_source_command(
        config.repos,
        paths,
        git_runner,
        policy=cache_policy,
        record_global_source_state=record_global_source_state,
        lightweight_discovery=True,
        allow_partial_failures=False,
    )
    context = _detect_local_context(cwd) if context is None else context
    _validate_local_index_refresh_config(context)
    if context.is_skill_vault:
        print("Updating vault skills...")
        result = update_vault_skills(catalog, context.vault_skills_dir)
    else:
        print("Updating project skills...")
        result = update_project_skills(catalog, _project_skills_dir(adapter, context))
    _print_update_result(result)
    _refresh_local_index_if_needed(context)
    return 0


def _print_update_result(result: SyncResult) -> None:
    skill_label = _result_skill_label(result.target_kind)
    skills_label = _result_skills_label(result.target_kind)
    if result.no_skills_dir:
        print(f"No {skills_label} found to update.")
        return

    if result.updated:
        for skill in result.updated:
            print(f"Updated {skill_label} '{skill}'.")
    else:
        actionable_skips = [
            skip for skip in result.skipped if skip.reason != "unchanged"
        ]
        if not actionable_skips:
            print(f"No matching {skills_label} found to update.")

    for skip in result.skipped:
        if skip.reason == "unchanged":
            continue
        if skip.reason == "modified":
            print(
                f"Skipped local {skill_label} '{skip.skill}': "
                "local edits preserved."
            )
        elif skip.reason == "source-missing":
            repos = ", ".join(_escape_control_characters(repo) for repo in skip.repo_ids)
            print(
                f"Skipped local {skill_label} '{skip.skill}': recorded source is missing ({repos}). "
                "orphan state recorded."
            )
        else:
            print(f"Skipped local {skill_label} '{skip.skill}'.")


def _print_sync_result(result: SyncResult) -> None:
    skill_label = _result_skill_label(result.target_kind)
    skills_label = _result_skills_label(result.target_kind)
    if result.no_skills_dir:
        print(f"No {skills_label} found to sync.")
        return

    if result.updated:
        for skill in result.updated:
            print(f"Synced {skill_label} '{skill}'.")
    else:
        print(f"No matching {skills_label} found to sync.")

    for skill in result.backfilled:
        print(f"Recorded origin for legacy {skill_label} '{skill}'.")

    for skip in result.skipped:
        if skip.reason == "ambiguous":
            if skip.source_references:
                sources = ", ".join(
                    _escape_control_characters(source)
                    for source in skip.source_references
                )
                print(
                    f"Skipped local {skill_label} '{skip.skill}': "
                    f"multiple source skills match ({sources})."
                )
                continue
            repos = ", ".join(_escape_control_characters(repo) for repo in skip.repo_ids)
            print(
                f"Skipped local {skill_label} '{skip.skill}': multiple source repos match ({repos})."
            )
        elif skip.reason == "source-missing":
            repos = ", ".join(_escape_control_characters(repo) for repo in skip.repo_ids)
            print(
                f"Skipped local {skill_label} '{skip.skill}': recorded source is missing ({repos}). "
                "orphan state recorded."
            )
        else:
            print(f"Skipped local {skill_label} '{skip.skill}'.")


def _raise_on_duplicate_source_skills(catalog: Sequence[SourceSkill]) -> None:
    seen: dict[str, SourceSkill] = {}
    for entry in catalog:
        existing = seen.get(entry.name)
        if existing is not None:
            raise SvError(
                f"Duplicate source skill '{entry.name}' found in multiple sources: "
                f"{_source_skill_reference(existing)}, {_source_skill_reference(entry)}. "
                "Use qualified skill references like repo:skill to choose one source."
            )
        seen[entry.name] = entry


def _resolve_selected_duplicate_source_skills(
    selected_skills: Sequence[SourceSkill], *, skill_chooser: SkillChooser
) -> list[SourceSkill] | None:
    duplicate_groups = _selected_duplicate_source_skill_groups(selected_skills)
    if not duplicate_groups:
        return list(selected_skills)

    chosen_by_name: dict[str, SourceSkill] = {}
    for skill_name, entries in duplicate_groups.items():
        if skill_chooser is _choose_skill and not _can_prompt_for_skill_choice():
            choices = ", ".join(_source_skill_reference(entry) for entry in entries)
            raise SvError(
                f"Select only one source for duplicate skill '{skill_name}': {choices}. "
                "Use repo:skill with 'sv add' when you need a specific source."
            )
        print(f"Multiple selected sources provide '{skill_name}':")
        if _source_choice_table_needed(skill_chooser):
            print(_format_source_skill_choices(entries))
        chosen = skill_chooser(entries)
        if chosen is None:
            print(f"No skill selected for duplicate '{skill_name}'. No skills added.")
            return None
        chosen_by_name[skill_name] = chosen

    resolved: list[SourceSkill] = []
    emitted_duplicate_names: set[str] = set()
    for entry in selected_skills:
        chosen = chosen_by_name.get(entry.name)
        if chosen is None:
            resolved.append(entry)
            continue
        if entry.name in emitted_duplicate_names:
            continue
        resolved.append(chosen)
        emitted_duplicate_names.add(entry.name)
    return resolved


def _selected_duplicate_source_skill_groups(
    selected_skills: Sequence[SourceSkill],
) -> dict[str, list[SourceSkill]]:
    by_name: dict[str, list[SourceSkill]] = {}
    for entry in selected_skills:
        by_name.setdefault(entry.name, []).append(entry)
    return {name: entries for name, entries in by_name.items() if len(entries) > 1}


def _can_prompt_for_skill_choice() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _source_choice_table_needed(skill_chooser: SkillChooser) -> bool:
    return skill_chooser is not _choose_skill or not _can_prompt_for_skill_choice()


def _can_browse_tty(stdin=None, stdout=None) -> bool:
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    if not (stdin.isatty() and stdout.isatty()):
        return False
    try:
        stdin.fileno()
        stdout.fileno()
    except (AttributeError, OSError):
        return False
    return True


def _ambiguous_skill_error(skill_reference: str, matches: Sequence[SourceSkill]) -> str:
    return (
        f"Multiple source skills match '{skill_reference}'. "
        "Use a qualified skill reference from these choices:\n"
        f"{_format_source_skill_scriptable_choices(matches)}"
    )


def _source_skill_headers() -> list[str]:
    return ["Skill", "Source", "Description"]


def _source_skill_max_widths() -> CellWidths:
    return {"Skill": 28, "Source": 32, "Description": 72}


def _source_skill_min_widths() -> CellWidths:
    return {"Skill": 10, "Source": 12, "Description": 24}


def _source_skill_rows(catalog: Sequence[SourceSkill]) -> list[list[str]]:
    rows: list[list[str]] = []
    for skill_name, entries in _source_skills_by_name(catalog).items():
        if len(entries) == 1:
            entry = entries[0]
            rows.append([entry.name, entry.repo_id, entry.description])
            continue
        rows.append(
            [
                skill_name,
                f"{len(entries)} sources",
                "Choose a source below.",
            ]
        )
    return rows


def _duplicate_source_skill_rows(
    catalog: Sequence[SourceSkill], *, duplicate_names: set[str]
) -> list[list[str]]:
    rows: list[list[str]] = []
    visible_names: set[str] = set()
    for entry in catalog:
        if entry.name not in duplicate_names:
            continue
        skill_name = entry.name if entry.name not in visible_names else ""
        visible_names.add(entry.name)
        rows.append(
            [skill_name, entry.repo_id, entry.description, _source_skill_reference(entry)]
        )
    return rows


def _source_skills_by_name(
    catalog: Sequence[SourceSkill],
) -> dict[str, list[SourceSkill]]:
    by_name: dict[str, list[SourceSkill]] = {}
    for entry in catalog:
        by_name.setdefault(entry.name, []).append(entry)
    return by_name


def _duplicate_skill_names(catalog: Sequence[SourceSkill]) -> list[str]:
    counts: dict[str, int] = {}
    for entry in catalog:
        counts[entry.name] = counts.get(entry.name, 0) + 1
    return sorted(name for name, count in counts.items() if count > 1)


def _source_skill_reference(entry: SourceSkill) -> str:
    return entry.qualified_reference


def _format_source_skill_choices(matches: Sequence[SourceSkill]) -> str:
    headers = ["Repo", "Path", "Description", "Add as"]
    rows = _source_skill_choice_rows(matches)
    max_widths: Mapping[str | int, int] = {
        "Repo": 32,
        "Path": 48,
        "Description": 72,
        "Add as": 56,
    }
    min_widths: Mapping[str | int, int] = {
        "Repo": 12,
        "Path": 12,
        "Description": 24,
        "Add as": 16,
    }
    return format_table(
        headers,
        rows,
        max_widths=max_widths,
        min_widths=min_widths,
        max_table_width=_table_width(),
    )


def _source_skill_choice_rows(matches: Sequence[SourceSkill]) -> list[list[str]]:
    return [
        [
            _escape_control_characters(entry.repo_id),
            _escape_control_characters(entry.source_relative_path),
            _escape_control_characters(entry.description),
            _escape_control_characters(_source_skill_reference(entry)),
        ]
        for entry in matches
    ]


def _format_source_skill_scriptable_choices(matches: Sequence[SourceSkill]) -> str:
    lines = ["Repo | Path | Description | Add as"]
    lines.extend(" | ".join(row) for row in _source_skill_choice_rows(matches))
    return "\n".join(lines)


def _source_skill_label(entry: SourceSkill) -> str:
    return entry.display_label


def _source_skill_picker_columns(entry: SourceSkill) -> list[str]:
    return [
        _escape_control_characters(entry.name),
        _source_skill_picker_source_label(entry),
        _escape_control_characters(entry.description),
    ]


def _source_skill_picker_source_label(entry: SourceSkill) -> str:
    if entry.is_default_source_path:
        return _escape_control_characters(entry.repo_id)
    return _escape_control_characters(
        f"{entry.repo_id}:{entry.source_relative_path}"
    )


def _choose_skill(matches: Sequence[SourceSkill]) -> SourceSkill | None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None

    while True:
        selected = select_skills(
            matches,
            item_columns=_source_skill_picker_columns,
            header_columns=["Skill", "Source", "Description"],
            item_ranker=_source_skill_ranker(matches),
            enter_action_label="choose",
        )
        if not selected:
            return None
        if len(selected) == 1:
            return selected[0]
        print("Select exactly one source skill, or q to cancel.")


def _strip_arg_separator(args: Sequence[str]) -> list[str]:
    forwarded = list(args)
    if forwarded and forwarded[0] == "--":
        return forwarded[1:]
    return forwarded

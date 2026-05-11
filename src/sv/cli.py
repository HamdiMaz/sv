from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
import subprocess
import sys
from typing import Any

from sv.agents import PiAdapter
from sv.catalog import (
    SourceSkill,
    build_source_catalog,
    find_catalog_matches,
    find_qualified_catalog_entry,
)
from sv.config import SvPaths, add_repo, load_config, remove_repo
from sv.errors import SvError
from sv.project import (
    AddSkillResult,
    RemoveSkillResult,
    SyncResult,
    add_all_project_skills,
    add_project_skill,
    list_project_skills,
    normalize_skill_name,
    remove_project_skill,
    sync_project_skills,
)
from sv.selector import select_skills
from sv.source import default_runner, ensure_source_repos
from sv.table import format_table

SkillSelector = Callable[..., list[Any]]
SkillChooser = Callable[[Sequence[SourceSkill]], SourceSkill | None]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sv", description="Manage project-local AI agent skills."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "list", help="List skills available in configured source repos."
    )

    add_parser = subparsers.add_parser(
        "add", help="Add source skills to this Pi project."
    )
    add_parser.add_argument("skill", nargs="?")
    add_parser.add_argument(
        "--all", action="store_true", help="Add every skill from source repos."
    )
    add_parser.add_argument(
        "-l",
        "--list",
        dest="interactive",
        action="store_true",
        help="Choose skills from an interactive list.",
    )

    remove_parser = subparsers.add_parser(
        "remove", help="Remove Pi skills from this project."
    )
    remove_parser.add_argument("skill", nargs="?")
    remove_parser.add_argument(
        "-l",
        "--list",
        dest="interactive",
        action="store_true",
        help="Choose project skills from an interactive list.",
    )

    subparsers.add_parser("sync", help="Update local Pi skills from source repos.")

    run_parser = subparsers.add_parser(
        "run", help="Run Pi with only project skills enabled."
    )
    run_parser.add_argument("pi_args", nargs=argparse.REMAINDER)

    repo_parser = subparsers.add_parser("repo", help="Manage global skill source repos.")
    repo_subparsers = repo_parser.add_subparsers(dest="repo_command", required=True)
    repo_add_parser = repo_subparsers.add_parser("add", help="Add a skill source repo.")
    repo_add_parser.add_argument("repo")
    repo_remove_parser = repo_subparsers.add_parser(
        "remove", help="Remove a skill source repo."
    )
    repo_remove_parser.add_argument("repo_id")
    repo_subparsers.add_parser("list", help="List configured skill source repos.")

    return parser


def default_process_runner(command: Sequence[str]) -> int:
    return subprocess.call(list(command))


def handle(
    args: argparse.Namespace,
    cwd: Path,
    home: Path,
    git_runner=default_runner,
    process_runner=default_process_runner,
    skill_selector: SkillSelector = select_skills,
    skill_chooser: SkillChooser | None = None,
) -> int:
    paths = SvPaths.from_home(home)
    adapter = PiAdapter()
    chooser = _choose_skill if skill_chooser is None else skill_chooser

    try:
        if args.command == "repo":
            return _handle_repo(args, paths=paths)

        if args.command == "run":
            return _handle_run(
                args.pi_args, adapter=adapter, process_runner=process_runner
            )

        if args.command == "add":
            if args.interactive:
                if args.all or args.skill is not None:
                    raise SvError("Use -l by itself, or provide a skill name/--all.")
                catalog = _update_sources_and_catalog(paths, git_runner, update=True)
                return _handle_add_interactive(
                    catalog,
                    cwd=cwd,
                    adapter=adapter,
                    skill_selector=skill_selector,
                )

            if args.all and args.skill not in {None, "all"}:
                raise SvError("Use either a skill name or --all, not both.")
            if args.all or args.skill == "all":
                catalog = _update_sources_and_catalog(paths, git_runner, update=True)
                return _handle_add_all(catalog, cwd=cwd, adapter=adapter)
            if args.skill is None:
                raise SvError("Specify a skill name or use --all.")

            _validate_skill_reference(args.skill)
            catalog = _update_sources_and_catalog(paths, git_runner, update=True)
            return _handle_add(
                args.skill,
                catalog,
                cwd=cwd,
                adapter=adapter,
                skill_chooser=chooser,
            )

        if args.command == "remove":
            if args.interactive:
                if args.skill is not None:
                    raise SvError("Use -l by itself, or provide a skill name.")
                return _handle_remove_interactive(
                    cwd=cwd, adapter=adapter, skill_selector=skill_selector
                )

            if args.skill is None:
                raise SvError("Specify a skill name or use -l.")

            skill_name = normalize_skill_name(args.skill)
            return _handle_remove(skill_name, cwd=cwd, adapter=adapter)

        if args.command == "list":
            catalog = _update_sources_and_catalog(paths, git_runner, update=True)
            return _handle_list(catalog)

        if args.command == "sync":
            catalog = _update_sources_and_catalog(paths, git_runner, update=True)
            return _handle_sync(catalog, cwd=cwd, adapter=adapter)

        raise SvError(f"Unknown command: {args.command}")
    except SvError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return handle(args, cwd=Path.cwd(), home=Path.home())


def _update_sources_and_catalog(
    paths: SvPaths, git_runner, *, update: bool = True
) -> list[SourceSkill]:
    config = load_config(paths)
    ensure_source_repos(config.repos, paths, runner=git_runner, update=update)
    return build_source_catalog(config.repos, paths)


def _validate_skill_reference(reference: str) -> None:
    if ":" in reference:
        repo_id, skill = reference.rsplit(":", 1)
        if not repo_id.strip():
            raise SvError(f"Invalid skill reference {reference!r}. Use repo:skill.")
        normalize_skill_name(skill)
        return
    normalize_skill_name(reference)


def _handle_repo(args: argparse.Namespace, paths: SvPaths) -> int:
    if args.repo_command == "add":
        try:
            result = add_repo(paths, args.repo)
        except ValueError as exc:
            raise SvError(str(exc)) from exc
        if result.status == "exists":
            print(f"Repo {result.repo.id} is already configured.")
        else:
            print(f"Added repo {result.repo.id} ({result.repo.url})")
        return 0

    if args.repo_command == "remove":
        try:
            removed = remove_repo(paths, args.repo_id)
        except ValueError as exc:
            raise SvError(str(exc)) from exc
        print(f"Removed repo {removed.id}")
        return 0

    if args.repo_command == "list":
        config = load_config(paths)
        rows = [
            [repo.id, repo.url, str(paths.source_repo_for(repo.id))]
            for repo in config.repos
        ]
        print(format_table(["Repo", "URL", "Cache"], rows))
        return 0

    raise SvError(f"Unknown repo command: {args.repo_command}")


def _handle_run(args: Sequence[str], adapter: PiAdapter, process_runner) -> int:
    """Run Pi and turn a missing executable into a user-facing error."""
    command = adapter.run_command(_strip_arg_separator(args))
    try:
        return process_runner(command)
    except FileNotFoundError as exc:
        raise SvError(
            f"Unable to run '{command[0]}'. Make sure it is installed and on PATH."
        ) from exc


def _handle_list(catalog: Sequence[SourceSkill]) -> int:
    if not catalog:
        print("No valid skills found in configured source repos.")
        return 0

    print(format_table(["Skill", "Repo", "Description"], _source_skill_rows(catalog)))
    return 0


def _handle_add(
    skill_reference: str,
    catalog: Sequence[SourceSkill],
    cwd: Path,
    adapter: PiAdapter,
    skill_chooser: SkillChooser,
) -> int:
    qualified = find_qualified_catalog_entry(catalog, skill_reference)
    if qualified is not None:
        result = add_project_skill(qualified, adapter.project_skill_dir(cwd))
        _print_add_result(result)
        return 0

    if ":" in skill_reference:
        raise SvError(
            f"Skill '{skill_reference}' was not found in configured source repos."
        )

    matches = find_catalog_matches(catalog, skill_reference)
    if not matches:
        raise SvError(
            f"Skill '{skill_reference}' was not found in configured source repos."
        )
    if len(matches) == 1:
        result = add_project_skill(matches[0], adapter.project_skill_dir(cwd))
        _print_add_result(result)
        return 0

    print(f"Multiple source skills match '{skill_reference}':")
    rows = [
        [str(index), entry.name, entry.repo_id, entry.description]
        for index, entry in enumerate(matches, start=1)
    ]
    print(format_table(["#", "Skill", "Repo", "Description"], rows))
    chosen = skill_chooser(matches)
    if chosen is None:
        print(
            "No skill selected. "
            f"Rerun with {matches[0].repo_id}:{matches[0].name} to choose explicitly."
        )
        return 0

    result = add_project_skill(chosen, adapter.project_skill_dir(cwd))
    _print_add_result(result)
    return 0


def _handle_add_all(
    catalog: Sequence[SourceSkill], cwd: Path, adapter: PiAdapter
) -> int:
    result = add_all_project_skills(catalog, adapter.project_skill_dir(cwd))
    if not result.results:
        print("No valid skills found in configured source repos.")
        return 0

    for skill_result in result.results:
        _print_add_result(skill_result)
    return 0


def _handle_add_interactive(
    catalog: Sequence[SourceSkill],
    cwd: Path,
    adapter: PiAdapter,
    skill_selector: SkillSelector,
) -> int:
    if not catalog:
        print("No valid skills found in configured source repos.")
        return 0

    selected_skills = skill_selector(catalog, item_label=_source_skill_label)
    if not selected_skills:
        print("No skills selected.")
        return 0

    for entry in selected_skills:
        result = add_project_skill(entry, adapter.project_skill_dir(cwd))
        _print_add_result(result)
    return 0


def _print_add_result(result: AddSkillResult) -> None:
    source = f" from {result.repo_id}" if result.repo_id else ""
    if result.status == "exists":
        print(f"Pi skill '{result.skill}' already exists at {result.target}")
        return

    print(f"Added Pi skill '{result.skill}'{source} to {result.target}")


def _handle_remove(skill: str, cwd: Path, adapter: PiAdapter) -> int:
    result = remove_project_skill(skill, adapter.project_skill_dir(cwd))
    _print_remove_result(result)
    return 0


def _handle_remove_interactive(
    cwd: Path, adapter: PiAdapter, skill_selector: SkillSelector
) -> int:
    project_skills_dir = adapter.project_skill_dir(cwd)
    skills = list_project_skills(project_skills_dir)
    if not skills:
        print("No Pi skills found to remove.")
        return 0

    selected_skills = skill_selector(skills)
    if not selected_skills:
        print("No skills selected.")
        return 0

    for skill in selected_skills:
        result = remove_project_skill(skill, project_skills_dir)
        _print_remove_result(result)
    return 0


def _print_remove_result(result: RemoveSkillResult) -> None:
    print(f"Removed Pi skill '{result.skill}' from {result.target}")


def _handle_sync(catalog: Sequence[SourceSkill], cwd: Path, adapter: PiAdapter) -> int:
    result = sync_project_skills(catalog, adapter.project_skill_dir(cwd))
    _print_sync_result(result)
    return 0


def _print_sync_result(result: SyncResult) -> None:
    if result.no_skills_dir:
        print("No Pi skills found to sync.")
        return

    if result.updated:
        for skill in result.updated:
            print(f"Synced Pi skill '{skill}'.")
    else:
        print("No matching Pi skills found to sync.")

    for skill in result.backfilled:
        print(f"Recorded origin for legacy Pi skill '{skill}'.")

    for skip in result.skipped:
        if skip.reason == "ambiguous":
            repos = ", ".join(skip.repo_ids)
            print(
                f"Skipped local Pi skill '{skip.skill}': multiple source repos match ({repos})."
            )
        elif skip.reason == "source-missing":
            repos = ", ".join(skip.repo_ids)
            print(
                f"Skipped local Pi skill '{skip.skill}': recorded source is missing ({repos})."
            )
        else:
            print(f"Skipped local Pi skill '{skip.skill}'.")


def _source_skill_rows(catalog: Sequence[SourceSkill]) -> list[list[str]]:
    return [[entry.name, entry.repo_id, entry.description] for entry in catalog]


def _source_skill_label(entry: SourceSkill) -> str:
    return f"{entry.name}  {entry.repo_id}  {entry.description}"


def _choose_skill(matches: Sequence[SourceSkill]) -> SourceSkill | None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None

    while True:
        choice = input("Choose a skill number, or q to cancel: ").strip()
        if choice.lower() == "q":
            return None
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(matches):
                return matches[index]
        print(f"Enter a number from 1 to {len(matches)}, or q to cancel.")


def _strip_arg_separator(args: Sequence[str]) -> list[str]:
    forwarded = list(args)
    if forwarded and forwarded[0] == "--":
        return forwarded[1:]
    return forwarded

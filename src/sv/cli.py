from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
import subprocess
import sys

from sv.agents import PiAdapter
from sv.config import SvPaths, add_repo, load_config, remove_repo
from sv.errors import SvError
from sv.project import (
    AddSkillResult,
    RemoveSkillResult,
    add_all_project_skills,
    add_project_skill,
    list_project_skills,
    normalize_skill_name,
    remove_project_skill,
    sync_project_skills,
)
from sv.selector import select_skills
from sv.source import default_runner, ensure_source_repo, list_source_skills
from sv.table import format_table

SkillSelector = Callable[[Sequence[str]], list[str]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sv", description="Manage project-local AI agent skills."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "list", help="List skills available in the configured source repo."
    )

    add_parser = subparsers.add_parser(
        "add", help="Add source skills to this Pi project."
    )
    add_parser.add_argument("skill", nargs="?")
    add_parser.add_argument(
        "--all", action="store_true", help="Add every skill from the source repo."
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

    subparsers.add_parser(
        "sync", help="Update local Pi skills that exist in the source repo."
    )

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
) -> int:
    paths = SvPaths.from_home(home)
    adapter = PiAdapter()

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
                _ensure_configured_source(paths, git_runner, update=False)
                return _handle_add_interactive(
                    cwd=cwd,
                    paths=paths,
                    adapter=adapter,
                    skill_selector=skill_selector,
                )

            if args.all and args.skill not in {None, "all"}:
                raise SvError("Use either a skill name or --all, not both.")
            if args.all or args.skill == "all":
                _ensure_configured_source(paths, git_runner)
                return _handle_add_all(cwd=cwd, paths=paths, adapter=adapter)
            if args.skill is None:
                raise SvError("Specify a skill name or use --all.")

            skill_name = normalize_skill_name(args.skill)
            _ensure_configured_source(paths, git_runner)
            return _handle_add(skill_name, cwd=cwd, paths=paths, adapter=adapter)

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

        _ensure_configured_source(paths, git_runner)

        if args.command == "list":
            return _handle_list(paths)

        if args.command == "sync":
            return _handle_sync(cwd=cwd, paths=paths, adapter=adapter)

        raise SvError(f"Unknown command: {args.command}")
    except SvError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return handle(args, cwd=Path.cwd(), home=Path.home())


def _ensure_configured_source(
    paths: SvPaths, git_runner, *, update: bool = True
) -> None:
    """Clone or update the configured skill source before source-backed commands."""
    config = load_config(paths)
    ensure_source_repo(config.repo, paths.source_repo, runner=git_runner, update=update)


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


def _handle_list(paths: SvPaths) -> int:
    skills = list_source_skills(paths.source_repo)
    if not skills:
        print("No skills found in source repo.")
        return 0

    for index, skill in enumerate(skills, start=1):
        print(f"{index}- {skill}")
    return 0


def _handle_add(skill: str, cwd: Path, paths: SvPaths, adapter: PiAdapter) -> int:
    result = add_project_skill(skill, paths.source_repo, adapter.project_skill_dir(cwd))
    _print_add_result(result)
    return 0


def _handle_add_all(cwd: Path, paths: SvPaths, adapter: PiAdapter) -> int:
    result = add_all_project_skills(paths.source_repo, adapter.project_skill_dir(cwd))
    if not result.results:
        print("No skills found in source repo.")
        return 0

    for skill_result in result.results:
        _print_add_result(skill_result)
    return 0


def _handle_add_interactive(
    cwd: Path, paths: SvPaths, adapter: PiAdapter, skill_selector: SkillSelector
) -> int:
    skills = list_source_skills(paths.source_repo)
    if not skills:
        print("No skills found in source repo.")
        return 0

    selected_skills = skill_selector(skills)
    if not selected_skills:
        print("No skills selected.")
        return 0

    for skill in selected_skills:
        result = add_project_skill(
            skill, paths.source_repo, adapter.project_skill_dir(cwd)
        )
        _print_add_result(result)
    return 0


def _print_add_result(result: AddSkillResult) -> None:
    if result.status == "exists":
        print(f"Pi skill '{result.skill}' already exists at {result.target}")
        return

    print(f"Added Pi skill '{result.skill}' to {result.target}")


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


def _handle_sync(cwd: Path, paths: SvPaths, adapter: PiAdapter) -> int:
    result = sync_project_skills(paths.source_repo, adapter.project_skill_dir(cwd))
    if result.no_skills_dir:
        print("No Pi skills found to sync.")
        return 0

    if result.updated:
        for skill in result.updated:
            print(f"Synced Pi skill '{skill}'.")
    else:
        print("No matching Pi skills found to sync.")

    for skill in result.skipped:
        print(f"Skipped local Pi skill '{skill}'.")

    return 0


def _strip_arg_separator(args: Sequence[str]) -> list[str]:
    forwarded = list(args)
    if forwarded and forwarded[0] == "--":
        return forwarded[1:]
    return forwarded

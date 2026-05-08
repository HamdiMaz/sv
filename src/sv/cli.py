from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import subprocess
import sys

from sv.agents import PiAdapter
from sv.config import SvPaths, load_config, save_repo
from sv.errors import SvError
from sv.project import add_project_skill, normalize_skill_name, sync_project_skills
from sv.source import default_runner, ensure_source_repo, list_source_skills


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sv", description="Manage project-local AI agent skills."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "list", help="List skills available in the configured source repo."
    )

    add_parser = subparsers.add_parser(
        "add", help="Add a source skill to this Pi project."
    )
    add_parser.add_argument("skill")

    subparsers.add_parser(
        "sync", help="Update local Pi skills that exist in the source repo."
    )

    run_parser = subparsers.add_parser(
        "run", help="Run Pi with only project skills enabled."
    )
    run_parser.add_argument("pi_args", nargs=argparse.REMAINDER)

    config_parser = subparsers.add_parser(
        "config", help="Show or change sv configuration."
    )
    config_subparsers = config_parser.add_subparsers(
        dest="config_command", required=True
    )
    repo_parser = config_subparsers.add_parser(
        "repo", help="Set the default skill source repo."
    )
    repo_parser.add_argument("repo")
    config_subparsers.add_parser("show", help="Show effective sv configuration.")

    return parser


def default_process_runner(command: Sequence[str]) -> int:
    return subprocess.call(list(command))


def handle(
    args: argparse.Namespace,
    cwd: Path,
    home: Path,
    git_runner=default_runner,
    process_runner=default_process_runner,
) -> int:
    paths = SvPaths.from_home(home)
    adapter = PiAdapter()

    try:
        if args.command == "config":
            return _handle_config(args, cwd=cwd, paths=paths, adapter=adapter)

        if args.command == "run":
            return _handle_run(
                args.pi_args, adapter=adapter, process_runner=process_runner
            )

        if args.command == "add":
            skill_name = normalize_skill_name(args.skill)
            _ensure_configured_source(paths, git_runner)
            return _handle_add(skill_name, cwd=cwd, paths=paths, adapter=adapter)

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


def _ensure_configured_source(paths: SvPaths, git_runner) -> None:
    """Clone or update the configured skill source before source-backed commands."""
    config = load_config(paths)
    ensure_source_repo(config.repo, paths.source_repo, runner=git_runner)


def _handle_config(
    args: argparse.Namespace, cwd: Path, paths: SvPaths, adapter: PiAdapter
) -> int:
    if args.config_command == "repo":
        try:
            repo = save_repo(paths, args.repo)
        except ValueError as exc:
            raise SvError(str(exc)) from exc
        print(f"Set source repo to {repo}")
        return 0

    if args.config_command == "show":
        config = load_config(paths)
        print(f"repo = {config.repo}")
        print(f"source = {paths.source_repo}")
        print(f"pi_skills = {adapter.project_skill_dir(cwd)}")
        return 0

    raise SvError(f"Unknown config command: {args.config_command}")


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

    for skill in skills:
        print(skill)
    return 0


def _handle_add(skill: str, cwd: Path, paths: SvPaths, adapter: PiAdapter) -> int:
    result = add_project_skill(skill, paths.source_repo, adapter.project_skill_dir(cwd))
    if result.status == "exists":
        print(f"Pi skill '{result.skill}' already exists at {result.target}")
        return 0

    print(f"Added Pi skill '{result.skill}' to {result.target}")
    return 0


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

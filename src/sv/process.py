from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
import os
import subprocess

from sv.errors import SvError

Runner = Callable[[Sequence[str], Path | None], subprocess.CompletedProcess[str]]

DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 60
_COMMAND_TIMEOUT_EXIT_CODE = 124
_ALLOWED_GIT_PROTOCOLS = "file:https:ssh"


def default_process_runner(command: Sequence[str]) -> int:
    return subprocess.call(list(command))


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


def _missing_git_guidance() -> str:
    return (
        "Git is required but was not found on PATH. Install Git from "
        "https://git-scm.com/downloads or add git to PATH, then retry."
    )

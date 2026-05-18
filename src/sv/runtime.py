from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import shutil
import sys
from typing import TextIO


def utc_now() -> datetime:
    return datetime.now(UTC)


def default_terminal_width() -> int:
    return max(shutil.get_terminal_size(fallback=(120, 24)).columns, 1)


@dataclass(frozen=True)
class Runtime:
    cwd: Path
    home: Path
    env: Mapping[str, str]
    stdin: TextIO
    stdout: TextIO
    stderr: TextIO
    now: Callable[[], datetime] = utc_now
    terminal_width: Callable[[], int] = default_terminal_width

    @classmethod
    def from_process(cls) -> "Runtime":
        return cls(
            cwd=Path.cwd(),
            home=Path.home(),
            env=os.environ,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

    def width(self) -> int:
        return max(self.terminal_width(), 1)

    def can_prompt(self) -> bool:
        return self.stdin.isatty() and self.stdout.isatty()

    def prompt(self, prompt: str) -> str:
        self.stdout.write(prompt)
        self.stdout.flush()
        return self.stdin.readline().rstrip("\n")

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PiAdapter:
    name: str = "pi"

    def project_skill_dir(self, project_root: Path) -> Path:
        return project_root / ".pi" / "skills"

    def run_command(
        self, extra_args: Sequence[str] = (), *, skills_path: str = ".pi/skills"
    ) -> list[str]:
        return ["pi", "--no-skills", "--skill", skills_path, *list(extra_args)]

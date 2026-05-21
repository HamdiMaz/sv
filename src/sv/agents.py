from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sv.errors import SvError
from sv.terminal import escape_terminal_controls


@dataclass(frozen=True)
class ProjectAgent:
    name: str
    display_name: str
    folder: str

    @property
    def target_path_prefix(self) -> str:
        return f"{self.folder}/skills"

    @property
    def skill_label(self) -> str:
        return f"{self.display_name} skill"

    @property
    def skills_label(self) -> str:
        return f"{self.display_name} skills"

    @property
    def skills_dir_label(self) -> str:
        return f"{self.display_name} skills directory"

    @property
    def path_label(self) -> str:
        return f"{self.display_name} skills path"

    def project_skill_dir(self, project_root: Path) -> Path:
        return project_root / self.folder / "skills"


_PROJECT_AGENTS: tuple[ProjectAgent, ...] = (
    ProjectAgent(name="pi", display_name="Pi", folder=".pi"),
    ProjectAgent(name="claude", display_name="Claude", folder=".claude"),
    ProjectAgent(name="agents", display_name="Agents", folder=".agents"),
)
_PROJECT_AGENTS_BY_NAME = {agent.name: agent for agent in _PROJECT_AGENTS}
_PROJECT_AGENT_DISPLAY_TO_NAME = {
    agent.display_name.casefold(): agent.name for agent in _PROJECT_AGENTS
}


def supported_project_agents() -> tuple[ProjectAgent, ...]:
    return _PROJECT_AGENTS


def supported_project_agent_names() -> tuple[str, ...]:
    return tuple(agent.name for agent in _PROJECT_AGENTS)


def supported_project_agents_text() -> str:
    return ", ".join(supported_project_agent_names())


def normalize_project_agent(value: str) -> str:
    normalized = value.strip().casefold()
    agent = _PROJECT_AGENTS_BY_NAME.get(normalized)
    if agent is not None:
        return agent.name
    display_name = _PROJECT_AGENT_DISPLAY_TO_NAME.get(normalized)
    if display_name is not None:
        return display_name
    safe_value = escape_terminal_controls(value)
    raise SvError(
        f"Unsupported agent '{safe_value}'. Supported agents: {supported_project_agents_text()}."
    )


def project_agent_for(value: str) -> ProjectAgent:
    return _PROJECT_AGENTS_BY_NAME[normalize_project_agent(value)]


@dataclass(frozen=True)
class PiAdapter:
    name: str = "pi"

    def project_skill_dir(self, project_root: Path) -> Path:
        return project_agent_for("pi").project_skill_dir(project_root)

    def run_command(
        self, extra_args: Sequence[str] = (), *, skills_path: str = ".pi/skills"
    ) -> list[str]:
        return ["pi", "--no-skills", "--skill", skills_path, *list(extra_args)]

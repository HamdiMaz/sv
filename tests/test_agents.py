from pathlib import Path

import pytest

from sv.agents import (
    PiAdapter,
    ProjectAgent,
    normalize_project_agent,
    project_agent_for,
    supported_project_agents,
    supported_project_agent_names,
    supported_project_agents_text,
)
from sv.errors import SvError


def test_supported_project_agents_define_skill_dirs(tmp_path: Path):
    assert supported_project_agent_names() == ("pi", "claude", "agents")
    assert project_agent_for("pi").project_skill_dir(tmp_path) == tmp_path / ".pi" / "skills"
    assert project_agent_for("claude").project_skill_dir(tmp_path) == tmp_path / ".claude" / "skills"
    assert project_agent_for("agents").project_skill_dir(tmp_path) == tmp_path / ".agents" / "skills"


def test_supported_project_agents_define_labels():
    agents = supported_project_agents()

    assert agents[0] == ProjectAgent(
        name="pi",
        display_name="Pi",
        folder=".pi",
    )
    assert project_agent_for("claude").skill_label == "Claude skill"
    assert project_agent_for("agents").skills_label == "Agents skills"
    assert project_agent_for("agents").target_path_prefix == ".agents/skills"


def test_normalize_project_agent_accepts_display_names_and_keys():
    assert normalize_project_agent("pi") == "pi"
    assert normalize_project_agent("Pi") == "pi"
    assert normalize_project_agent("claude") == "claude"
    assert normalize_project_agent("Claude") == "claude"
    assert normalize_project_agent(" Claude ") == "claude"
    assert normalize_project_agent("agents") == "agents"
    assert normalize_project_agent("Agents") == "agents"


def test_supported_project_agents_text_lists_agent_names():
    assert supported_project_agents_text() == "pi, claude, agents"


@pytest.mark.parametrize("value", ["", "bad", "pi\x1b[2J"])
def test_normalize_project_agent_rejects_unsupported_values(value: str):
    with pytest.raises(SvError, match="Supported agents"):
        normalize_project_agent(value)


def test_normalize_project_agent_escapes_unsupported_values_in_errors():
    with pytest.raises(SvError) as exc_info:
        normalize_project_agent("pi\x1b[2J")

    message = str(exc_info.value)
    assert "\x1b" not in message
    assert "pi\\x1b[2J" in message


def test_pi_adapter_keeps_existing_run_behavior(tmp_path: Path):
    adapter = PiAdapter()

    assert adapter.name == "pi"
    assert adapter.project_skill_dir(tmp_path) == tmp_path / ".pi" / "skills"
    assert adapter.run_command(["--model", "fast"]) == [
        "pi",
        "--no-skills",
        "--skill",
        ".pi/skills",
        "--model",
        "fast",
    ]

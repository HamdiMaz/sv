from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from sv.runtime import Runtime, default_terminal_width


class TtyStringIO(StringIO):
    def isatty(self):
        return True


def test_runtime_captures_paths_env_streams_and_clock(tmp_path: Path):
    now = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    stdin = StringIO("answer\n")
    stdout = StringIO()
    stderr = StringIO()

    runtime = Runtime(
        cwd=tmp_path / "project",
        home=tmp_path / "home",
        env={"SV_JOBS": "1"},
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        now=lambda: now,
        terminal_width=lambda: 42,
    )

    assert runtime.cwd == tmp_path / "project"
    assert runtime.home == tmp_path / "home"
    assert runtime.env["SV_JOBS"] == "1"
    assert runtime.now() == now
    assert runtime.width() == 42
    assert runtime.can_prompt() is False


def test_runtime_prompt_reads_from_configured_streams():
    runtime = Runtime(
        cwd=Path("/work"),
        home=Path("/home/user"),
        env={},
        stdin=StringIO("repo/name\n"),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert runtime.prompt("Repo: ") == "repo/name"
    assert runtime.stdout.getvalue() == "Repo: "


def test_runtime_can_prompt_requires_tty_stdin_and_stdout():
    runtime = Runtime(
        cwd=Path("/work"),
        home=Path("/home/user"),
        env={},
        stdin=TtyStringIO(),
        stdout=TtyStringIO(),
        stderr=StringIO(),
    )

    assert runtime.can_prompt() is True


def test_default_terminal_width_clamps_to_at_least_one(monkeypatch):
    monkeypatch.setattr("sv.runtime.shutil.get_terminal_size", lambda fallback: type("Size", (), {"columns": 0})())

    assert default_terminal_width() == 1

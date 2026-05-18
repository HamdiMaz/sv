from __future__ import annotations

from pathlib import Path
import subprocess

from sv.process import default_process_runner, default_runner


def test_default_process_runner_delegates_to_subprocess_call(monkeypatch):
    calls = []

    def fake_call(command):
        calls.append(command)
        return 7

    monkeypatch.setattr("sv.process.subprocess.call", fake_call)

    assert default_process_runner(["pi", "--help"]) == 7
    assert calls == [["pi", "--help"]]


def test_default_runner_sets_noninteractive_environment(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("sv.process.subprocess.run", fake_run)

    result = default_runner(["git", "status"], tmp_path)

    assert result.stdout == "ok"
    assert captured["args"] == ["git", "status"]
    assert captured["cwd"] == tmp_path
    assert captured["text"] is True
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    assert captured["capture_output"] is True
    assert captured["timeout"] == 60
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["env"]["GH_PROMPT_DISABLED"] == "1"
    assert captured["env"]["GIT_ALLOW_PROTOCOL"] == "file:https:ssh"

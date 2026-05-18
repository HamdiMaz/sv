from __future__ import annotations

import threading

import pytest

from sv.errors import SvError
from sv.parallel import configured_jobs, map_ordered


def test_configured_jobs_defaults_to_bounded_io_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SV_JOBS", raising=False)
    monkeypatch.setattr("sv.parallel.os.cpu_count", lambda: 128)

    assert configured_jobs() == 8


def test_configured_jobs_accepts_explicit_sequential_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SV_JOBS", "1")

    assert configured_jobs() == 1


def test_configured_jobs_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for value in ("", "0", "65", "two", "1.5", "-1"):
        monkeypatch.setenv("SV_JOBS", value)
        with pytest.raises(SvError, match="SV_JOBS must be an integer between 1 and 64"):
            configured_jobs()


def test_map_ordered_validates_sv_jobs_even_with_no_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SV_JOBS", "many")

    with pytest.raises(SvError, match="SV_JOBS must be an integer between 1 and 64"):
        map_ordered([], lambda value: value)


def test_map_ordered_preserves_input_order_when_workers_complete_out_of_order() -> None:
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()

    def worker(value: int) -> str:
        if value == 0:
            first_started.set()
            assert second_started.wait(2), "second worker did not start while first was blocked"
            assert release_first.wait(2), "first worker was not released"
            return "first"
        second_started.set()
        assert first_started.wait(2), "first worker did not start"
        release_first.set()
        return "second"

    assert map_ordered([0, 1], worker, jobs=2) == ["first", "second"]


def test_map_ordered_runs_inline_when_jobs_is_one() -> None:
    thread_names: list[str] = []

    def worker(value: int) -> int:
        thread_names.append(threading.current_thread().name)
        return value * 2

    assert map_ordered([1, 2, 3], worker, jobs=1) == [2, 4, 6]
    assert thread_names == [threading.current_thread().name] * 3


def test_map_ordered_preserves_none_results_in_threaded_mode() -> None:
    assert map_ordered(["a", "b"], lambda _value: None, jobs=2) == [None, None]


def test_map_ordered_reraises_first_failure_by_input_order() -> None:
    def worker(value: str) -> str:
        if value == "first":
            raise SvError("first failure")
        if value == "second":
            raise SvError("second failure")
        return value

    with pytest.raises(SvError, match="first failure"):
        map_ordered(["ok", "first", "second"], worker, jobs=3)


def test_map_ordered_reraises_worker_base_exception() -> None:
    def worker(value: str) -> str:
        if value == "stop":
            raise SystemExit("stop now")
        return value

    with pytest.raises(SystemExit, match="stop now"):
        map_ordered(["ok", "stop"], worker, jobs=2)

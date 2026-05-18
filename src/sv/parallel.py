from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
import os
from typing import TypeVar, cast

from sv.errors import SvError

T = TypeVar("T")
R = TypeVar("R")

_DEFAULT_MAX_JOBS = 8
_MAX_JOBS = 64
_MISSING = object()


def configured_jobs(env: Mapping[str, str] | None = None) -> int:
    """Return the configured sv worker count.

    SV_JOBS is intentionally process-wide because these are internal worker pools,
    not a user-facing command option. Tests may pass an explicit environment map.
    """

    values = os.environ if env is None else env
    raw_value = values.get("SV_JOBS")
    if raw_value is None:
        return min(_DEFAULT_MAX_JOBS, (os.cpu_count() or 1) + 4)
    try:
        jobs = int(raw_value, 10)
    except ValueError as exc:
        raise SvError("SV_JOBS must be an integer between 1 and 64.") from exc
    if jobs < 1 or jobs > _MAX_JOBS:
        raise SvError("SV_JOBS must be an integer between 1 and 64.")
    return jobs


def map_ordered(
    items: Iterable[T],
    worker: Callable[[T], R],
    *,
    jobs: int | None = None,
) -> list[R]:
    """Run independent work and return results in input order.

    Exceptions are collected and the first exception by input index is re-raised.
    This keeps command failures deterministic even when workers finish out of order.
    """

    item_list = list(items)
    worker_count = configured_jobs() if jobs is None else jobs
    if worker_count < 1 or worker_count > _MAX_JOBS:
        raise SvError("SV_JOBS must be an integer between 1 and 64.")
    if not item_list:
        return []
    if worker_count == 1 or len(item_list) == 1:
        return [worker(item) for item in item_list]

    results: list[R | object] = [_MISSING] * len(item_list)
    failures: list[Exception | None] = [None] * len(item_list)
    max_workers = min(worker_count, len(item_list))
    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="sv")
    futures: dict[Future[R], int] = {}
    try:
        futures = {
            executor.submit(worker, item): index
            for index, item in enumerate(item_list)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                failures[index] = exc
    except BaseException:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)

    for failure in failures:
        if failure is not None:
            raise failure

    ordered: list[R] = []
    for result in results:
        if result is _MISSING:
            raise AssertionError("parallel worker result missing after successful join")
        ordered.append(cast(R, result))
    return ordered

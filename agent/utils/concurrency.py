"""Controlled thread-pool helpers for I/O-bound pipeline stages."""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def env_int(name: str, default: int, minimum: int = 1, maximum: int = 20) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def map_ordered(
    items: list[T],
    worker: Callable[[T], R],
    *,
    concurrency: int,
    label: str = "task",
) -> list[R]:
    """
    Run worker over items with a thread pool, preserving input order in results.
    """
    if not items:
        return []
    if concurrency <= 1 or len(items) == 1:
        return [worker(item) for item in items]

    started = time.perf_counter()
    results: list[R | None] = [None] * len(items)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_map = {pool.submit(worker, item): index for index, item in enumerate(items)}
        for future in as_completed(future_map):
            index = future_map[future]
            results[index] = future.result()

    elapsed = time.perf_counter() - started
    from agent.utils.logger import log

    log.info(
        "%s: processed %d items with concurrency=%d in %.1fs",
        label,
        len(items),
        concurrency,
        elapsed,
    )
    return results  # type: ignore[return-value]

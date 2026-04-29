"""Heavy-lifting helpers + timing/measure utilities for the stress
tests.

Kept here (not in conftest.py) because pytest's conftest is not a
regularly-importable module — the test modules import helpers
explicitly via ``from enjambre_de_guerra._harness import ...``.
"""

from __future__ import annotations

import gc
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from hoc.core import HoneycombConfig, HoneycombGrid
from hoc.nectar import NectarFlow
from hoc.security import RateLimiter
from hoc.swarm import SwarmConfig, SwarmScheduler


def build_loaded_scheduler(
    *,
    radius: int = 2,
    max_queue_size: int = 100_000,
    queue_full_policy: str = "raise",
    n_tasks: int = 0,
    task_factory: Callable[[int], dict] | None = None,
) -> tuple[HoneycombGrid, NectarFlow, SwarmScheduler]:
    """Standardised scheduler factory for stress tests.

    Returns a (grid, nectar, scheduler) triple with the rate limiter
    relaxed so the burst tests aren't bottlenecked on token-bucket
    math. Optionally pre-populates ``n_tasks`` tasks.
    """
    grid = HoneycombGrid(HoneycombConfig(radius=radius))
    nectar = NectarFlow(grid)
    cfg = SwarmConfig(
        max_queue_size=max_queue_size,
        queue_full_policy=queue_full_policy,  # type: ignore[arg-type]
        submit_rate_per_second=10_000_000.0,
        submit_rate_burst=10_000_000,
        execute_rate_per_second=10_000_000.0,
        execute_rate_burst=10_000_000,
    )
    sched = SwarmScheduler(grid, nectar, cfg)
    sched._submit_limiter = RateLimiter(
        per_second=cfg.submit_rate_per_second, burst=cfg.submit_rate_burst
    )
    sched._execute_limiter = RateLimiter(
        per_second=cfg.execute_rate_per_second, burst=cfg.execute_rate_burst
    )

    if n_tasks > 0:
        for i in range(n_tasks):
            payload = task_factory(i) if task_factory else {}
            sched.submit_task("compute", payload)

    return grid, nectar, sched


# ─────────────────────────────────────────────────────────────────────
# Timing + measurement utilities
# ─────────────────────────────────────────────────────────────────────


@contextmanager
def stopwatch(name: str = "block") -> Iterator[dict]:
    """Time a block; result lands in the yielded dict as ``elapsed_s``."""
    out: dict = {"name": name}
    start = time.perf_counter()
    try:
        yield out
    finally:
        out["elapsed_s"] = time.perf_counter() - start


def gc_now() -> int:
    """Force a full GC pass; return number of objects collected."""
    return gc.collect()


def rss_mb() -> float:
    """Current process RSS in MiB. POSIX uses ``resource.getrusage``;
    Windows falls back to ``psutil`` if available, else 0.0 (the
    caller skips its assert in that case).

    Stress tests use this as a *trend* signal, not an absolute bound.
    """
    if sys.platform == "win32":
        try:
            import psutil  # type: ignore[import-not-found]

            return psutil.Process().memory_info().rss / (1024 * 1024)
        except ImportError:
            return 0.0
    import resource

    usage = resource.getrusage(resource.RUSAGE_SELF)
    if sys.platform == "darwin":
        return usage.ru_maxrss / (1024 * 1024)
    return usage.ru_maxrss / 1024


def drain_scheduler(scheduler, max_ticks: int = 1000, until_pending_zero: bool = True) -> dict:
    """Run ``scheduler.run_tick_sync`` until the pending queue empties
    or ``max_ticks`` ticks elapse. Returns a summary dict."""
    ticks_run = 0
    for _ in range(max_ticks):
        if until_pending_zero and scheduler.get_pending_count() == 0:
            break
        scheduler.run_tick_sync()
        ticks_run += 1
    stats = scheduler.get_stats()
    return {
        "ticks_run": ticks_run,
        "pending_after": scheduler.get_pending_count(),
        "completed": stats["tasks_completed"],
        "failed": stats["tasks_failed"],
        "dropped": stats.get("tasks_dropped", 0),
    }


def pi_chunk_inline(n: int, seed: int = 0) -> int:
    """Tiny pure-Python Pi estimator chunk — generic "do compute"
    payload for stress tests that don't care about the result."""
    import random

    rng = random.Random(seed)
    inside = 0
    for _ in range(n):
        x, y = rng.random(), rng.random()
        if x * x + y * y <= 1.0:
            inside += 1
    return inside

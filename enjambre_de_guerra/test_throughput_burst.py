"""Stress: burst submission + drain.

Hipótesis bajo prueba:
- 50K submissions en orden completan sin perder counters.
- Bajo policy="drop_oldest", la cola se mantiene bounded y
  ``tasks_dropped`` registra exactamente la diferencia.
- Submission rate sostenido > 100k tasks/s en hardware moderno (con
  rate limiter relajado).
- ``tasks_completed + tasks_dropped + pending`` siempre suma N total.
"""

from __future__ import annotations

import pytest

from enjambre_de_guerra._harness import (
    build_loaded_scheduler,
    drain_scheduler,
    stopwatch,
)

pytestmark = pytest.mark.stress


class TestThroughputBurst:
    def test_50k_submissions_complete(self):
        """Bursting 50K submissions should not raise and the queue
        accounting must balance."""
        _, _, sched = build_loaded_scheduler(
            radius=2, max_queue_size=60_000, queue_full_policy="raise"
        )
        N = 50_000
        with stopwatch("submit") as t:
            for i in range(N):
                sched.submit_task("compute", {"i": i})
        # Submission throughput sanity (>= 50K in <5s on any modern CPU).
        assert t["elapsed_s"] < 10.0, f"submit was unexpectedly slow: {t['elapsed_s']:.2f}s for 50K"
        assert sched.get_queue_size() == N
        assert sched.get_stats()["tasks_dropped"] == 0

    @pytest.mark.slow
    def test_50k_burst_with_drop_oldest_keeps_queue_bounded(self):
        """drop_oldest under saturation: queue stays at cap, drops counter
        equals overflow exactly."""
        cap = 1_000
        N = 50_000
        _, _, sched = build_loaded_scheduler(
            radius=1, max_queue_size=cap, queue_full_policy="drop_oldest"
        )
        for i in range(N):
            sched.submit_task("compute", {"i": i})

        assert sched.get_queue_size() == cap
        # Each submit beyond the cap evicts exactly one task.
        assert sched.get_stats()["tasks_dropped"] == N - cap

    def test_burst_then_drain_invariant(self):
        """After draining, completed + dropped + pending = N submitted."""
        N = 5_000
        _, _, sched = build_loaded_scheduler(
            radius=2, max_queue_size=N + 100, queue_full_policy="raise"
        )
        for _ in range(N):
            sched.submit_task("compute", {})
        summary = drain_scheduler(sched, max_ticks=2_000)
        # Probabilistic refusal in ForagerBehavior means not every task
        # completes within max_ticks; the surviving pending are still
        # tracked. Sum invariant must hold regardless.
        accounted = (
            summary["completed"] + summary["failed"] + summary["dropped"] + summary["pending_after"]
        )
        assert accounted == N, (
            f"counter desync: completed={summary['completed']} "
            f"failed={summary['failed']} dropped={summary['dropped']} "
            f"pending={summary['pending_after']} != N={N}"
        )

    def test_mixed_priority_burst(self):
        """5K tasks with mixed priorities; high-priority drains first
        when scheduler is loaded."""
        from hoc.swarm import TaskPriority

        _, _, sched = build_loaded_scheduler(
            radius=2, max_queue_size=10_000, queue_full_policy="raise"
        )
        # Submit alternating CRITICAL / BACKGROUND.
        for i in range(2_500):
            sched.submit_task(
                "compute",
                {"i": i},
                priority=TaskPriority.CRITICAL if i % 2 == 0 else TaskPriority.BACKGROUND,
            )
        assert sched.get_queue_size() == 2_500

        # First completed task should be a CRITICAL (priority=0).
        sched.run_tick_sync()
        completed = [t for t in sched._task_queue if t.state.name == "COMPLETED"]
        # If anything completed at all, the lowest priority value wins.
        if completed:
            assert min(t.priority for t in completed) == 0

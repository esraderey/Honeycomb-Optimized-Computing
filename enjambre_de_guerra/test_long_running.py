"""Stress: endurance — ticks largos sin perf drift.

Hipótesis bajo prueba:
- 5000 ticks consecutivos en un grid radio-2 sin perf drift
  (segundo-half del run no más lento que el primero, dentro de
  ±50%).
- Memory growth bounded — RSS no crece linealmente con tick count
  (lo que indicaría leak).
- Counters tick_count / tasks_completed monotonically increase.
- _task_index no acumula stale entries (B2.5 fix).
- BehaviorIndex._tombstoned se compacta periódicamente.
"""

from __future__ import annotations

import sys

import pytest

from enjambre_de_guerra._harness import (
    build_loaded_scheduler,
    gc_now,
    rss_mb,
    stopwatch,
)

pytestmark = pytest.mark.stress


class TestLongRunning:
    @pytest.mark.slow
    def test_5000_ticks_no_perf_drift(self):
        """Tick 5000 veces; mide tiempo del primer cuarto vs último
        cuarto. Si el último es >2× el primero, hay drift."""
        _, _, sched = build_loaded_scheduler(
            radius=2, max_queue_size=10_000, queue_full_policy="raise"
        )
        # Mantener la cola alimentada para que el work stealing no se
        # apague.
        for _ in range(500):
            sched.submit_task("compute", {})

        QUARTER = 1_250
        # Warm up.
        for _ in range(50):
            sched.run_tick_sync()
            if sched.get_pending_count() < 50:
                for _ in range(50):
                    sched.submit_task("compute", {})

        # First quarter timing.
        with stopwatch("first_quarter") as t1:
            for _ in range(QUARTER):
                if sched.get_pending_count() < 50:
                    for _ in range(50):
                        try:
                            sched.submit_task("compute", {})
                        except RuntimeError:
                            break  # queue full → drop policy might be raise; skip
                sched.run_tick_sync()

        # Middle two quarters (just to drive load).
        for _ in range(2 * QUARTER):
            if sched.get_pending_count() < 50:
                for _ in range(50):
                    try:
                        sched.submit_task("compute", {})
                    except RuntimeError:
                        break
            sched.run_tick_sync()

        # Last quarter timing.
        with stopwatch("last_quarter") as t2:
            for _ in range(QUARTER):
                if sched.get_pending_count() < 50:
                    for _ in range(50):
                        try:
                            sched.submit_task("compute", {})
                        except RuntimeError:
                            break
                sched.run_tick_sync()

        # Drift check: last quarter no más de 2× el primero. Esto da
        # margen para warm-up effects + ruido normal.
        assert t2["elapsed_s"] < t1["elapsed_s"] * 2.0, (
            f"perf drift detected: first quarter {t1['elapsed_s']:.2f}s, "
            f"last quarter {t2['elapsed_s']:.2f}s"
        )
        # Ticks contador tan alto como esperamos (5050 incluyendo warmup).
        assert sched._tick_count >= 5_000

    @pytest.mark.slow
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="rss_mb returns 0.0 on Windows without psutil",
    )
    def test_memory_bounded_over_5000_ticks(self):
        """RSS no debe crecer linealmente con tick count. Margen
        permisivo (≤30 MiB sobre 5000 ticks) porque el GC puede
        retener bloques."""
        _, _, sched = build_loaded_scheduler(
            radius=2, max_queue_size=10_000, queue_full_policy="raise"
        )
        gc_now()
        rss_start = rss_mb()
        for _ in range(5_000):
            if sched.get_pending_count() < 100:
                for _ in range(100):
                    try:
                        sched.submit_task("compute", {})
                    except RuntimeError:
                        break
            sched.run_tick_sync()
        gc_now()
        rss_end = rss_mb()

        delta = rss_end - rss_start
        # Bound generoso. Si el delta supera 30 MiB, hay leak real.
        assert delta < 30.0, (
            f"memory leak suspect: RSS grew {delta:.1f} MiB over 5K ticks "
            f"(start={rss_start:.1f}, end={rss_end:.1f})"
        )

    def test_task_index_does_not_accumulate(self):
        """1000 tasks completados; _task_index queda casi vacío
        (B2.5 fix). Sin esto, cada task completed leakeaba un entry."""
        from hoc.swarm import TaskState

        _, _, sched = build_loaded_scheduler(
            radius=1, max_queue_size=10_000, queue_full_policy="raise"
        )
        for _ in range(1_000):
            t = sched.submit_task("compute", {})
            t.state = TaskState.COMPLETED  # force completion
        # Drain the cleanup loop.
        for _ in range(50):
            sched.run_tick_sync()

        # Index should be near-empty after cleanup.
        assert len(sched._task_index) < 50, (
            f"_task_index accumulated {len(sched._task_index)} entries — "
            f"B2.5 cleanup regression"
        )

    def test_behavior_index_tombstones_eventually_compact(self):
        """Después de N×INDEX_COMPACT_INTERVAL_TICKS ticks, los
        tombstones del BehaviorIndex deben quedar limpios."""
        from hoc.swarm import SwarmScheduler

        _, _, sched = build_loaded_scheduler(
            radius=2, max_queue_size=10_000, queue_full_policy="raise"
        )
        # Submit + cancel mucho para acumular tombstones.
        for _ in range(500):
            t = sched.submit_task("compute", {})
            sched.cancel_task(t.task_id)

        assert len(sched._behavior_index._tombstoned) > 0

        # Run enough ticks para forzar al menos un compact pass.
        for _ in range(SwarmScheduler.INDEX_COMPACT_INTERVAL_TICKS + 5):
            sched.run_tick_sync()

        # Tombstones drained.
        assert sched._behavior_index._tombstoned == set()

    def test_monotonic_counters_over_long_run(self):
        """tick_count, tasks_completed son monotonically increasing.
        Cualquier wrap o reset es bug."""
        _, _, sched = build_loaded_scheduler(
            radius=1, max_queue_size=5_000, queue_full_policy="raise"
        )
        last_tick = 0
        last_completed = 0
        for _ in range(200):
            for _ in range(20):
                try:
                    sched.submit_task("compute", {})
                except RuntimeError:
                    break
            sched.run_tick_sync()
            assert sched._tick_count >= last_tick
            assert sched._tasks_completed >= last_completed
            last_tick = sched._tick_count
            last_completed = sched._tasks_completed

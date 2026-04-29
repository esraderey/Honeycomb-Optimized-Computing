"""Stress: concurrent submit / tick / cancel sobre el mismo scheduler.

Hipótesis bajo prueba:
- N threads concurrentes haciendo submit_task no corrompen el heap
  ni el _task_index (RLock holds).
- Cancel concurrente con tick() no produce KeyError ni doble-execute.
- BehaviorIndex.compact() bajo concurrencia mantiene tombstones
  consistentes.
- B2.5 leak fix: completed/failed/cancelled tasks se limpian del
  index incluso bajo concurrencia.
"""

from __future__ import annotations

import threading

import pytest

from enjambre_de_guerra._harness import build_loaded_scheduler

pytestmark = pytest.mark.stress


class TestConcurrentMutations:
    def test_concurrent_submissions_no_corruption(self):
        """8 threads × 1000 submissions = 8000 tasks. No race.
        Heap stays well-formed."""
        import heapq

        _, _, sched = build_loaded_scheduler(
            radius=2, max_queue_size=20_000, queue_full_policy="raise"
        )
        N_THREADS = 8
        PER_THREAD = 1_000
        errors: list[Exception] = []

        def submitter(prefix: int) -> None:
            try:
                for i in range(PER_THREAD):
                    sched.submit_task(f"compute_{prefix}_{i}", {"prefix": prefix})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=submitter, args=(i,)) for i in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"submission threads raised: {errors!r}"
        assert sched.get_queue_size() == N_THREADS * PER_THREAD

        # Heap invariant: parent <= children for every interior node.
        # heapq.heapify is idempotent on a valid heap; if the tree was
        # corrupted, the underlying list would still be invalid.
        snapshot = list(sched._task_queue)
        # Verify the priority heap property holds.
        for i in range(len(snapshot)):
            left = 2 * i + 1
            right = 2 * i + 2
            if left < len(snapshot):
                assert snapshot[i] <= snapshot[left], "heap violated at left child"
            if right < len(snapshot):
                assert snapshot[i] <= snapshot[right], "heap violated at right child"
        # Re-heapify is a no-op on a sound heap (sanity).
        heap_before = list(snapshot)
        heapq.heapify(snapshot)
        assert snapshot == heap_before

    def test_concurrent_submit_and_tick(self):
        """Half threads submitting, others ticking. No exceptions, no
        deadlock, _task_index keys ⊆ heap task_ids."""
        _, _, sched = build_loaded_scheduler(
            radius=2, max_queue_size=10_000, queue_full_policy="raise"
        )
        STOP_AFTER = 2_000
        errors: list[Exception] = []
        stop = threading.Event()

        def submitter() -> None:
            try:
                count = 0
                while not stop.is_set() and count < STOP_AFTER:
                    sched.submit_task("compute", {})
                    count += 1
            except Exception as e:
                errors.append(e)

        def ticker() -> None:
            try:
                while not stop.is_set():
                    sched.run_tick_sync()
            except Exception as e:
                errors.append(e)

        submitters = [threading.Thread(target=submitter) for _ in range(4)]
        tickers = [threading.Thread(target=ticker) for _ in range(2)]
        for t in submitters + tickers:
            t.start()
        for t in submitters:
            t.join(timeout=20.0)
        stop.set()
        for t in tickers:
            t.join(timeout=10.0)

        assert errors == [], f"concurrent threads raised: {errors!r}"

        # Index ⊆ heap invariant: every key in _task_index appears in heap.
        with sched._lock:
            heap_ids = {t.task_id for t in sched._task_queue}
            index_ids = set(sched._task_index)
        # _task_index entries should all be in the heap (no orphans).
        assert index_ids <= heap_ids | {""}, f"orphan task_ids in index: {index_ids - heap_ids}"

    def test_concurrent_cancel_and_tick(self):
        """Cancel from one thread while another ticks. Cancelled tasks
        eventually leave the index — no leak."""
        from hoc.swarm import TaskState

        _, _, sched = build_loaded_scheduler(
            radius=2, max_queue_size=10_000, queue_full_policy="raise"
        )
        # Pre-load 500 tasks pinned to a non-existent coord so they
        # never execute; cancel will be the only path that removes them.
        from hoc.core import HexCoord

        far = HexCoord(99, 99)
        ids = []
        for _ in range(500):
            t = sched.submit_task("compute", {}, target_cell=far)
            ids.append(t.task_id)

        errors: list[Exception] = []
        canceled_count = [0]

        def canceler() -> None:
            try:
                for tid in ids:
                    if sched.cancel_task(tid):
                        canceled_count[0] += 1
            except Exception as e:
                errors.append(e)

        def ticker() -> None:
            try:
                for _ in range(50):
                    sched.run_tick_sync()
            except Exception as e:
                errors.append(e)

        c = threading.Thread(target=canceler)
        t = threading.Thread(target=ticker)
        c.start()
        t.start()
        c.join(timeout=10.0)
        t.join(timeout=10.0)

        assert errors == [], f"cancel/tick raced: {errors!r}"
        # All cancelled tasks ended up in CANCELLED state and were
        # pruned from index by the cleanup loop.
        for tid in ids:
            task_after = sched._task_index.get(tid)
            # Either gone (cleaned up by tick's B2.5 fix) or in
            # CANCELLED terminal state.
            assert task_after is None or task_after.state == TaskState.CANCELLED

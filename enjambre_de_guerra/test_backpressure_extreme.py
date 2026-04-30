"""Stress: backpressure policies bajo carga extrema.

Hipótesis bajo prueba:
- 100K submissions con queue_size=100 + drop_oldest:
  ``tasks_dropped`` == 99_900 exacto, queue.size == 100.
- 50K submissions con drop_newest: el primer 100 sobrevive, el resto
  cancela.
- block policy con timeout 100ms: si el productor sigue empujando
  más rápido que el ticker drena, el productor empieza a hit timeout
  consistentemente (signal accurate).
- Mix de prioridades durante drop_oldest: las CRITICAL sobreviven,
  las BACKGROUND son las que evictan.
"""

from __future__ import annotations

import threading
import time

import pytest

from enjambre_de_guerra._harness import build_loaded_scheduler
from hoc.swarm import TaskPriority, TaskState

pytestmark = pytest.mark.stress


class TestBackpressureExtreme:
    @pytest.mark.slow
    def test_100k_drops_99900_exact(self):
        """El brief de Phase 7.3 pidió 10K → 9900 dropped. Aquí
        escalamos 10× para confirmar el invariant a esa magnitud."""
        cap = 100
        N = 100_000
        _, _, sched = build_loaded_scheduler(
            radius=1, max_queue_size=cap, queue_full_policy="drop_oldest"
        )
        for _ in range(N):
            sched.submit_task("compute", {})
        assert sched.get_queue_size() == cap
        assert sched.get_stats()["tasks_dropped"] == N - cap

    def test_50k_drop_newest_first_100_survive(self):
        """Con drop_newest, los primeros que entraron sobreviven.
        Los siguientes 49_900 son cancelled."""
        cap = 100
        N = 50_000
        _, _, sched = build_loaded_scheduler(
            radius=1, max_queue_size=cap, queue_full_policy="drop_newest"
        )
        first_100_ids = []
        for i in range(N):
            t = sched.submit_task("compute", {"i": i})
            if i < cap:
                first_100_ids.append(t.task_id)

        ids_in_queue = {t.task_id for t in sched._task_queue}
        # Todos los primeros 100 sobreviven en la cola.
        assert set(first_100_ids) == ids_in_queue
        assert sched.get_stats()["tasks_dropped"] == N - cap

    def test_drop_oldest_preserves_critical_priority(self):
        """5K submissions; alternando CRITICAL / BACKGROUND. Después de
        que la queue se llena (cap=100), el drop_oldest debe estar
        evictando BACKGROUND, dejando CRITICAL casi intactas."""
        cap = 100
        _, _, sched = build_loaded_scheduler(
            radius=1, max_queue_size=cap, queue_full_policy="drop_oldest"
        )
        for i in range(5_000):
            prio = TaskPriority.CRITICAL if i % 2 == 0 else TaskPriority.BACKGROUND
            sched.submit_task("compute", {"i": i}, priority=prio)

        priorities_in_queue = [t.priority for t in sched._task_queue]
        critical_count = sum(1 for p in priorities_in_queue if p == TaskPriority.CRITICAL.value)
        background_count = sum(1 for p in priorities_in_queue if p == TaskPriority.BACKGROUND.value)
        # CRITICAL debe dominar fuertemente la queue final (drop_oldest
        # evicta el "peor" = BACKGROUND).
        assert critical_count > background_count, (
            f"drop_oldest should evict BACKGROUND first; "
            f"got critical={critical_count} background={background_count}"
        )

    def test_block_policy_timeout_under_pressure(self):
        """Productor + consumidor a tasas mismatched → productor empieza
        a timeout. Validamos que el timeout es accurate
        (timeouts > 0 cuando productor outpaces consumer)."""
        cap = 5
        _, _, sched = build_loaded_scheduler(
            radius=1,
            max_queue_size=cap,
            queue_full_policy="block",
        )
        # Configuramos timeout corto (50ms) y poll frecuente (5ms).
        sched.config.queue_full_block_timeout_s = 0.05
        sched.config.queue_full_block_poll_s = 0.005

        # Pre-llenar la cola.
        for _ in range(cap):
            sched.submit_task("compute", {})

        # Sin ticker → cualquier nuevo submit debería timeout.
        timeouts = 0
        for _ in range(10):
            try:
                sched.submit_task("compute", {})
            except RuntimeError as e:
                if "timed out" in str(e):
                    timeouts += 1
        assert timeouts == 10, f"expected 10 timeouts, got {timeouts}"

    # ───────────────────────────────────────────────────────────────────
    # Edge cases — capacity en los bordes + races sobre el bound check
    # ───────────────────────────────────────────────────────────────────

    def test_capacity_1_drop_oldest_invariant(self):
        """capacity=1 es el límite degenerado: cada submit
        beyond el primero evicta al previo. dropped == N - 1."""
        N = 1_000
        _, _, sched = build_loaded_scheduler(
            radius=1, max_queue_size=1, queue_full_policy="drop_oldest"
        )
        for _ in range(N):
            sched.submit_task("compute", {})
        assert sched.get_queue_size() == 1
        assert sched.get_stats()["tasks_dropped"] == N - 1

    def test_capacity_n_plus_one_no_drops(self):
        """capacity == N + 1 (justo arriba del exact-fit): cero drops.
        Validate el off-by-one no mete drops espurios."""
        N = 500
        _, _, sched = build_loaded_scheduler(
            radius=1, max_queue_size=N + 1, queue_full_policy="drop_oldest"
        )
        for _ in range(N):
            sched.submit_task("compute", {})
        assert sched.get_queue_size() == N
        assert sched.get_stats()["tasks_dropped"] == 0

    def test_capacity_exact_fit_no_drops(self):
        """capacity == N exact: el último submit cabe sin drop.
        Otro chequeo de off-by-one — el comparison es ``len(queue) >=
        max_queue_size`` (overflow strict), not ``> max_queue_size``."""
        N = 500
        _, _, sched = build_loaded_scheduler(
            radius=1, max_queue_size=N, queue_full_policy="drop_oldest"
        )
        for _ in range(N):
            sched.submit_task("compute", {})
        assert sched.get_queue_size() == N
        assert sched.get_stats()["tasks_dropped"] == 0
        # Now one more triggers exactly one drop.
        sched.submit_task("compute", {})
        assert sched.get_queue_size() == N
        assert sched.get_stats()["tasks_dropped"] == 1

    def test_threaded_race_against_drop_oldest_count_invariant(self):
        """8 threads × 1000 submissions cada uno = 8000 submissions
        contra capacity=100 + drop_oldest. La suma final
        ``tasks_dropped + queue_size`` debe ser exactamente 8000.

        Si el lock entre el bound-check y el drop-oldest tuviera
        race, esto produciría un counter desync detectable."""
        cap = 100
        N_THREADS = 8
        PER_THREAD = 1_000
        _, _, sched = build_loaded_scheduler(
            radius=1, max_queue_size=cap, queue_full_policy="drop_oldest"
        )
        errors: list[Exception] = []

        def submitter() -> None:
            try:
                for _ in range(PER_THREAD):
                    sched.submit_task("compute", {})
            except Exception as e:
                errors.append(e)

        ts = [threading.Thread(target=submitter) for _ in range(N_THREADS)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30.0)

        assert errors == [], f"submitter raced: {errors!r}"
        total = N_THREADS * PER_THREAD
        in_q = sched.get_queue_size()
        dropped = sched.get_stats()["tasks_dropped"]
        # Hard invariant: every submit either landed in queue or
        # was counted as dropped. No silent loss.
        assert in_q + dropped == total, (
            f"counter desync under threaded drop_oldest: "
            f"queue={in_q} dropped={dropped} sum={in_q + dropped} != {total}"
        )
        assert in_q == cap, f"queue should be at cap, got {in_q}"

    def test_threaded_race_drop_newest_count_invariant(self):
        """Mismo invariant pero bajo drop_newest. Si el lock no
        cubre la decisión de aceptar/rechazar, los counters
        desincronizan."""
        cap = 50
        N_THREADS = 8
        PER_THREAD = 500
        _, _, sched = build_loaded_scheduler(
            radius=1, max_queue_size=cap, queue_full_policy="drop_newest"
        )
        errors: list[Exception] = []

        def submitter() -> None:
            try:
                for _ in range(PER_THREAD):
                    sched.submit_task("compute", {})
            except Exception as e:
                errors.append(e)

        ts = [threading.Thread(target=submitter) for _ in range(N_THREADS)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30.0)

        assert errors == []
        total = N_THREADS * PER_THREAD
        in_q = sched.get_queue_size()
        dropped = sched.get_stats()["tasks_dropped"]
        assert in_q + dropped == total
        assert in_q == cap

    def test_capacity_zero_rejects_everything(self):
        """capacity=0 con drop_newest: TODO submit es dropeado.
        Edge case raro pero válido — un caller podría usarlo como
        kill switch."""
        N = 100
        _, _, sched = build_loaded_scheduler(
            radius=1, max_queue_size=0, queue_full_policy="drop_newest"
        )
        for _ in range(N):
            sched.submit_task("compute", {})
        assert sched.get_queue_size() == 0
        assert sched.get_stats()["tasks_dropped"] == N

    def test_block_policy_drains_when_consumer_appears(self):
        """Una vez que un thread consumer empieza a tickear, el
        productor en block deja de timeout y completa."""
        cap = 2
        _, _, sched = build_loaded_scheduler(
            radius=1,
            max_queue_size=cap,
            queue_full_policy="block",
        )
        sched.config.queue_full_block_timeout_s = 2.0
        sched.config.queue_full_block_poll_s = 0.005

        # Pre-llenar.
        for _ in range(cap):
            sched.submit_task("compute", {})

        # Ticker thread drena la cola en background.
        stop = threading.Event()

        def ticker() -> None:
            while not stop.is_set():
                # Forzar tasks a COMPLETED para liberar slots.
                for t in list(sched._task_queue):
                    if t.state == TaskState.PENDING:
                        t.state = TaskState.COMPLETED
                sched.run_tick_sync()
                time.sleep(0.01)

        th = threading.Thread(target=ticker, daemon=True)
        th.start()
        try:
            # 5 submits adicionales — todos deben completar (no timeout).
            for _ in range(5):
                sched.submit_task("compute", {})
        finally:
            stop.set()
            th.join(timeout=3.0)

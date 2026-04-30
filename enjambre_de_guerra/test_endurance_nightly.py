"""Stress: endurance time-bounded — corre por minutos/horas, no
por tick count.

El test_long_running.py original ("5000 ticks") tarda <90s y NO es
endurance — es smoke test con números grandes. Endurance real
necesita:

- Tiempo de wall-clock significativo (10+ min) para que GC/heap
  fragmentation/scheduler drift aparezcan.
- Métricas explícitas con thresholds: p99 tick latency entre primer
  y último kilo de ticks debe estar dentro de un factor X.
- Memory growth bound observado en MiB, no asumido.
- Recovery cycles repetidos (no un solo path "happy submit + drain").

Por defecto este archivo está marcado ``@pytest.mark.nightly`` y
NO corre ni en el job stress-fast del CI ni localmente con
``pytest enjambre_de_guerra/``. Activación manual:

    pytest enjambre_de_guerra/test_endurance_nightly.py -v -m nightly

O via el workflow ``stress.yml`` (job stress-slow), invocado por
``gh workflow run stress.yml`` cuando se quiere validar pre-release.

ESTOS TESTS NO SON UNA GARANTÍA. Son evidencia conditional: "el
panal sobrevivió N horas en este hardware bajo este workload sin
síntomas observables". Endurance bajo carga real de Vent/CAMV es
otra cosa que requiere telemetría production-side.
"""

from __future__ import annotations

import gc
import os
import statistics
import sys
import time

import pytest

from enjambre_de_guerra._harness import (
    build_loaded_scheduler,
    rss_mb,
)

pytestmark = [pytest.mark.stress, pytest.mark.nightly]


# Configuración: el wall-clock budget. Default 10 min para mantenerlo
# corriendo en CI nightly. Para validación pre-release, override via
# env var: ``HOC_ENDURANCE_MINUTES=60 pytest ...``.
ENDURANCE_MINUTES = float(os.environ.get("HOC_ENDURANCE_MINUTES", "10"))


def _percentile(samples: list[float], p: float) -> float:
    """p-th percentile (0-100) without numpy."""
    if not samples:
        return 0.0
    s = sorted(samples)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


class TestEnduranceTimeBounded:
    def test_p99_tick_latency_no_drift(self):
        """Mide p99 tick latency en ventanas de N ticks por
        ENDURANCE_MINUTES. Comparamos primera ventana vs última.
        Si p99(last) > p99(first) × 3.0, hay drift real, no ruido.

        Threshold deliberadamente generoso (3×) porque sample noise
        bajo Python con GC en CI runners de menor rendimiento que
        un workstation. Cualquier drift más severo es bug.
        """
        deadline = time.perf_counter() + ENDURANCE_MINUTES * 60
        _, _, sched = build_loaded_scheduler(
            radius=2, max_queue_size=10_000, queue_full_policy="raise"
        )

        WINDOW = 500  # ticks per window for p99 measurement
        windows: list[list[float]] = []
        current_window: list[float] = []

        # Keep the queue fed during the run.
        def _refill():
            if sched.get_pending_count() < 50:
                for _ in range(50):
                    try:
                        sched.submit_task("compute", {})
                    except RuntimeError:
                        break

        # Warm-up.
        for _ in range(50):
            _refill()
            sched.run_tick_sync()

        while time.perf_counter() < deadline:
            _refill()
            t0 = time.perf_counter()
            sched.run_tick_sync()
            current_window.append(time.perf_counter() - t0)

            if len(current_window) >= WINDOW:
                windows.append(current_window)
                current_window = []

        if current_window:
            windows.append(current_window)

        assert len(windows) >= 4, (
            f"endurance run too short: only {len(windows)} windows "
            f"of {WINDOW} ticks each — bump ENDURANCE_MINUTES"
        )

        first_p99 = _percentile(windows[0], 99)
        last_p99 = _percentile(windows[-1], 99)
        median_p99 = statistics.median(_percentile(w, 99) for w in windows)

        # Drift threshold: last p99 within 3× of first p99.
        # The 3× margin absorbs GC noise + cache warmup; if drift
        # exceeds it, it's a real regression.
        assert last_p99 < first_p99 * 3.0, (
            f"p99 tick latency drift: first window p99={first_p99 * 1e6:.1f}µs, "
            f"last window p99={last_p99 * 1e6:.1f}µs (median={median_p99 * 1e6:.1f}µs); "
            f"ratio {last_p99 / first_p99:.2f}x exceeds 3x threshold"
        )

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="rss_mb returns 0.0 on Windows without psutil",
    )
    def test_memory_growth_bounded_over_time(self):
        """RSS samples a intervalos regulares por ENDURANCE_MINUTES.
        Linear regression slope debe ser cercana a 0 (heap stable).
        Threshold: > 5 MiB/min sostenido = leak."""
        deadline = time.perf_counter() + ENDURANCE_MINUTES * 60
        _, _, sched = build_loaded_scheduler(
            radius=2, max_queue_size=10_000, queue_full_policy="raise"
        )

        gc.collect()
        samples: list[tuple[float, float]] = []  # (elapsed_s, rss_mb)
        start = time.perf_counter()
        last_sample = start

        SAMPLE_INTERVAL_S = 30.0  # cada 30s

        while time.perf_counter() < deadline:
            if sched.get_pending_count() < 100:
                for _ in range(100):
                    try:
                        sched.submit_task("compute", {})
                    except RuntimeError:
                        break
            sched.run_tick_sync()

            now = time.perf_counter()
            if now - last_sample >= SAMPLE_INTERVAL_S:
                gc.collect()
                samples.append((now - start, rss_mb()))
                last_sample = now

        assert len(samples) >= 4, (
            f"need at least 4 RSS samples for slope; got {len(samples)} "
            f"(bump ENDURANCE_MINUTES or shorten SAMPLE_INTERVAL_S)"
        )

        # Simple linear regression (least squares) slope: rss = a + b*t.
        n = len(samples)
        sum_t = sum(t for t, _ in samples)
        sum_r = sum(r for _, r in samples)
        sum_tr = sum(t * r for t, r in samples)
        sum_tt = sum(t * t for t, _ in samples)
        slope_mb_per_s = (n * sum_tr - sum_t * sum_r) / (n * sum_tt - sum_t * sum_t)
        slope_mb_per_min = slope_mb_per_s * 60

        # Threshold: 5 MiB/min sostenido es leak suspect.
        assert slope_mb_per_min < 5.0, (
            f"RSS grew {slope_mb_per_min:.2f} MiB/min over {ENDURANCE_MINUTES}min "
            f"(samples: {samples!r}) — leak suspect"
        )

    def test_repeated_recovery_cycles(self):
        """Submit + drain + submit + drain × N ciclos. Verifica que
        el scheduler vuelve a su shape inicial después de cada ciclo
        (counters monotonic, _task_index empty entre ciclos)."""
        deadline = time.perf_counter() + ENDURANCE_MINUTES * 60
        _, _, sched = build_loaded_scheduler(
            radius=1, max_queue_size=5_000, queue_full_policy="raise"
        )

        cycle_count = 0
        last_completed = 0
        last_failed = 0

        while time.perf_counter() < deadline:
            # Submit batch.
            for _ in range(500):
                try:
                    sched.submit_task("compute", {})
                except RuntimeError:
                    break

            # Drain.
            for _ in range(200):
                if sched.get_pending_count() == 0:
                    break
                sched.run_tick_sync()

            # Counters must be monotonically non-decreasing across cycles.
            assert sched._tasks_completed >= last_completed
            assert sched._tasks_failed >= last_failed
            last_completed = sched._tasks_completed
            last_failed = sched._tasks_failed

            # _task_index should drain (B2.5 fix). Some entries may
            # linger if probabilistic refusal blocked drain — bound 100.
            assert len(sched._task_index) < 100, (
                f"_task_index leaked across cycle {cycle_count}: "
                f"{len(sched._task_index)} entries"
            )
            cycle_count += 1

        # Sanity: enough cycles to count as endurance.
        assert cycle_count >= 5, f"only {cycle_count} cycles ran in {ENDURANCE_MINUTES}min"

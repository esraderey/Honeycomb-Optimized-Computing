"""Stress: SandboxedTaskRunner bajo burst de tareas.

Hipótesis bajo prueba (POSIX only — Windows skipea):
- 100 sandboxed tasks consecutivos no leakean procesos.
- Mix de tareas (ok / raise / segfault / timeout) — el sandbox las
  contiene todas; ningún crash en el subprocess propaga al panal.
- Cada SandboxCrashed / SandboxTimeout deja al runner en estado
  utilizable para la siguiente tarea (no se queda colgado).
"""

from __future__ import annotations

import sys
import time

import pytest

from hoc.sandbox import (
    SandboxConfig,
    SandboxCrashed,
    SandboxedTaskRunner,
    SandboxTimeout,
)

pytestmark = [
    pytest.mark.stress,
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="Sandbox process isolation deferred a Phase 7.x en Windows",
    ),
]


# Top-level payloads (multiprocessing fork preserves globals via memory).
def _ok(x: int) -> int:
    return x * 2


def _slow(secs: float) -> str:
    time.sleep(secs)
    return "done"


def _boom() -> None:
    raise RuntimeError("intentional")


def _segfault() -> None:
    import ctypes

    ctypes.string_at(0)  # type: ignore[arg-type]


class TestSandboxBurst:
    @pytest.mark.slow
    def test_100_sandboxed_tasks_no_leak(self):
        """100 ok tasks consecutivos. Cada uno se ejecuta en un fork
        separado; ninguno deja zombies. Un test rápido de
        ``no_zombies`` requiere ``os.waitpid``-fu; aquí sólo
        verificamos que las tareas completan sin colgar el runner."""
        runner = SandboxedTaskRunner(SandboxConfig(timeout_s=5.0, isolation="process"))
        for i in range(100):
            assert runner.run(_ok, i) == i * 2

    def test_mixed_burst_25_each_outcome(self):
        """100 tareas: 25 ok, 25 raise, 25 segfault, 25 timeout. Todos
        contenidos; el runner sobrevive y procesa la siguiente."""
        runner = SandboxedTaskRunner(SandboxConfig(timeout_s=0.5, isolation="process"))
        outcomes = {"ok": 0, "crash": 0, "timeout": 0}
        for i in range(100):
            kind = i % 4
            try:
                if kind == 0:
                    assert runner.run(_ok, i) == i * 2
                    outcomes["ok"] += 1
                elif kind == 1:
                    runner.run(_boom)
                    pytest.fail("expected SandboxCrashed")
                elif kind == 2:
                    runner.run(_segfault)
                    pytest.fail("expected SandboxCrashed")
                else:
                    runner.run(_slow, 5.0)
                    pytest.fail("expected SandboxTimeout")
            except SandboxCrashed:
                outcomes["crash"] += 1
            except SandboxTimeout:
                outcomes["timeout"] += 1

        assert outcomes["ok"] == 25
        assert outcomes["crash"] == 50  # raise + segfault both → SandboxCrashed
        assert outcomes["timeout"] == 25

    def test_panal_survives_burst_of_failures(self):
        """20 segfaults consecutivos; después una tarea ok funciona
        normalmente. Validamos que no se acumula state corrupto en el
        runner."""
        runner = SandboxedTaskRunner(SandboxConfig(timeout_s=5.0, isolation="process"))
        for _ in range(20):
            with pytest.raises(SandboxCrashed):
                runner.run(_segfault)
        # Final ok task succeeds.
        assert runner.run(_ok, 7) == 14

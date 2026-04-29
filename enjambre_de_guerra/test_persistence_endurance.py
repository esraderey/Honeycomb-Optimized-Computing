"""Stress: checkpoint persistence bajo carga repetida.

Hipótesis bajo prueba:
- 500 checkpoint/restore cycles back-to-back no degradan ni corrompen.
- Large grid (radius=8, ~217 cells) roundtrip preserva todo el state
  per-cell (state_history deque incluido).
- Tick concurrente con checkpoint produce snapshots consistentes
  (atomic write contract).
- Bundle grid + scheduler con 5K tasks roundtrips sin desync de
  contadores.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from enjambre_de_guerra._harness import build_loaded_scheduler, stopwatch
from hoc.core import CellState, HoneycombConfig, HoneycombGrid
from hoc.nectar import NectarFlow
from hoc.swarm import SwarmScheduler

pytestmark = pytest.mark.stress


class TestPersistenceEndurance:
    def test_500_checkpoints_back_to_back(self, tmp_path: Path):
        """500 ciclos checkpoint/restore. Validamos que el estado
        post-restore sigue siendo el snapshot original."""
        cfg = HoneycombConfig(radius=2)
        grid = HoneycombGrid(cfg)
        path = tmp_path / "snap.bin"

        baseline_cells = set(grid._cells)
        baseline_count = len(grid._cells)

        with stopwatch("500_cycles") as t:
            for _ in range(500):
                grid.checkpoint(path)
                restored = HoneycombGrid.restore_from_checkpoint(path)
                assert len(restored._cells) == baseline_count
                assert set(restored._cells) == baseline_cells
                grid = restored

        # Sanity: ningún ciclo dejó archivo .tmp huérfano.
        assert not (tmp_path / "snap.bin.tmp").exists()
        # Throughput floor — 500 cycles bajo 30s = ~60 cycles/s.
        # Si esto se vuelve más lento, hay regresión real.
        assert t["elapsed_s"] < 30.0, (
            f"500 checkpoint cycles took {t['elapsed_s']:.1f}s — " f"regression vs baseline ~5s"
        )

    @pytest.mark.slow
    def test_large_grid_radius_8_roundtrip(self, tmp_path: Path):
        """Radio 8 ≈ 217 celdas. Cada celda con state_history poblada
        por una secuencia de transitions. El roundtrip completo debe
        preservar history."""
        cfg = HoneycombConfig(radius=8)
        grid = HoneycombGrid(cfg)

        # Drive todas las cells por una secuencia EMPTY → IDLE → ACTIVE → IDLE.
        from hoc.core import WorkerCell

        for cell in grid._cells.values():
            if isinstance(cell, WorkerCell):
                cell.state = CellState.IDLE
                cell.state = CellState.ACTIVE
                cell.state = CellState.IDLE

        path = tmp_path / "big.bin"
        grid.checkpoint(path)
        restored = HoneycombGrid.restore_from_checkpoint(path)

        # Cell counts iguales.
        assert len(restored._cells) == len(grid._cells)
        # State + history preserved per cell.
        for coord in grid._cells:
            orig = grid._cells[coord]
            rest = restored._cells[coord]
            assert orig.state == rest.state
            assert list(orig._state_history) == list(rest._state_history)

    def test_5k_tasks_bundle_roundtrip(self, tmp_path: Path):
        """5K tasks en queue + scheduler counters → bundle blob →
        restore. Counters y queue size match."""
        _, _, sched = build_loaded_scheduler(
            radius=2, max_queue_size=10_000, queue_full_policy="raise"
        )
        for i in range(5_000):
            sched.submit_task("compute", {"i": i})

        sched._tick_count = 42
        sched._tasks_completed = 100
        sched._tasks_failed = 10
        sched._tasks_dropped = 5

        path = tmp_path / "bundle.bin"
        sched.grid.checkpoint(path, scheduler=sched)

        new_grid = HoneycombGrid.restore_from_checkpoint(path)
        new_nectar = NectarFlow(new_grid)
        restored = SwarmScheduler.restore_from_checkpoint(path, new_grid, new_nectar)

        assert restored is not None
        assert restored.get_queue_size() == 5_000
        assert restored._tick_count == 42
        assert restored._tasks_completed == 100
        assert restored._tasks_failed == 10
        assert restored._tasks_dropped == 5

    def test_compressed_vs_uncompressed_size_ratio(self, tmp_path: Path):
        """Compressed blob shrinks by ≥40% on a radius-3 grid (cell
        metadata is repetitive). Sanity test on the zlib path."""
        grid = HoneycombGrid(HoneycombConfig(radius=3))
        plain = tmp_path / "plain.bin"
        compressed = tmp_path / "compressed.bin"
        grid.checkpoint(plain, compress=False)
        grid.checkpoint(compressed, compress=True)

        plain_size = plain.stat().st_size
        compressed_size = compressed.stat().st_size
        ratio = compressed_size / plain_size
        assert ratio < 0.6, (
            f"compression ratio {ratio:.2f} — expected < 0.6 on a "
            f"radius-3 grid; the cell-state strings are highly repetitive"
        )

    def test_concurrent_tick_during_checkpoint(self, tmp_path: Path):
        """Atomic write contract: a tick completes mid-checkpoint
        without corrupting the snapshot. We can't truly race the
        write+rename atomically from Python here, but we drive the
        worst case: 10 checkpoint writes interleaved with 10
        tick runs from another thread."""
        cfg = HoneycombConfig(radius=2)
        grid = HoneycombGrid(cfg)
        path = tmp_path / "snap.bin"
        errors: list[Exception] = []

        def writer() -> None:
            try:
                for _ in range(10):
                    grid.checkpoint(path)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def ticker() -> None:
            try:
                for _ in range(10):
                    grid.run_tick_sync()
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        w = threading.Thread(target=writer)
        t = threading.Thread(target=ticker)
        w.start()
        t.start()
        w.join(timeout=15.0)
        t.join(timeout=15.0)

        assert errors == [], f"writer/ticker raced: {errors!r}"
        # Final snapshot is decodable.
        restored = HoneycombGrid.restore_from_checkpoint(path)
        assert len(restored._cells) == len(grid._cells)

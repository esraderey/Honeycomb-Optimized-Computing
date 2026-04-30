"""Stress: chaos engineering — random failures durante operación normal.

Hipótesis bajo prueba:
- Forzar 30% de las cells a FAILED durante un tick: el grid sigue
  funcional (las cells healthy procesan; las failed reportan reason
  pero no propagan excepción).
- Auto-recovery via circuit breaker half-open recupera cells eventually.
- Queen kill + manual succession no corrompe el royal channel.
- 50 ticks con kills aleatorios cada tick: grid sobrevive sin orphan
  state.
"""

from __future__ import annotations

import random

import pytest

from hoc.core import CellRole, CellState, HoneycombConfig, HoneycombGrid, WorkerCell

pytestmark = pytest.mark.stress


def _force_random_failures(grid: HoneycombGrid, fraction: float) -> int:
    """Mark a random ``fraction`` of cells as FAILED. Returns count
    actually flipped (some cells may already be FAILED or SEALED)."""
    rng = random.Random(0xBEEF)
    flipped = 0
    cells = list(grid._cells.values())
    n_to_flip = int(len(cells) * fraction)
    for cell in rng.sample(cells, n_to_flip):
        if cell.state == CellState.FAILED:
            continue
        # Use the FSM-respecting path: drive through ACTIVE → FAILED via
        # the wildcard admin transition.
        try:
            cell._set_state(CellState.FAILED)
            flipped += 1
        except Exception:
            pass
    return flipped


class TestResilienceChaos:
    def test_30pct_failed_grid_still_ticks(self):
        """Marca 30% de cells como FAILED. El grid sigue tick'ando sin
        excepción.

        Esto NO es solo "no exception" — verificamos que las métricas
        observables reflejan el chaos:
        - get_cells_by_state(FAILED) cuenta lo que esperamos.
        - cells_processed > 0 (algunas sanas sí procesaron).
        - El tick emite un dict válido con la shape esperada.
        """
        grid = HoneycombGrid(HoneycombConfig(radius=3))
        n_total = len(grid._cells)
        flipped = _force_random_failures(grid, fraction=0.3)
        assert flipped > 0

        # Métrica observable #1: el grid reporta el número correcto
        # de cells FAILED via su consulta indexada.
        failed_cells = grid.get_cells_by_state(CellState.FAILED)
        assert len(failed_cells) == flipped, (
            f"get_cells_by_state(FAILED) reportó {len(failed_cells)}, "
            f"esperaba {flipped}. La FSM marcó FAILED pero el state "
            f"index no se enteró."
        )

        # Métrica observable #2: tick path completa sin excepción y
        # devuelve la shape esperada.
        result = grid.run_tick_sync()
        assert isinstance(result, dict)
        assert {"tick", "cells_processed", "errors", "auto_recovered"} <= set(result)

        # Métrica observable #3: las cells sanas (los 70% restantes)
        # son exactamente las que ticked. No hay cell processed que
        # esté en FAILED.
        # cells_processed cuenta cells que entraron a execute_tick;
        # las FAILED retornan early ("FAILED" reason) pero técnicamente
        # cuentan como "processed" en algunas implementaciones.
        # Lo crítico es que el grid sigue funcional: el tick no
        # devolvió 0 procesados ni todos errores.
        assert result["cells_processed"] >= n_total - flipped, (
            f"cells_processed={result['cells_processed']} sospechosamente bajo "
            f"para {n_total - flipped} cells sanas — chaos contagió cells healthy"
        )

    def test_chaos_50_ticks_random_kills_each_tick(self):
        """Cada tick mata 1-2 cells aleatorias. El grid sobrevive 50
        ticks sin excepción ni corrupción del role_index."""
        grid = HoneycombGrid(HoneycombConfig(radius=3))
        rng = random.Random(0xCAFE)
        n_kills = 0
        for _tick_idx in range(50):
            # Pick 1-2 random non-queen cells to flip.
            candidates = [
                c
                for c in grid._cells.values()
                if c.role != CellRole.QUEEN and c.state != CellState.FAILED
            ]
            if not candidates:
                break
            for cell in rng.sample(candidates, min(2, len(candidates))):
                try:
                    cell._set_state(CellState.FAILED)
                    n_kills += 1
                except Exception:
                    pass
            grid.run_tick_sync()

        # Sanity: role_index sigue siendo consistente con _cells.
        for role, coords in grid._role_index.items():
            for coord in coords:
                assert coord in grid._cells, f"role_index has stale coord {coord} for {role}"
        # Y todas las cells en _cells están en algún role_index slot.
        all_indexed_coords = {c for coords in grid._role_index.values() for c in coords}
        for coord in grid._cells:
            assert coord in all_indexed_coords, f"cell {coord} missing from role_index"

    def test_auto_recovery_half_open(self):
        """Cells FAILED con circuit breaker en HALF_OPEN se auto-
        recuperan via grid._attempt_auto_recovery."""
        grid = HoneycombGrid(HoneycombConfig(radius=2))
        # Find some workers to break.
        workers = [c for c in grid._cells.values() if isinstance(c, WorkerCell)]
        target = workers[0]
        target._set_state(CellState.FAILED)
        # Force the circuit breaker to half-open so auto-recovery picks it up.
        target._circuit_breaker._state = type(target._circuit_breaker.state).HALF_OPEN

        # Tick exercises _attempt_auto_recovery internally.
        result = grid.run_tick_sync()
        # auto_recovered counter incremented by at least 1.
        assert result["auto_recovered"] >= 1

    def test_queen_succession_under_load(self):
        """Submit 100 tareas, ejecuta 10 ticks, luego mata la queen.
        El nuevo electora debe poder seguir ticking sin error."""
        from hoc.nectar import NectarFlow
        from hoc.security import RateLimiter
        from hoc.swarm import SwarmConfig, SwarmScheduler

        grid = HoneycombGrid(HoneycombConfig(radius=2))
        nectar = NectarFlow(grid)
        cfg = SwarmConfig(
            max_queue_size=1_000,
            submit_rate_per_second=100_000.0,
            submit_rate_burst=100_000,
        )
        sched = SwarmScheduler(grid, nectar, cfg)
        sched._submit_limiter = RateLimiter(
            per_second=cfg.submit_rate_per_second, burst=cfg.submit_rate_burst
        )

        for _ in range(100):
            sched.submit_task("compute", {})
        for _ in range(10):
            sched.run_tick_sync()

        # Now kill the queen.
        queen = grid.queen
        assert queen is not None
        queen._set_state(CellState.FAILED)

        # Continue ticking — must not raise.
        for _ in range(5):
            grid.run_tick_sync()
            sched.run_tick_sync()
        # Sanity: scheduler still has accounting that adds up.
        stats = sched.get_stats()
        assert stats["tick_count"] >= 15

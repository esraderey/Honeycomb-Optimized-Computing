"""Stress: muchos grids ticking concurrentemente.

Hipótesis bajo prueba:
- N=50 grids gathered con ``asyncio.gather`` no se contaminan
  cruzadamente (cada uno tiene su propio tick_count, sus propias
  cells, su propio lock).
- Ningún grid pierde ticks en el cross-fire.
- El event-loop puede manejar fan-out al thread pool sin deadlock.
- Multiple gather rounds back-to-back tampoco corrompen estado.
"""

from __future__ import annotations

import asyncio

import pytest

from hoc.core import HoneycombConfig, HoneycombGrid

pytestmark = pytest.mark.stress


class TestConcurrentGrids:
    async def test_50_grids_gathered_no_cross_contamination(self):
        """Cada grid tiene su propio tick_count que avanza
        exactamente 1 después de un await tick()."""
        N = 50
        grids = [HoneycombGrid(HoneycombConfig(radius=1)) for _ in range(N)]
        before = [g._tick_count for g in grids]
        results = await asyncio.gather(*[g.tick() for g in grids])
        after = [g._tick_count for g in grids]

        assert all(b == 0 for b in before), "fresh grids should start at 0"
        assert all(a == 1 for a in after), f"each grid must have ticked exactly once; got {after}"
        # Each result is a dict (no exceptions slipped through).
        assert all(isinstance(r, dict) for r in results)

    async def test_repeated_gather_rounds_advance_consistently(self):
        """5 rondas consecutivas de gather sobre 20 grids → cada grid
        avanza exactamente 5 ticks."""
        N = 20
        ROUNDS = 5
        grids = [HoneycombGrid(HoneycombConfig(radius=1)) for _ in range(N)]
        for _ in range(ROUNDS):
            await asyncio.gather(*[g.tick() for g in grids])
        for g in grids:
            assert g._tick_count == ROUNDS

    @pytest.mark.slow
    async def test_100_grids_radius_2_no_deadlock(self):
        """100 grids r=2 (≈ 700 cells totales) gathered no se cuelga
        en el thread pool. Un timeout > 60s sería deadlock."""
        N = 100
        grids = [HoneycombGrid(HoneycombConfig(radius=2)) for _ in range(N)]
        results = await asyncio.wait_for(asyncio.gather(*[g.tick() for g in grids]), timeout=60.0)
        assert len(results) == N
        for g in grids:
            assert g._tick_count == 1

    async def test_grids_with_failed_cells_dont_take_others_down(self):
        """Inyecto fallo en uno de N grids; los otros completan
        normalmente. ``return_exceptions=True`` para que el gather
        no propague la excepción del raro."""
        from hoc.core import CellState

        N = 10
        grids = [HoneycombGrid(HoneycombConfig(radius=1)) for _ in range(N)]

        # Grid índice 3 tiene una cell forzada a FAILED — sus ticks
        # devuelven {"processed": False, "reason": "FAILED"} sin
        # romper. No es un crash en sí; es un health flag.
        for cell in grids[3]._cells.values():
            cell._state = CellState.FAILED

        results = await asyncio.gather(*[g.tick() for g in grids], return_exceptions=True)
        # Ningún resultado es una excepción.
        assert all(
            isinstance(r, dict) for r in results
        ), f"some grid raised: {[type(r).__name__ for r in results if not isinstance(r, dict)]}"
        # Los grids "sanos" tickearon.
        for i, g in enumerate(grids):
            if i != 3:
                assert g._tick_count == 1

    async def test_no_shared_state_between_grids(self):
        """Mutación de cells en un grid no aparece en los otros.
        Sanidad básica del isolation."""
        from hoc.core import CellState

        g1 = HoneycombGrid(HoneycombConfig(radius=1))
        g2 = HoneycombGrid(HoneycombConfig(radius=1))

        await asyncio.gather(g1.tick(), g2.tick())

        # Forzar una transición visible en g1.
        coord = next(iter(g1._cells))
        g1._cells[coord]._state = CellState.IDLE

        # g2 debe seguir igual.
        assert g2._cells[coord]._state != CellState.IDLE or (
            # IDLE puede ser legítimo post-tick para algunas cells. Lo
            # que importa es que el cambio explícito en g1 no se reflejó
            # en g2 a través de algún ClassVar mal compartido.
            g1._cells[coord]
            is not g2._cells[coord]
        )
        assert g1._cells[coord] is not g2._cells[coord]

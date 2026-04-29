"""Stress: cross-system invariants via Hypothesis.

Invariantes que **siempre** deben mantenerse, sin importar la
secuencia de ops:

- HexCoord.distance_to es simétrica (d(a,b) == d(b,a)).
- HexCoord.ring(n) tiene exactamente 6n cells (n>0) o 1 (n=0).
- Pheromone intensity es monotónicamente decreciente bajo decay puro
  (sin re-deposit).
- SwarmScheduler.tasks_completed + tasks_failed + tasks_dropped +
  pending == total_submitted (cuenta-de-tareas invariant).
- Checkpoint roundtrip: para cualquier grid restorado, set de coords
  + per-cell state idénticos al original.
- HiveTask.to_dict / from_dict son inversas sobre estados
  alcanzables.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from enjambre_de_guerra._harness import build_loaded_scheduler
from hoc.core import HexCoord, HoneycombConfig, HoneycombGrid
from hoc.nectar import PheromoneTrail, PheromoneType
from hoc.swarm import HiveTask

pytestmark = pytest.mark.stress


# Hypothesis strategies for HexCoord.
hex_coords = st.builds(HexCoord, q=st.integers(-50, 50), r=st.integers(-50, 50))


class TestGeometryInvariants:
    @given(a=hex_coords, b=hex_coords)
    @settings(max_examples=500, deadline=None)
    def test_distance_symmetric(self, a: HexCoord, b: HexCoord):
        """d(a, b) == d(b, a) por triángulo de hex coords."""
        assert a.distance_to(b) == b.distance_to(a)

    @given(a=hex_coords, b=hex_coords, c=hex_coords)
    @settings(max_examples=200, deadline=None)
    def test_distance_triangle_inequality(self, a: HexCoord, b: HexCoord, c: HexCoord):
        """d(a, c) <= d(a, b) + d(b, c)."""
        assert a.distance_to(c) <= a.distance_to(b) + b.distance_to(c)

    @given(center=hex_coords, n=st.integers(min_value=0, max_value=10))
    @settings(max_examples=100, deadline=None)
    def test_ring_size_correct(self, center: HexCoord, n: int):
        """ring(0) == 1 punto; ring(n>0) == 6n puntos."""
        ring = list(center.ring(n))
        expected = 1 if n == 0 else 6 * n
        assert len(ring) == expected

    @given(center=hex_coords, n=st.integers(min_value=1, max_value=8))
    @settings(max_examples=100, deadline=None)
    def test_ring_distance_uniform(self, center: HexCoord, n: int):
        """Todos los puntos del ring(n) están a distancia n del center."""
        for coord in center.ring(n):
            assert center.distance_to(coord) == n


class TestPheromoneInvariants:
    @given(
        intensity=st.floats(min_value=0.1, max_value=10.0),
        elapsed=st.floats(min_value=0.1, max_value=5.0),
        decay_rate=st.floats(min_value=0.01, max_value=0.5),
    )
    @settings(max_examples=200, deadline=None)
    def test_decay_monotonically_decreasing(
        self, intensity: float, elapsed: float, decay_rate: float
    ):
        """Sin re-deposit, decay_all(t) <= intensity_inicial."""
        from hoc.core.pheromone import PheromoneDeposit, PheromoneType as CorePType

        d = PheromoneDeposit(ptype=CorePType.FOOD, intensity=intensity, decay_rate=decay_rate)
        original = d.intensity
        new_intensity = d.decay(elapsed)
        assert new_intensity <= original
        # Y matemáticamente: I' = I * (1 - r)^t.
        expected = original * ((1.0 - decay_rate) ** elapsed)
        assert math.isclose(new_intensity, expected, rel_tol=1e-6)

    @given(n_deposits=st.integers(min_value=1, max_value=200))
    @settings(max_examples=20, deadline=None)
    def test_trail_lru_never_exceeds_max_coords(self, n_deposits: int):
        """Después de N deposits en coords distintas, len(deposits) <=
        max_coords."""
        max_coords = 50
        trail = PheromoneTrail(max_coords=max_coords)
        for i in range(n_deposits):
            trail.deposit(HexCoord(i, 0), PheromoneType.FOOD, 0.5)
        assert len(trail._deposits) <= max_coords


class TestSchedulerCounterInvariants:
    @given(
        n_submit=st.integers(min_value=10, max_value=500),
        cap=st.integers(min_value=5, max_value=100),
    )
    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_count_invariant_drop_oldest(self, n_submit: int, cap: int):
        """Para CUALQUIER N submissions con cap < N, el contador
        ``tasks_dropped + queue_size == N`` siempre."""
        _, _, sched = build_loaded_scheduler(
            radius=1, max_queue_size=cap, queue_full_policy="drop_oldest"
        )
        for i in range(n_submit):
            sched.submit_task("compute", {"i": i})
        dropped = sched.get_stats()["tasks_dropped"]
        in_queue = sched.get_queue_size()
        assert dropped + in_queue == n_submit

    @given(
        n_submit=st.integers(min_value=10, max_value=500),
        cap=st.integers(min_value=5, max_value=100),
    )
    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_count_invariant_drop_newest(self, n_submit: int, cap: int):
        """Para drop_newest: queue_size = min(N, cap), dropped = max(0, N - cap)."""
        _, _, sched = build_loaded_scheduler(
            radius=1, max_queue_size=cap, queue_full_policy="drop_newest"
        )
        for i in range(n_submit):
            sched.submit_task("compute", {"i": i})
        dropped = sched.get_stats()["tasks_dropped"]
        in_queue = sched.get_queue_size()
        assert in_queue == min(n_submit, cap)
        assert dropped == max(0, n_submit - cap)


class TestHiveTaskRoundtripInvariants:
    @given(
        priority=st.integers(min_value=0, max_value=4),
        task_type=st.sampled_from(["compute", "spawn", "warmup", "explore", "validate"]),
        attempts=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=200, deadline=None)
    def test_roundtrip_preserves_simple_fields(self, priority: int, task_type: str, attempts: int):
        """to_dict ∘ from_dict == identity sobre los campos primitivos."""
        task = HiveTask(priority=priority, task_type=task_type)
        task.attempts = attempts
        restored = HiveTask.from_dict(task.to_dict())
        assert restored.priority == priority
        assert restored.task_type == task_type
        assert restored.attempts == attempts

    @given(
        q=st.integers(min_value=-20, max_value=20),
        r=st.integers(min_value=-20, max_value=20),
    )
    @settings(max_examples=100, deadline=None)
    def test_roundtrip_preserves_target_cell(self, q: int, r: int):
        """target_cell sobrevive el roundtrip exact-match."""
        task = HiveTask(priority=2, task_type="compute", target_cell=HexCoord(q, r))
        restored = HiveTask.from_dict(task.to_dict())
        assert restored.target_cell == HexCoord(q, r)


class TestCheckpointInvariants:
    @given(radius=st.integers(min_value=1, max_value=4))
    @settings(
        max_examples=10,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    def test_grid_roundtrip_preserves_topology(self, radius: int):
        """Para CUALQUIER radius en [1, 4], el restored grid tiene el
        mismo set de coords que el original."""
        import tempfile
        from pathlib import Path as _Path

        cfg = HoneycombConfig(radius=radius)
        grid = HoneycombGrid(cfg)
        # tempfile manualmente — Hypothesis re-genera inputs sin
        # resetear function-scoped fixtures.
        with tempfile.TemporaryDirectory() as tmp:
            path = _Path(tmp) / "snap.bin"
            grid.checkpoint(path)
            restored = HoneycombGrid.restore_from_checkpoint(path)
        assert set(restored._cells) == set(grid._cells)
        assert restored.config.radius == radius

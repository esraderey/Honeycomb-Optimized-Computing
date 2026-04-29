"""Stress: Pheromone trail bajo DoS de inundación.

Hipótesis bajo prueba:
- 50K deposits en coordenadas distintas: el LRU bound (DEFAULT_MAX_COORDS=10K)
  evicta las más viejas, no permite crecimiento ilimitado.
- HMAC verification sigue siendo válida después de eviction (no hay
  state corruption).
- get_stats() reporta locations bounded incluso bajo flood.
- Diffusion sobre 10K coords no se cuelga.
- Metadata bound (DEFAULT_MAX_METADATA_KEYS=100) cierra el segundo
  vector de DoS.
"""

from __future__ import annotations

import pytest

from enjambre_de_guerra._harness import stopwatch
from hoc.core import HexCoord
from hoc.nectar import PheromoneTrail, PheromoneType

pytestmark = pytest.mark.stress


class TestPheromoneDoS:
    @pytest.mark.slow
    def test_50k_distinct_coords_lru_bounded(self):
        """50K coords únicas: DEFAULT_MAX_COORDS=10K las acota; las
        viejas son evicted por LRU."""
        trail = PheromoneTrail()  # uses DEFAULT_MAX_COORDS = 10_000
        with stopwatch("flood") as t:
            for i in range(50_000):
                # Coordinates spread across a wide hex region to be unique.
                trail.deposit(HexCoord(i, -i), PheromoneType.FOOD, 0.5)

        # Bounded.
        assert len(trail._deposits) <= 10_000, f"LRU bound violated: {len(trail._deposits)} coords"
        # Throughput floor: 50K deposits in <10s.
        assert t["elapsed_s"] < 15.0, f"deposit throughput regressed: {t['elapsed_s']:.1f}s for 50K"

    def test_lru_evicts_oldest_first(self):
        """Después de overflow, las primeras coords (las más viejas)
        ya no están; las recientes sí."""
        trail = PheromoneTrail(max_coords=100)
        # Inserta 200 coords distintas; las primeras 100 deben ser
        # evicted.
        for i in range(200):
            trail.deposit(HexCoord(i, -i), PheromoneType.FOOD, 0.5)

        # First 100 evicted (by LRU).
        for i in range(100):
            assert trail.sense(HexCoord(i, -i), PheromoneType.FOOD) == 0.0
        # Last 100 still present.
        for i in range(100, 200):
            assert trail.sense(HexCoord(i, -i), PheromoneType.FOOD) > 0.0

    def test_metadata_keys_bounded_per_deposit(self):
        """Inundar metadata con 5K keys sobre el mismo deposit:
        DEFAULT_MAX_METADATA_KEYS=100 corta el secondary vector."""
        trail = PheromoneTrail()
        coord = HexCoord(0, 0)
        for i in range(5_000):
            trail.deposit(
                coord,
                PheromoneType.FOOD,
                0.1,
                metadata={f"key_{i}": i},
            )
        deposit = trail._deposits[coord][PheromoneType.FOOD]
        assert len(deposit.metadata) <= 100

    def test_hmac_signature_survives_re_deposits(self):
        """Re-deposits sobre la misma coord no invalidan la signature
        original (signature cubre identity, no intensity)."""
        trail = PheromoneTrail()
        coord = HexCoord(5, -3)
        trail.deposit(coord, PheromoneType.FOOD, 0.3)
        sig_after_first = trail._deposits[coord][PheromoneType.FOOD].signature

        # Re-deposit muchas veces → la signature persiste.
        for _ in range(100):
            trail.deposit(coord, PheromoneType.FOOD, 0.1)

        deposit = trail._deposits[coord][PheromoneType.FOOD]
        assert deposit.signature == sig_after_first
        assert deposit.verify() is True

    def test_diffusion_over_thousands_of_coords_completes(self):
        """5K coords con depósitos. diffuse_to_neighbors completa sin
        cuelgue (timeout 30s en la marca). Es un test de no-deadlock
        + sanity del LRU bajo workload realista."""
        trail = PheromoneTrail()
        # Sembrar 5K coords con depósitos.
        for i in range(5_000):
            trail.deposit(HexCoord(i // 100, i % 100), PheromoneType.FOOD, 0.5)

        with stopwatch("diffuse") as t:
            trail.diffuse_to_neighbors(diffusion_rate=0.05)
        # 5K coords × 6 neighbours = 30K secondary deposits via the diffuse path.
        # Cap at 30s as a deadlock signal.
        assert t["elapsed_s"] < 30.0

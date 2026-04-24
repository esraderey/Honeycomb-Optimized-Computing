"""
HOC Core · Internal Metrics (transitional home)
===============================================

Clases de métricas que viven internamente dentro de ``hoc.core``:
``CellMetrics``, ``GridMetrics`` y ``MetricsCollector``.

Estas clases están *shadowed* por las equivalentes en :mod:`hoc.metrics`
para consumidores del paquete top-level (``from hoc import CellMetrics``
resuelve a :class:`hoc.metrics.CellMetrics`). La copia interna existe
porque :class:`hoc.core.grid.HoneycombGrid` registra su propio historial
mediante ``MetricsCollector.record(GridMetrics(...))`` y las celdas
exponen ``get_metrics() -> CellMetrics``.

Fase 3.3 (split de core): este módulo es un *home temporal*. La hoja de
ruta 3.4 moverá estas tres clases a ``hoc.metrics.collection`` y este
archivo se borrará. Por ahora se exporta también desde ``hoc.core`` para
mantener la API pública (`from hoc.core import CellMetrics, GridMetrics,
MetricsCollector`).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .cells import CellRole, CellState
from .grid_geometry import HexCoord

__all__ = ["CellMetrics", "GridMetrics", "MetricsCollector"]


# ═══════════════════════════════════════════════════════════════════════════════
# MÉTRICAS Y ESTADÍSTICAS (internas a core — shadowed por hoc.metrics para
# consumidores top-level).
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class CellMetrics:
    """Métricas de una celda individual."""

    coord: HexCoord
    role: CellRole
    state: CellState
    load: float
    vcore_count: int
    error_count: int
    ticks_processed: int
    pheromone_total: float
    neighbor_count: int
    last_activity: float
    circuit_state: str = "CLOSED"  # v3.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "coord": self.coord.to_dict(),
            "role": self.role.name,
            "state": self.state.name,
            "load": self.load,
            "vcore_count": self.vcore_count,
            "error_count": self.error_count,
            "ticks_processed": self.ticks_processed,
            "pheromone_total": self.pheromone_total,
            "neighbor_count": self.neighbor_count,
            "last_activity": self.last_activity,
            "circuit_state": self.circuit_state,
        }


@dataclass(slots=True)
class GridMetrics:
    """Métricas agregadas del grid."""

    timestamp: float
    total_cells: int
    active_cells: int
    idle_cells: int
    failed_cells: int
    total_vcores: int
    average_load: float
    max_load: float
    min_load: float
    load_stddev: float
    total_pheromones: float
    ticks_per_second: float
    errors_per_second: float
    work_steals: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MetricsCollector:
    """Colector de métricas thread-safe con historial y agregaciones."""

    __slots__ = ("_counters", "_history", "_last_sample", "_lock", "_max_history", "_sample_rate")

    def __init__(self, max_history: int = 1000, sample_rate: float = 1.0):
        self._history: deque = deque(maxlen=max_history)
        self._max_history = max_history
        self._sample_rate = sample_rate
        self._last_sample = 0.0
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)

    def record(self, metrics: GridMetrics) -> None:
        current = time.time()
        if current - self._last_sample >= 1.0 / self._sample_rate:
            with self._lock:
                self._history.append(metrics)
                self._last_sample = current

    def increment(self, counter: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[counter] += amount

    def get_counter(self, counter: str) -> int:
        return self._counters.get(counter, 0)

    def get_history(self, limit: int | None = None) -> list[GridMetrics]:
        with self._lock:
            if limit:
                return list(self._history)[-limit:]
            return list(self._history)

    def get_latest(self) -> GridMetrics | None:
        with self._lock:
            return self._history[-1] if self._history else None

    def get_averages(self, window: int = 60) -> dict[str, float]:
        history = self.get_history(window)
        if not history:
            return {}

        return {
            "avg_load": float(np.mean([m.average_load for m in history])),
            "avg_vcores": float(np.mean([m.total_vcores for m in history])),
            "avg_tps": float(np.mean([m.ticks_per_second for m in history])),
            "avg_errors": float(np.mean([m.errors_per_second for m in history])),
        }

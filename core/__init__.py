"""
HOC Core v3.0 - Estructuras Fundamentales del Panal Hexagonal (Producción)
=========================================================================

Implementación production-ready de geometría hexagonal y celdas del panal.
Usa coordenadas axiales (q, r) para navegación eficiente.

MEJORAS v3.0 sobre v2.0:
- [FIX] Variable shadowing en _cached_filled_hex (r param vs loop r)
- [FIX] EventBus weak refs que fallaban con bound methods
- [FIX] steal_from llamaba _update_load() fuera del lock
- [FIX] add_vcore/remove_vcore bypasseaban el setter de estado (sin eventos)
- [FIX] heapq comparisons fallaban con dicts no-comparables
- [FIX] Singleton global EventBus no era thread-safe
- [FIX] HoneycombConfig usaba assert (deshabilitado con -O)
- [FIX] RWLock tenía potential deadlock sin timeout
- [ARCH] __all__ para exports limpios
- [ARCH] Índices por rol/estado en HoneycombGrid para O(1) lookups
- [ARCH] Circuit breaker con backoff exponencial para recovery
- [ARCH] LRU eviction en StorageCell cuando llega a capacidad máxima
- [ARCH] GuardCell y ScoutCell implementados (estaban declarados pero no existían)
- [ARCH] from_dict() completo con restauración de estado
- [ARCH] HealthMonitor integrado con alertas configurables
- [PERF] Pheromone updates batched con NumPy
- [PERF] Lock-free reads donde es seguro (propiedades atómicas)
- [PERF] EventBus con backpressure y rate limiting
- [PERF] Bulk neighbor connection con operaciones vectoriales
- [ROBUST] Timeouts en lock acquisition
- [ROBUST] Graceful shutdown con cleanup garantizado
- [ROBUST] Rate limiting en event publishing
- [ROBUST] Validación exhaustiva en Config con mensajes claros

Sistema de Coordenadas Axiales:

        +r  ↗
           / \\
          /   \\
    <----⬡-----> +q
          \\   /
           \\ /
             ↘ +s (implícito: s = -q - r)

Direcciones (vecinos):

         NW(0)    NE(1)
            ↖   ↗
             \\ /
        W(5)──⬡──E(2)
             / \\
            ↙   ↘
         SW(4)    SE(3)

Fase 3.3 — estructura del subpaquete
------------------------------------
``core.py`` se partió en varios módulos internos. Todos los símbolos de la
API pública antigua se re-exportan desde aquí con identidad preservada
(``hoc.core.HexCoord is hoc.core.grid.HexCoord``):

- :mod:`.events` — bus de eventos, tipos y singleton.
- :mod:`.locking` — ``RWLock`` con timeouts.
- :mod:`.pheromone` — feromonas internas al grid.
- :mod:`.health` — circuit breaker y monitor de salud.
- :mod:`.grid_geometry` / :mod:`.grid_config` / :mod:`.grid` — geometría
  hexagonal, configuración y ``HoneycombGrid``.
- :mod:`.cells_base` / :mod:`.cells_specialized` / :mod:`.cells` —
  ``HoneycombCell`` y las 7 subclases especializadas.
- :mod:`._metrics_internal` — ``CellMetrics``/``GridMetrics``/
  ``MetricsCollector`` (home transitorio; se moverá a ``hoc.metrics``
  en la Fase 3.4).
- :mod:`.constants` — magic numbers extraídos (scaffolding).
"""

from __future__ import annotations

from ._metrics_internal import CellMetrics, GridMetrics, MetricsCollector
from .cells import (
    CellRole,
    CellState,
    DroneCell,
    GuardCell,
    HoneycombCell,
    NurseryCell,
    QueenCell,
    ScoutCell,
    StorageCell,
    WorkerCell,
)
from .events import (
    Event,
    EventBus,
    EventHandler,
    EventType,
    get_event_bus,
    reset_event_bus,
    set_event_bus,
)
from .grid import (
    HoneycombGrid,
    benchmark_grid,
    create_grid,
)
from .grid_config import GridTopology, HoneycombConfig
from .grid_geometry import (
    HexCoord,
    HexDirection,
    HexPathfinder,
    HexRegion,
    HexRing,
)
from .health import CircuitBreaker, CircuitState, HealthMonitor, HealthStatus
from .locking import RWLock
from .pheromone import PheromoneDeposit, PheromoneField, PheromoneType

__all__ = [
    # Core types
    "HexCoord",
    "HexDirection",
    "HexRegion",
    "HexRing",
    "HexPathfinder",
    # Events
    "EventType",
    "Event",
    "EventBus",
    "EventHandler",
    # Concurrency
    "RWLock",
    # Pheromones
    "PheromoneType",
    "PheromoneDeposit",
    "PheromoneField",
    # States & Roles
    "CellState",
    "CellRole",
    # Config
    "HoneycombConfig",
    "GridTopology",
    # Metrics
    "CellMetrics",
    "GridMetrics",
    "MetricsCollector",
    # Cells
    "HoneycombCell",
    "QueenCell",
    "WorkerCell",
    "DroneCell",
    "NurseryCell",
    "StorageCell",
    "GuardCell",
    "ScoutCell",
    # Grid
    "HoneycombGrid",
    # Health
    "HealthMonitor",
    "HealthStatus",
    "CircuitBreaker",
    "CircuitState",
    # Utilities
    "create_grid",
    "benchmark_grid",
    # Event bus management
    "get_event_bus",
    "set_event_bus",
    "reset_event_bus",
]

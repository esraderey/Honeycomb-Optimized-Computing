"""
FailoverFlow FSM (Phase 4.6)
============================

Conceptual flow of a cell failover in
:class:`hoc.resilience.CellFailover`:

::

    HEALTHY ─circuit_open─► DEGRADED ─strategy_picked─► MIGRATING
                                                            │
                                              ┌─────────────┴──────────┐
                                              ▼                        ▼
                                          RECOVERED               LOST
                                              │                        │
                                              └──stabilized──► HEALTHY ◀┘
                                                                   ▲
                                                                   │
                                                              new_cells_provisioned
                                                                   │
                                                                   └── (from LOST)

Undo
----

Tramoya's :meth:`tramoya.Machine.undo` is the natural fit for the
``MIGRATING → LOST`` failure path: if the workload migration stalls or
all mirror cells reject the workload, ``undo()`` returns the FSM to
``DEGRADED`` and the caller compensates by reattaching the workload to
the source (when the source is still responsive).

The same caveat as the rest of Phase 4 applies: undoing the FSM does
**not** revert external side effects. The caller of
:meth:`hoc.resilience.CellFailover.migrate_cell` is responsible for
restoring vCores to the source cell when the FSM rolls back.

Why declarative-only
--------------------

Same trade-off as the other Phase 4 FSMs (task / pheromone / succession).
``CellFailover`` exposes :meth:`migrate_cell` and :meth:`mark_recovered`
but does not maintain a per-cell failover state — failed cells live in a
``set[HexCoord]`` and migration is a single synchronous method.

Wiring this in would require:

1. A per-cell ``_failover_phase`` map on ``CellFailover``.
2. Splitting ``migrate_cell`` into separate methods for each transition
   (start, complete, abort) so FSM hooks have well-defined targets.
3. Carefully sequencing the FSM transitions inside the existing
   try/except block so a partial migration rolls back deterministically.

Phase 4 ships the FSM as documentation + Mermaid + property tests.
Phase 4 closure flags the wire-up as a deliberate gap; the natural time
to do it is when ``resilience.py`` is split into a subpackage (Phase
5+ per ADR-006), since that split will already require touching every
``CellFailover`` method.
"""

from __future__ import annotations

from .base import HocStateMachine, HocTransition

FAILOVER_HEALTHY = "HEALTHY"
FAILOVER_DEGRADED = "DEGRADED"
FAILOVER_MIGRATING = "MIGRATING"
FAILOVER_RECOVERED = "RECOVERED"
FAILOVER_LOST = "LOST"

ALL_FAILOVER_STATES: tuple[str, ...] = (
    FAILOVER_HEALTHY,
    FAILOVER_DEGRADED,
    FAILOVER_MIGRATING,
    FAILOVER_RECOVERED,
    FAILOVER_LOST,
)


def build_failover_fsm() -> HocStateMachine:
    """Build the FailoverFlow FSM. Declarative-only — see module docstring."""
    transitions: list[HocTransition] = [
        # Circuit breaker on the source cell opens: we leave HEALTHY for
        # DEGRADED. cells_base.py:411 (CIRCUIT_BREAKER_OPENED event), and
        # CellFailover picks up the failed-cells set.
        HocTransition(
            FAILOVER_HEALTHY,
            FAILOVER_DEGRADED,
            trigger="circuit_opened",
            guard=lambda ctx: bool(ctx.get("circuit_open", False)),
        ),
        # Failover strategy selected and a target chosen — start migration.
        # CellFailover.migrate_cell is invoked. resilience.py:317-337.
        HocTransition(
            FAILOVER_DEGRADED,
            FAILOVER_MIGRATING,
            trigger="strategy_picked",
            guard=lambda ctx: bool(ctx.get("target_cell") is not None),
        ),
        # Migration succeeded: target accepted vCores. resilience.py:332
        # (the source is marked FAILED, target inherits load).
        HocTransition(
            FAILOVER_MIGRATING,
            FAILOVER_RECOVERED,
            trigger="migration_succeeded",
            guard=lambda ctx: bool(ctx.get("vcores_migrated", 0) > 0),
        ),
        # Migration timeout or all mirrors rejected the workload — task
        # is lost. resilience.py:335-337 catches Exception.
        HocTransition(FAILOVER_MIGRATING, FAILOVER_LOST, trigger="migration_failed"),
        # New cells provisioned to take over the failed coordinate.
        # SwarmRecovery / HexRedundancy fill the slot.
        HocTransition(
            FAILOVER_LOST,
            FAILOVER_HEALTHY,
            trigger="cells_provisioned",
            guard=lambda ctx: bool(ctx.get("replacement_count", 0) > 0),
        ),
        # Stabilization window: RECOVERED settles back to HEALTHY after
        # the new owner stays operational across N ticks.
        HocTransition(
            FAILOVER_RECOVERED,
            FAILOVER_HEALTHY,
            trigger="stabilized",
            guard=lambda ctx: bool(
                ctx.get("ticks_stable", 0) >= ctx.get("stabilization_window", 1)
            ),
        ),
    ]

    return HocStateMachine(
        name="FailoverFlow",
        states=list(ALL_FAILOVER_STATES),
        transitions=transitions,
        initial=FAILOVER_HEALTHY,
        # Larger history because undo on the migration path is part of
        # the design — keep enough states to support multi-step rollbacks.
        history_size=16,
    )

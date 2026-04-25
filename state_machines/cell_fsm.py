"""
CellState FSM (Phase 4.3)
=========================

Formal state machine for :class:`hoc.core.cells_base.HoneycombCell`. Each
``HoneycombCell`` instance owns one :class:`HocStateMachine` built by
:func:`build_cell_fsm`; the cell's ``state`` setter delegates to it.

Cardinal invariant
------------------

This FSM **observes** the transitions HOC already executes — it is **not**
the aspirational lifecycle from the Phase 4 brief
(``INITIALIZING → IDLE → BUSY → DEGRADED → FAILED → RECOVERING``). The
real ``CellState`` enum (declared in ``core/cells_base.py``) has 9 values:
``EMPTY, IDLE, ACTIVE, SPAWNING, MIGRATING, FAILED, RECOVERING, SEALED,
OVERLOADED``. The brief's ``INITIALIZING`` maps to ``EMPTY``, ``BUSY`` to
``ACTIVE``, and the brief's ``DEGRADED`` step does not exist — production
code goes ``ACTIVE → FAILED`` directly when the circuit breaker opens.

Four states have **no incoming transitions** in current production code:
``SPAWNING``, ``MIGRATING``, ``SEALED``, ``OVERLOADED``. They are declared
in the enum and rendered by ``metrics.visualization`` but never assigned.
The FSM keeps them as legal nodes (the enum is the source of truth) but
attempting to transition into them raises :class:`IllegalStateTransition`.
This is reported as **B12** in the Phase 4 closure.

Transition catalog
------------------

The FSM has two kinds of transitions:

1. **Lifecycle (explicit source)** — invoked by ``HoneycombCell`` methods
   themselves (``add_vcore``, ``remove_vcore``, ``execute_tick``,
   ``recover``). These have specific source states.

2. **Admin / failover (wildcard source)** — invoked by surrounding
   subsystems that need to force a cell into a particular state regardless
   of where it was: :class:`hoc.resilience.CellFailover` (marks source
   ``FAILED`` after migration, marks target ``IDLE`` after recovery),
   :class:`hoc.resilience.QueenSuccession` (marks the deposed queen
   ``FAILED``), :class:`hoc.resilience.SwarmRecovery._restart_cell`,
   :class:`hoc.resilience.SwarmRecovery._rebuild_cell` (marks any cell
   ``RECOVERING`` then ``EMPTY`` or ``IDLE``), and tests that inject
   faults by writing ``cell.state = CellState.FAILED`` directly.

Both kinds coexist in the same FSM. The destination-driven
:meth:`HocStateMachine.transition_to` resolves the right trigger by
looking up the dest index — explicit-source matches take precedence over
wildcards (mirrors ``tramoya``'s own resolution order).

The ``transition_to(target)`` API is what the cell setter uses, so neither
``HoneycombCell`` nor its callers need to know the trigger names. Trigger
names exist for two reasons: visualization (Mermaid diagrams show the
trigger), and direct firing in tests / observability.
"""

from __future__ import annotations

from .base import WILDCARD, HocStateMachine, HocTransition

# State name constants — match the names of CellState enum members in
# core/cells_base.py. Keeping them as module-level strings means
# core/cells_base.py can import only what it needs from here without
# circular dependency on the enum. The wire-up site does
# ``CellState(x).name`` to bridge between enum and FSM.
CELL_STATE_EMPTY = "EMPTY"
CELL_STATE_ACTIVE = "ACTIVE"
CELL_STATE_IDLE = "IDLE"
CELL_STATE_SPAWNING = "SPAWNING"
CELL_STATE_MIGRATING = "MIGRATING"
CELL_STATE_FAILED = "FAILED"
CELL_STATE_RECOVERING = "RECOVERING"
CELL_STATE_SEALED = "SEALED"
CELL_STATE_OVERLOADED = "OVERLOADED"

ALL_CELL_STATES: tuple[str, ...] = (
    CELL_STATE_EMPTY,
    CELL_STATE_ACTIVE,
    CELL_STATE_IDLE,
    CELL_STATE_SPAWNING,
    CELL_STATE_MIGRATING,
    CELL_STATE_FAILED,
    CELL_STATE_RECOVERING,
    CELL_STATE_SEALED,
    CELL_STATE_OVERLOADED,
)


def build_cell_fsm() -> HocStateMachine:
    """
    Build a fresh CellState FSM. Used by:

    - :class:`hoc.core.cells_base.HoneycombCell.__init__` — one FSM per
      cell, attached to the cell's slot ``_fsm``.
    - :func:`scripts.generate_state_machines_md` — visualization.
    - The state-machines tests — legal/illegal transition exercise.

    Initial state is :data:`CELL_STATE_EMPTY`, matching ``HoneycombCell.__init__``.
    """
    transitions: list[HocTransition] = [
        # ── Lifecycle transitions (explicit source) ────────────────────────
        # add_vcore (cells_base.py:267): the first vCore wakes the cell up.
        HocTransition(CELL_STATE_EMPTY, CELL_STATE_IDLE, trigger="vcore_added"),
        # remove_vcore (cells_base.py:288): last vCore drains the cell.
        # Both IDLE and ACTIVE are valid sources because the call may race
        # with a tick that has already entered ACTIVE.
        HocTransition(CELL_STATE_IDLE, CELL_STATE_EMPTY, trigger="vcore_drained_idle"),
        HocTransition(CELL_STATE_ACTIVE, CELL_STATE_EMPTY, trigger="vcore_drained_active"),
        # execute_tick (cells_base.py:395): IDLE → ACTIVE on tick start.
        HocTransition(CELL_STATE_IDLE, CELL_STATE_ACTIVE, trigger="tick_started"),
        # execute_tick (cells_base.py:434): ACTIVE → IDLE on successful tick.
        HocTransition(CELL_STATE_ACTIVE, CELL_STATE_IDLE, trigger="tick_completed"),
        # execute_tick (cells_base.py:411): ACTIVE → FAILED when circuit breaker opens.
        HocTransition(CELL_STATE_ACTIVE, CELL_STATE_FAILED, trigger="tick_failed"),
        # recover (cells_base.py:449): FAILED → RECOVERING on explicit recovery.
        HocTransition(CELL_STATE_FAILED, CELL_STATE_RECOVERING, trigger="recovery_started"),
        # recover (cells_base.py:456): RECOVERING → EMPTY on recovery completion.
        HocTransition(CELL_STATE_RECOVERING, CELL_STATE_EMPTY, trigger="recovery_completed"),
        # CombRepair / SwarmRecovery._restart_cell (resilience.py:1134):
        # RECOVERING → IDLE when rebuild restores the cell with vCores.
        HocTransition(CELL_STATE_RECOVERING, CELL_STATE_IDLE, trigger="recovery_restored"),
        # ── Admin / failover transitions (wildcard source) ─────────────────
        # CellFailover marks the source cell FAILED after migration
        # (resilience.py:332). QueenSuccession marks the deposed queen
        # FAILED (resilience.py:722). Tests inject faults by writing
        # cell.state = FAILED directly. Any current state is admissible.
        HocTransition(WILDCARD, CELL_STATE_FAILED, trigger="admin_mark_failed"),
        # CellFailover.mark_recovered sets the cell IDLE
        # (resilience.py:346). SwarmRecovery._restart_cell ends at IDLE
        # (resilience.py:1134). _replicate_from_mirror ends at IDLE
        # (resilience.py:1163). CombRepair._repair_state_mismatch sets IDLE
        # (resilience.py:1389). All from arbitrary source states.
        HocTransition(WILDCARD, CELL_STATE_IDLE, trigger="admin_set_idle"),
        # SwarmRecovery._restart_cell starts the recovery from any state
        # (resilience.py:1128). _rebuild_cell same (resilience.py:1140).
        HocTransition(WILDCARD, CELL_STATE_RECOVERING, trigger="admin_recover"),
        # _rebuild_cell ends at EMPTY (resilience.py:1154) from RECOVERING,
        # but CombRepair may also reset from arbitrary states. Wildcard
        # keeps the contract honest.
        HocTransition(WILDCARD, CELL_STATE_EMPTY, trigger="admin_reset"),
        # Tests force ACTIVE for fault injection scenarios
        # (test_resilience.py:657, 704). Admin path because production code
        # never sets ACTIVE directly — only via execute_tick (already
        # covered by tick_started above).
        HocTransition(WILDCARD, CELL_STATE_ACTIVE, trigger="admin_force_active"),
    ]

    return HocStateMachine(
        name="CellState",
        states=list(ALL_CELL_STATES),
        transitions=transitions,
        initial=CELL_STATE_EMPTY,
        # Per-cell FSMs accumulate one history entry per state change.
        # Cap small to bound memory across thousands of cells in a grid.
        history_size=8,
    )

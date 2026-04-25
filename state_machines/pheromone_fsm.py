"""
PheromoneDeposit FSM (Phase 4.5)
=================================

Conceptual lifecycle of a :class:`hoc.nectar.PheromoneDeposit`:

::

    FRESH ──┐
            │
            ▼
        DECAYING ◀──┐
            │       │
        ┌───┴──┐    │
        ▼      ▼    │
    DIFFUSING ─┘    │
        │           │
        └────► EVAPORATED  (terminal)

Why declarative-only
--------------------

Unlike the ``CellState`` FSM (4.3), this FSM is **declarative-only**: the
spec is captured here for documentation (``docs/state-machines.md``) and
property-based testing of the state graph, but the production code in
``nectar.py`` does **not** route deposit transitions through it.

Reason: ``PheromoneDeposit`` instances are extremely numerous (production
trails carry up to ``DEFAULT_MAX_COORDS = 10_000`` coordinates, each with
up to 9 deposits — ~90k objects). Allocating one ``HocStateMachine`` per
deposit, or holding a global mutex around a shared validator FSM, would
exceed the Phase 4 ``<5 %`` benchmark budget by orders of magnitude. A
deposit's lifecycle is also implicit: there is no ``state`` field on
``PheromoneDeposit`` — the phase is derivable from ``intensity`` and
``time.time() - timestamp`` alone, and ``PheromoneTrail.evaporate`` reads
those scalar fields directly.

Writing the FSM here still pays:

- The Mermaid export documents the lifecycle for new contributors.
- Property tests validate the state graph (e.g. that EVAPORATED is
  terminal, that no transition skips DECAYING out of FRESH directly).
- If a future phase profiles the cleanup hot path and decides the
  observability of explicit FSM transitions is worth the cost, the
  transitions are already specified here — wire-up becomes mechanical.

The Phase 4 closure documents this trade-off as a deliberate gap rather
than a missing piece.

Lifecycle phases (definitions)
------------------------------

- **FRESH**: deposit was created (or refreshed) within
  ``DEFAULT_FRESHNESS_WINDOW`` seconds. Intensity is at or near maximum.
- **DECAYING**: age exceeds the freshness window; intensity is dropping
  per the configured ``PheromoneDecay`` strategy.
- **DIFFUSING**: a special transient phase entered when
  ``HoneycombCell.diffuse_pheromones`` spreads a fraction of the deposit
  to neighbours. The original deposit is still in DECAYING after the
  spread completes — DIFFUSING is observed only during the operation.
- **EVAPORATED**: terminal. Intensity dropped below
  :data:`hoc.nectar.PheromoneTrail.CLEANUP_THRESHOLD` and the deposit is
  scheduled for removal from the trail's ``OrderedDict``.
"""

from __future__ import annotations

from .base import HocStateMachine, HocTransition

PHEROMONE_FRESH = "FRESH"
PHEROMONE_DECAYING = "DECAYING"
PHEROMONE_DIFFUSING = "DIFFUSING"
PHEROMONE_EVAPORATED = "EVAPORATED"

ALL_PHEROMONE_STATES: tuple[str, ...] = (
    PHEROMONE_FRESH,
    PHEROMONE_DECAYING,
    PHEROMONE_DIFFUSING,
    PHEROMONE_EVAPORATED,
)

# Freshness window in seconds. Matches the conceptual "fresh deposit"
# definition the brief uses; not enforced by current code (no state
# field on PheromoneDeposit), but documented here as the boundary
# between FRESH and DECAYING for property tests.
DEFAULT_FRESHNESS_WINDOW: float = 5.0


def build_pheromone_fsm() -> HocStateMachine:
    """
    Build the conceptual PheromoneDeposit lifecycle FSM. Used by
    :mod:`scripts.generate_state_machines_md` and the property tests.
    Production code does not call ``transition_to`` on this FSM — see the
    module docstring for why.
    """
    transitions: list[HocTransition] = [
        # FRESH age out -> DECAYING. The only legal exit from FRESH; the
        # phase boundary is `age > DEFAULT_FRESHNESS_WINDOW`.
        HocTransition(
            PHEROMONE_FRESH,
            PHEROMONE_DECAYING,
            trigger="aged_out",
            guard=lambda ctx: bool(
                ctx.get("age", 0.0) > ctx.get("freshness_window", DEFAULT_FRESHNESS_WINDOW)
            ),
        ),
        # DECAYING -> DIFFUSING when the cell spreads to neighbours
        # (HoneycombCell.diffuse_pheromones). Two guards: intensity above
        # the diffuse threshold, and at least one reachable neighbour.
        HocTransition(
            PHEROMONE_DECAYING,
            PHEROMONE_DIFFUSING,
            trigger="diffusion_started",
            guard=lambda ctx: bool(
                ctx.get("intensity", 0.0) > ctx.get("diffuse_threshold", 0.01)
                and ctx.get("neighbors_reachable", 0) > 0
            ),
        ),
        # DIFFUSING -> DECAYING after the diffusion call returns. Intensity
        # has been split with neighbours; the original goes back to plain
        # decay.
        HocTransition(
            PHEROMONE_DIFFUSING,
            PHEROMONE_DECAYING,
            trigger="diffusion_completed",
        ),
        # DECAYING -> EVAPORATED when intensity falls below the cleanup
        # threshold. Terminal state.
        HocTransition(
            PHEROMONE_DECAYING,
            PHEROMONE_EVAPORATED,
            trigger="intensity_below_cleanup",
            guard=lambda ctx: bool(ctx.get("intensity", 1.0) < ctx.get("cleanup_threshold", 0.001)),
        ),
        # DIFFUSING -> EVAPORATED if the diffusion itself drains the
        # deposit below the cleanup threshold (a rare but allowed shortcut).
        HocTransition(
            PHEROMONE_DIFFUSING,
            PHEROMONE_EVAPORATED,
            trigger="intensity_below_cleanup_during_diffuse",
            guard=lambda ctx: bool(ctx.get("intensity", 1.0) < ctx.get("cleanup_threshold", 0.001)),
        ),
    ]

    return HocStateMachine(
        name="PheromoneDeposit",
        states=list(ALL_PHEROMONE_STATES),
        transitions=transitions,
        initial=PHEROMONE_FRESH,
        # No history needed — declarative-only FSM, no per-instance
        # storage of past transitions.
        history_size=0,
    )

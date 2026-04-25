"""
TaskLifecycle FSM (Phase 4.4a)
==============================

Lifecycle of a :class:`hoc.swarm.HiveTask`:

::

    PENDING ─claim──► RUNNING ─success──► COMPLETED  (terminal)
       │                │
       │                ├─exception──────► FAILED ──retry──┐
       │                │                                  │
       └─shutdown───────┴──► CANCELLED  (terminal)         │
                                                           │
       ◄───────────────────────────────────────────────────┘

Why declarative-only
--------------------

Same pattern as :mod:`hoc.state_machines.pheromone_fsm`. ``HiveTask`` is a
``@dataclass(order=True)`` whose ``state`` field is mutated directly from
~15 call-sites in :mod:`hoc.swarm` and many more in
``tests/test_swarm.py`` and ``tests/test_heavy.py``. Wiring the FSM in
through ``__setattr__`` would force every test that injects task state
for fault simulation through the FSM, requiring either:

- relaxing the FSM with wildcard admin transitions to every state (which
  drains the FSM of validation value), or
- rewriting tests to use a ``transition_state`` helper (which conflates
  Phase 4 scope with refactoring tests authored in Phase 1-3).

Either path delivers less value than a declarative FSM does:
``docs/state-machines.md`` documents the lifecycle for new contributors,
``tests/test_state_machines.py`` validates the graph (e.g. terminal-state
properties), and the trigger names map cleanly onto the call-sites in
swarm.py for future wire-up. Phase 4 closure documents this explicitly.

ASSIGNED is dead state (B12)
----------------------------

The :class:`hoc.swarm.TaskState` enum declares ``ASSIGNED`` (value 2)
between ``PENDING`` and ``RUNNING``. The brief proposes
``PENDING → ASSIGNED → RUNNING`` as the happy path. **No production
call-site or test ever writes ``task.state = TaskState.ASSIGNED``.**
Workers go straight ``PENDING → RUNNING`` when they claim a task in
``SwarmScheduler.execute_task``, ``SwarmScheduler.execute_with_failover``,
``SwarmScheduler.execute_with_circuit_breaker``, etc. (swarm.py:308, 382,
461, 531).

This FSM models the production flow: ``PENDING → RUNNING`` directly. The
``ASSIGNED`` state is **not** declared in this FSM. The Phase 4 closure
files this as **B12** with two options for resolution:

1. Remove ``ASSIGNED`` from the ``TaskState`` enum (the simpler fix —
   nothing depends on it).
2. Wire up the ``PENDING → ASSIGNED → RUNNING`` two-step in
   :class:`SwarmScheduler` (more accurately models the lock-acquire ←→
   begin-work boundary, but is a behavior change, out of scope for
   Phase 4).

Decision deferred to closure review with Raul.

Lifecycle reference
-------------------

- ``PENDING``: created and queued. ``HiveTask.__init__`` default.
- ``RUNNING``: worker has the task and is executing
  (``SwarmScheduler.execute_task`` etc.).
- ``COMPLETED``: terminal. Successful execution. ``callback`` (if any)
  fires.
- ``FAILED``: terminal *unless retry path triggers*. Exception caught
  during execution, or task expired (``HiveTask.is_expired``). May
  transition back to ``PENDING`` if ``can_retry()`` is true (swarm.py:1072).
- ``CANCELLED``: terminal. Set on shutdown for tasks still ``PENDING``
  (swarm.py:1010, 1127).
"""

from __future__ import annotations

from .base import HocStateMachine, HocTransition

TASK_PENDING = "PENDING"
TASK_RUNNING = "RUNNING"
TASK_COMPLETED = "COMPLETED"
TASK_FAILED = "FAILED"
TASK_CANCELLED = "CANCELLED"

# ASSIGNED is intentionally absent — see "ASSIGNED is dead state" in the
# module docstring. ``TaskState.ASSIGNED`` exists in the enum but no
# production code transitions to it. B12 in Phase 4 closure.

ALL_TASK_STATES: tuple[str, ...] = (
    TASK_PENDING,
    TASK_RUNNING,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_CANCELLED,
)

TERMINAL_TASK_STATES: frozenset[str] = frozenset({TASK_COMPLETED, TASK_CANCELLED})
"""States with no outgoing transitions in the happy lifecycle. ``FAILED``
is **not** terminal because the retry path can transition back to
``PENDING`` (swarm.py:1072)."""


def build_task_fsm() -> HocStateMachine:
    """
    Build the TaskLifecycle FSM. Used by
    :mod:`scripts.generate_state_machines_md` and the property tests.
    Production code in ``swarm.py`` does not (yet) call ``transition_to``
    on this FSM — see the module docstring for why.
    """
    transitions: list[HocTransition] = [
        # ── Lifecycle ──────────────────────────────────────────────────
        # SwarmScheduler.execute_task / execute_with_failover /
        # execute_with_circuit_breaker / execute_with_retry — worker
        # claims a PENDING task and immediately runs it. swarm.py:308,
        # 382, 461, 531.
        HocTransition(TASK_PENDING, TASK_RUNNING, trigger="claimed"),
        # Successful tick. swarm.py:318, 394, 402, 468, 538.
        HocTransition(TASK_RUNNING, TASK_COMPLETED, trigger="completed"),
        # Exception during execution OR is_expired() returned True.
        # swarm.py:335, 408, 483, 552.
        HocTransition(TASK_RUNNING, TASK_FAILED, trigger="failed"),
        # Retry path. SwarmScheduler.execute_with_retry returns the task
        # to the queue if can_retry(). swarm.py:1072.
        HocTransition(TASK_FAILED, TASK_PENDING, trigger="retry"),
        # Shutdown / explicit cancel before pickup. swarm.py:1009-1010,
        # 1126-1127.
        HocTransition(TASK_PENDING, TASK_CANCELLED, trigger="cancelled_pending"),
        # Test fixtures and force-completion paths can mark a RUNNING
        # task CANCELLED if shutdown beat the worker. test_swarm.py:444,
        # 451 (force-completed path), 566 (CANCELLED race).
        HocTransition(TASK_RUNNING, TASK_CANCELLED, trigger="cancelled_running"),
    ]

    return HocStateMachine(
        name="TaskLifecycle",
        states=list(ALL_TASK_STATES),
        transitions=transitions,
        initial=TASK_PENDING,
        history_size=4,
    )

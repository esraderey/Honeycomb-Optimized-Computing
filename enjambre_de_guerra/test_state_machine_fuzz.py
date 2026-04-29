"""Stress: hypothesis fuzz de transiciones FSM.

Hipótesis bajo prueba:
- Para CUALQUIER secuencia de transitions legítimas en el FSM
  TaskLifecycle, el state final es alcanzable + history bounded.
- IllegalStateTransition se levanta deterministicamente cuando se
  intenta una edge inexistente (no hay corruption silenciosa).
- ``_state_history`` deque respeta su maxlen incluso bajo bombardeo
  de transitions.
- HoneycombCell.fsm.transition_to es atomic — un raise deja state
  intacto.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from hoc.core import CellState, HoneycombConfig, HoneycombGrid, WorkerCell
from hoc.swarm import HiveTask, TaskState
from state_machines.base import IllegalStateTransition

pytestmark = pytest.mark.stress


# Edges legítimos del TaskLifecycle FSM (ver state_machines/task_fsm.py).
LEGAL_TASK_TRANSITIONS = {
    TaskState.PENDING: [
        TaskState.RUNNING,  # claim
        TaskState.CANCELLED,  # shutdown
        TaskState.COMPLETED,  # force_completed_from_pending (test fixture)
        TaskState.FAILED,  # force_failed_from_pending (test fixture)
    ],
    TaskState.RUNNING: [
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    ],
    TaskState.FAILED: [TaskState.PENDING],  # retry
    # COMPLETED y CANCELLED son terminales.
    TaskState.COMPLETED: [],
    TaskState.CANCELLED: [],
}


def _legal_random_walk(seed: int, steps: int) -> tuple[HiveTask, list[TaskState]]:
    """Walk the FSM via legal transitions only. Returns the task at
    its final state + the path of states it visited."""
    import random

    rng = random.Random(seed)
    task = HiveTask(priority=2, task_type="compute")
    path = [task.state]
    for _ in range(steps):
        opts = LEGAL_TASK_TRANSITIONS[task.state]
        if not opts:
            break  # terminal
        next_state = rng.choice(opts)
        task.state = next_state
        path.append(next_state)
    return task, path


class TestFSMFuzz:
    @given(
        seed=st.integers(min_value=0, max_value=10_000),
        steps=st.integers(min_value=1, max_value=20),
    )
    @settings(
        max_examples=200,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    def test_random_legal_walks_never_corrupt(self, seed: int, steps: int):
        """Random walks por edges legítimos siempre terminan en un
        estado coherente, sin excepción."""
        task, path = _legal_random_walk(seed, steps)
        # Final state matches the FSM's view.
        assert task.state.name == path[-1].name
        # FSM internal state agrees.
        assert task._fsm.state == task.state.name

    @given(
        seed=st.integers(min_value=0, max_value=10_000),
        n_attempts=st.integers(min_value=10, max_value=100),
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    def test_illegal_transitions_always_raise(self, seed: int, n_attempts: int):
        """Para CUALQUIER transition que no esté en
        LEGAL_TASK_TRANSITIONS desde el estado actual, se levanta
        IllegalStateTransition. Repetimos varias veces; ningún silent-
        success."""
        import random

        rng = random.Random(seed)
        task = HiveTask(priority=2, task_type="compute")

        for _ in range(n_attempts):
            current = task.state
            # All other states.
            illegal_targets = [
                s for s in TaskState if s not in LEGAL_TASK_TRANSITIONS[current] and s != current
            ]
            if not illegal_targets:
                break
            target = rng.choice(illegal_targets)
            with pytest.raises(IllegalStateTransition):
                task.state = target
            # Estado pre-tentativa intacto.
            assert task.state == current

    def test_history_deque_respects_maxlen(self):
        """500 cell transitions; history deque debe estar bounded en
        ``HoneycombCell._HISTORY_MAXLEN`` (8)."""
        grid = HoneycombGrid(HoneycombConfig(radius=1))
        cell = next(c for c in grid._cells.values() if isinstance(c, WorkerCell))

        # Drive cell por una secuencia legal repetida: IDLE → ACTIVE → IDLE.
        for _ in range(250):
            cell.state = CellState.IDLE
            cell.state = CellState.ACTIVE

        history = list(cell._state_history)
        # Bounded.
        assert len(history) <= 8
        # Y los últimos elementos son los más recientes.
        assert history[-1] in ("IDLE", "ACTIVE")

    def test_atomic_transition_failure_leaves_state_intact(self):
        """Intento de transition ilegal: ni el state ni el history
        cambian. Atomicity contract."""
        task = HiveTask(priority=2, task_type="compute")
        # Drive a COMPLETED (terminal).
        task.state = TaskState.RUNNING
        task.state = TaskState.COMPLETED

        state_before = task.state
        history_before = list(task._fsm.history) if hasattr(task._fsm, "history") else []

        # Intento ilegal: COMPLETED → RUNNING (no hay edge).
        with pytest.raises(IllegalStateTransition):
            task.state = TaskState.RUNNING

        assert task.state == state_before
        # History unchanged (depends on FSM impl; sanity check).
        history_after = list(task._fsm.history) if hasattr(task._fsm, "history") else []
        assert history_after == history_before

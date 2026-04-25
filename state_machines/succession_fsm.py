"""
QueenSuccession FSM (Phase 4.4b)
================================

Conceptual phases of a queen-succession election in
:class:`hoc.resilience.QueenSuccession`:

::

    STABLE ─heartbeat_lost─► DETECTING ─confirmed─► NOMINATING
                                  ▲                     │
                                  │                     ▼
                                  └─stabilized──   VOTING
                                          │            │
                                          │   ┌────────┴────────┐
                                          │   │                 │
                                          │   ▼                 ▼
                                       FAILED            ELECTED
                                          │   ◄─cooldown─       │
                                          └─────────────────────┘
                                                                │
                                                                ▼
                                                            STABLE  (after promote)

Why declarative-only
--------------------

Same trade-off as :mod:`hoc.state_machines.task_fsm` and
:mod:`hoc.state_machines.pheromone_fsm`. ``QueenSuccession`` does not
maintain a multi-phase state field today — it tracks a single boolean
``_election_in_progress`` plus a monotonic ``_term_number``. The
phases below are conceptual: they map to *positions inside*
:meth:`hoc.resilience.QueenSuccession.elect_new_queen` and
:meth:`_conduct_election` rather than to first-class states stored
between calls.

Wiring the FSM in would require:

1. Adding a ``_phase`` field to ``QueenSuccession`` that mutates
   transactionally with the existing flag.
2. Splitting ``elect_new_queen`` into 4-5 methods that the FSM hooks can
   target individually.
3. Re-running the **7 quorum signed-vote tests** in
   ``tests/test_security.py::TestQuorumSignedVotes`` to confirm the
   refactor does not regress B4 / Phase 2 hardening.

Worth doing eventually — the FSM hooks would naturally split tally vs.
nomination logic — but doing it inside Phase 4 conflates an FSM-modeling
phase with a security-critical refactor. The declarative FSM here:

- Documents the elections phases for new contributors.
- Is exported as Mermaid in ``docs/state-machines.md``.
- Property tests confirm graph structure (terminal states, retry path,
  ELECTED reachable only via VOTING).

The Phase 4 closure flags the wire-up as a deliberate gap.

Election security guards (mapped from existing tally code)
----------------------------------------------------------

The brief specified several guards on the ``VOTING → ELECTED`` edge.
These all map to the existing ``_tally_votes`` rejections (resilience.py
~590-672), which Phase 1 (B4) and Phase 2 (signed votes + term)
hardened. The FSM's guard re-states them so the diagram is honest:

- ``quorum_reached``: a strict majority of *valid* votes name a single
  candidate (``tallies[winner] >= total // 2 + 1``).
- ``signatures_valid``: every counted vote passed
  :meth:`hoc.resilience.Vote.verify` (HMAC-SHA256 over canonical bytes).
- ``term_matches``: ``vote.term == expected_term`` for every counted
  vote — anti-replay across elections.
"""

from __future__ import annotations

from .base import HocStateMachine, HocTransition

SUCCESSION_STABLE = "STABLE"
SUCCESSION_DETECTING = "DETECTING"
SUCCESSION_NOMINATING = "NOMINATING"
SUCCESSION_VOTING = "VOTING"
SUCCESSION_ELECTED = "ELECTED"
SUCCESSION_FAILED = "FAILED"

ALL_SUCCESSION_STATES: tuple[str, ...] = (
    SUCCESSION_STABLE,
    SUCCESSION_DETECTING,
    SUCCESSION_NOMINATING,
    SUCCESSION_VOTING,
    SUCCESSION_ELECTED,
    SUCCESSION_FAILED,
)


def build_succession_fsm() -> HocStateMachine:
    """Build the QueenSuccession FSM. Declarative-only — see module docstring."""
    transitions: list[HocTransition] = [
        # Heartbeat timeout opens the detection window. The HiveResilience
        # heartbeat loop calls check_queen_health() and sees `elapsed >
        # queen_heartbeat_interval * 2`. resilience.py ~485-497.
        HocTransition(
            SUCCESSION_STABLE,
            SUCCESSION_DETECTING,
            trigger="heartbeat_lost",
            guard=lambda ctx: bool(
                ctx.get("elapsed_since_heartbeat", 0.0) > ctx.get("timeout_threshold", 0.0)
            ),
        ),
        # Confirmation: missed heartbeats persisted past the confirm
        # window — start nominating. Currently the code commits to the
        # election after a single timeout; the FSM allows for the
        # multi-tick confirm window the brief describes.
        HocTransition(
            SUCCESSION_DETECTING,
            SUCCESSION_NOMINATING,
            trigger="failure_confirmed",
            guard=lambda ctx: bool(ctx.get("missed_heartbeats", 0) >= ctx.get("confirm_ticks", 1)),
        ),
        # False alarm: queen recovered before confirm window.
        HocTransition(SUCCESSION_DETECTING, SUCCESSION_STABLE, trigger="false_alarm"),
        # _identify_candidates returned a non-empty set — proceed to vote.
        HocTransition(
            SUCCESSION_NOMINATING,
            SUCCESSION_VOTING,
            trigger="candidates_identified",
            guard=lambda ctx: bool(ctx.get("candidate_count", 0) > 0),
        ),
        # No candidates — election aborts; HiveResilience may retry after
        # cooldown.
        HocTransition(SUCCESSION_NOMINATING, SUCCESSION_FAILED, trigger="no_candidates"),
        # Successful tally: quorum + valid signatures + term match.
        # _tally_votes returns a winner (resilience.py:672).
        HocTransition(
            SUCCESSION_VOTING,
            SUCCESSION_ELECTED,
            trigger="quorum_reached",
            guard=lambda ctx: bool(
                ctx.get("quorum_reached", False)
                and ctx.get("signatures_valid", False)
                and ctx.get("term_matches", False)
            ),
        ),
        # Tally rejected: insufficient votes, bad signatures, term
        # mismatch (resilience.py:651-671).
        HocTransition(SUCCESSION_VOTING, SUCCESSION_FAILED, trigger="tally_rejected"),
        # Promote completes: new queen takes over. resilience.py:697-727.
        HocTransition(SUCCESSION_ELECTED, SUCCESSION_STABLE, trigger="queen_promoted"),
        # Cooldown: failed election can be retried after a wait period.
        HocTransition(SUCCESSION_FAILED, SUCCESSION_STABLE, trigger="cooldown_expired"),
    ]

    return HocStateMachine(
        name="QueenSuccession",
        states=list(ALL_SUCCESSION_STATES),
        transitions=transitions,
        initial=SUCCESSION_STABLE,
        history_size=16,
    )

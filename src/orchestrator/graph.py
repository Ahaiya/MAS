"""
State Graph — runtime state machine for the evaluation pipeline.

StateGraph enforces the legal transition matrix defined in states.py.
It tracks the current state, records transition history, and provides
methods for the orchestrator to advance, query, and force-fail the run.

Design invariants:
- Only transitions present in TRANSITIONS are allowed via advance().
- force_fail() is the only way to reach FAILED — it bypasses TRANSITIONS
  to handle unrecoverable errors from any state.
- No business logic lives here. The orchestrator calls advance() with
  the target state determined by router.py.
- history records the (from, to) state pair for every transition,
  providing an ordered audit trail for RunTrace.
"""

from __future__ import annotations

from typing import List, Set, Tuple

from src.orchestrator.states import PipelineState, TRANSITIONS, TERMINAL_STATES


class IllegalTransitionError(Exception):
    """Raised when an illegal state transition is attempted."""

    def __init__(self, current: PipelineState, target: PipelineState) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Illegal transition: {current.value} -> {target.value}. "
            f"Allowed targets from {current.value}: "
            f"{sorted(s.value for s in TRANSITIONS.get(current, set()))}"
        )


class StateGraph:
    """Runtime state machine tracking current state and transition history.

    Usage by the orchestrator:
        graph = StateGraph()
        graph.advance(PipelineState.CONFIG_RESOLVED)
        ...
        if graph.is_terminal():
            # run complete

    The orchestrator determines the next state using router functions,
    then calls graph.advance(next_state). This class only enforces
    that the transition is legal.
    """

    def __init__(self) -> None:
        self._current: PipelineState = PipelineState.INIT
        self._history: List[Tuple[PipelineState, PipelineState]] = []

    @property
    def current_state(self) -> PipelineState:
        """The current pipeline state."""
        return self._current

    @property
    def history(self) -> List[Tuple[PipelineState, PipelineState]]:
        """Ordered list of (from_state, to_state) transitions taken."""
        return list(self._history)

    def advance(self, target: PipelineState) -> None:
        """Advance the state machine to the target state.

        Raises IllegalTransitionError if the transition is not in TRANSITIONS.
        """
        allowed = TRANSITIONS.get(self._current, set())
        if target not in allowed:
            raise IllegalTransitionError(self._current, target)
        prev = self._current
        self._current = target
        self._history.append((prev, target))

    def force_fail(self) -> None:
        """Force the state machine into FAILED from any state.

        This is the only way to reach FAILED. It is used by the orchestrator
        when an unrecoverable error occurs (e.g., config resolution failure,
        repeated fallback exhaustion).
        """
        prev = self._current
        self._current = PipelineState.FAILED
        self._history.append((prev, PipelineState.FAILED))

    def is_terminal(self) -> bool:
        """True if the current state is a terminal state."""
        return self._current in TERMINAL_STATES

    def available_transitions(self) -> Set[PipelineState]:
        """Return the set of states reachable from the current state."""
        return set(TRANSITIONS.get(self._current, set()))

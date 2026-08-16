from __future__ import annotations

from dataclasses import dataclass


PLAN_KINDS = frozenset({"promise", "reminder", "shared_plan"})
PLAN_STATES = frozenset(
    {"proposed", "confirmed", "due", "completed", "cancelled", "missed"}
)
TERMINAL_PLAN_STATES = frozenset({"completed", "cancelled", "missed"})
ACTIVE_PLAN_STATES = frozenset({"proposed", "confirmed", "due"})

_INITIAL_STATES = frozenset({"proposed", "confirmed"})
_TRANSITIONS = {
    "proposed": frozenset({"confirmed", "cancelled"}),
    "confirmed": frozenset({"due", "completed", "cancelled", "missed"}),
    "due": frozenset({"completed", "cancelled", "missed"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
    "missed": frozenset(),
}


class PlanStateError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PlanRecord:
    plan_id: str
    conversation_id: str
    kind: str
    description: str
    due_at: str | None
    timezone: str
    state: str
    created_from_event_id: str
    updated_from_event_id: str
    last_transition_at: str
    version: int


def require_initial_state(state: str) -> None:
    if state not in _INITIAL_STATES:
        raise PlanStateError("new plan must be proposed or confirmed")


def require_transition(from_state: str, to_state: str) -> None:
    if from_state not in PLAN_STATES or to_state not in PLAN_STATES:
        raise PlanStateError("unknown plan state")
    if to_state not in _TRANSITIONS[from_state]:
        raise PlanStateError(f"invalid plan transition: {from_state} -> {to_state}")


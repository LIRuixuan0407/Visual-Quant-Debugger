from __future__ import annotations

from app.paper.models import PaperSessionStatus

_ALLOWED_TRANSITIONS: dict[PaperSessionStatus, frozenset[PaperSessionStatus]] = {
    "CREATED": frozenset({"RUNNING", "STOPPED"}),
    "RUNNING": frozenset({"PAUSED", "STOPPED", "ERROR"}),
    "PAUSED": frozenset({"RUNNING", "STOPPED", "ERROR"}),
    "STOPPED": frozenset(),
    "ERROR": frozenset({"STOPPED"}),
}


def validate_paper_transition(current: PaperSessionStatus, target: PaperSessionStatus) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"Cannot transition a {current} session to {target}")


def recovery_target_status(current: PaperSessionStatus, *, explicit: bool) -> PaperSessionStatus:
    if explicit and current == "ERROR":
        return "PAUSED"
    return current

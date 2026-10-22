from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Status = Literal[
    "discovered",
    "screened",
    "preparing",
    "ready",
    "awaiting_approval",
    "submitted",
    "screening",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "closed",
]
STATUSES: tuple[Status, ...] = (
    "discovered",
    "screened",
    "preparing",
    "ready",
    "awaiting_approval",
    "submitted",
    "screening",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "closed",
)
WaitingOn = Literal["agent", "you", "site"]

_NEXT: dict[str, tuple[str, ...]] = {
    "discovered": ("screened", "preparing", "closed"),
    "screened": ("preparing", "closed"),
    "preparing": ("ready", "closed"),
    "ready": ("awaiting_approval", "preparing", "closed"),
    "awaiting_approval": ("submitted", "preparing", "closed"),
    "submitted": ("screening", "interview", "offer", "rejected", "withdrawn", "closed"),
    "screening": ("interview", "offer", "rejected", "withdrawn", "closed"),
    "interview": ("offer", "rejected", "withdrawn", "closed"),
    "offer": ("closed", "withdrawn"),
    "rejected": ("closed",),
    "withdrawn": ("closed",),
    "closed": (),
}
PRE_SUBMIT_ORDER = ("discovered", "screened", "preparing", "ready", "awaiting_approval")
# Statuses in which the agent can still be waiting for the owner's answers; from awaiting_approval on, no question is open.
QUESTION_STATES = PRE_SUBMIT_ORDER[:-1]


@dataclass(frozen=True)
class GateContext:
    """Facts the caller supplies; the machine never reads the outside world."""

    form_fully_mapped: bool = False
    cv_checks_passed: bool = False
    attachment_verified: bool = False
    approval_for_draft_hash: str | None = None
    current_draft_hash: str | None = None
    confirmation: Literal["live-verified", "user-confirmed", "agent-reported", "unapproved-reported"] | None = None
    agent_reported_draft: bool = False
    unapproved_submit_reported: bool = False


@dataclass(frozen=True)
class Result:
    ok: bool
    reason: str = ""


def can_transition(frm: str, to: str, g: GateContext | None = None) -> Result:
    g = g or GateContext()
    if to not in _NEXT.get(frm, ()):
        return Result(False, f"illegal transition {frm} -> {to}")
    if to == "ready" and g.confirmation != "user-confirmed" and not g.agent_reported_draft:
        if not g.form_fully_mapped:
            return Result(False, "form not fully mapped")
        if not g.cv_checks_passed:
            return Result(False, "CV checks have not passed")
        if not g.attachment_verified:
            return Result(False, "attachment not verified")
    if to == "submitted" and not (g.unapproved_submit_reported and g.confirmation == "unapproved-reported"):
        # Report-only exception prevents a second application; it never authorizes a browser click.
        if not g.approval_for_draft_hash:
            return Result(False, "no server-written approval")
        if g.approval_for_draft_hash != g.current_draft_hash:
            return Result(False, "draft changed since approval")
        if not g.confirmation:
            return Result(False, "no confirmation evidence")
    return Result(True)

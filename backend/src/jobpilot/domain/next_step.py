from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Step = Literal[
    "capture", "inventory", "plan", "cv", "fill", "next_page", "verify", "await_approval", "confirm", "idle", "blocked"
]

_DONE = {"submitted", "screening", "interview", "offer", "rejected", "withdrawn", "closed"}


@dataclass(frozen=True)
class JobFacts:
    status: str
    waiting_on: str
    meta_captured: bool
    has_inventory: bool
    has_plan: bool
    missing_answers: int
    cv_attached: bool
    fill_done: bool
    verified: bool
    submit_clicked: bool
    automation: str  # auto | assisted | manual
    pages_complete: bool = True
    external: bool = False  # owner applied outside the platform (manual/imported): only read the posting details
    external_complete: bool = True  # all required tracker fields present (company, role, location, description)
    agent_reported: bool = False


def next_step(f: JobFacts) -> Step:
    """Pure: the single next step for a job. Idempotent; derived from state, not a stored cursor."""
    if f.external:
        if f.external_complete:
            return "idle"
        return "blocked" if f.waiting_on == "you" else "capture"
    if f.status in _DONE:
        return "idle"
    if f.waiting_on == "you":
        return "blocked"
    if f.status == "awaiting_approval" and f.submit_clicked:
        return "confirm"
    if f.agent_reported and f.verified and f.fill_done and f.pages_complete and not f.missing_answers:
        if f.status != "awaiting_approval":
            return "await_approval"
        return "blocked"  # the owner submits in Neo (ADR 0026)
    if not f.meta_captured:
        return "capture"
    if f.automation == "manual":
        return "blocked"
    if not f.has_inventory:
        return "inventory"
    if not f.has_plan:
        return "plan"
    if f.missing_answers > 0:
        return "plan"
    if not f.cv_attached:
        return "cv"
    if not f.fill_done:
        return "fill"
    if not f.pages_complete:
        return "next_page"
    if not f.verified:
        return "verify"
    if f.status != "awaiting_approval":
        return "await_approval"
    return "blocked"  # the owner reviews and submits in Neo (ADR 0026)

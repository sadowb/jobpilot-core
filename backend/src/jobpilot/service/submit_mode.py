"""Which submit mode a run gets (ADR 0026, amended): the owner's global toggle, a per-job override, and the cases that
force a job back to review. The decision is taken once at run start and recorded; it is never re-read mid-run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .application import Services

Mode = Literal["review", "auto"]


@dataclass(frozen=True, slots=True)
class ModeDecision:
    mode: Mode  # what this run does
    requested: Mode  # what the override or the global toggle asked for
    override: Mode | None  # the per-job override, if any
    reason: str | None  # plain-language reason review was used although auto (or the override) would apply


class SubmitModeService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc

    def decide(self, job_id: str) -> ModeDecision:
        job = self.svc.job(job_id)
        override = job.get("submit_mode")
        override_mode: Mode | None = override if override in ("review", "auto") else None
        requested: Mode = override_mode or self.svc.runtime_settings().submitMode
        if requested == "review":
            reason = "This job is set to review." if override_mode == "review" else None
            return ModeDecision("review", requested, override_mode, reason)
        reason = self._forced_review(job_id, job)
        return ModeDecision("review" if reason else "auto", requested, override_mode, reason)

    def _forced_review(self, job_id: str, job: dict[str, object]) -> str | None:
        if self.svc.board_policy(str(job["board_id"]) if job.get("board_id") else None) != "auto":
            return "This board is not set to automatic submission."
        if job.get("try_questions_anyway"):
            return "You chose to continue without answering some required questions."
        if self.svc.jobs.repository.find_job_events_for_kind(job_id, "cv_select_fallback"):
            return "The CV was chosen without reading the posting."
        if self.svc.jobs.repository.find_job_events_for_kind(job_id, "email_verification_failed"):
            return "An email verification step failed earlier."
        return None

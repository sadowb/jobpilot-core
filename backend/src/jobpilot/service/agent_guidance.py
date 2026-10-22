"""Owner guidance and safe progress summaries; never store raw model streams."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from ..domain.state_machine import PRE_SUBMIT_ORDER
from ..dto.direct import SECRET_VALUE
from ..repository.reporting import ReportingRepository

if TYPE_CHECKING:
    from .application import Services


class AgentGuidanceService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.repository = ReportingRepository(svc.db)
        self._progress_at: dict[str, float] = {}

    def latest(self, job_id: str) -> dict[str, Any] | None:
        before = None
        while True:
            rows = self.repository.event_feed(["agent_guidance"], 100, before)
            for row in rows:
                if row["jobId"] == job_id and row["actor"] == "user":
                    return row
            if len(rows) <= 100:
                return None
            before = int(rows[-1]["id"])

    def send(self, job_id: str, message: str) -> None:
        self.svc.jobs.repository.lock_job(job_id)
        self.svc._live(job_id)
        job = self.svc.job(job_id)
        if job["status"] not in PRE_SUBMIT_ORDER or self.svc.submit_clicked(job_id):
            raise PermissionError("Guidance is available before submission begins.")
        message = message.strip()
        if not message or len(message) > 2000 or "\x00" in message:
            raise ValueError("Enter guidance between 1 and 2000 characters.")
        if SECRET_VALUE.search(message):
            raise ValueError("Do not include passwords, tokens, cookies or verification codes.")
        self.svc.event(job_id, "agent_guidance", "user", {"message": message})
        if job["status"] == "awaiting_approval":  # parked as filled: the note sends it back to the agent
            self.svc.jobs.reopen_parked(job_id, reason="owner guidance")
        else:
            self.svc.resume(job_id)

    def progress(self, job_id: str, message: str) -> None:
        # Only server-authored summaries, never assistant reasoning or tool arguments.
        if message not in (
            "Starting the browser agent.",
            "Working on the application and checking the form.",
            "Attaching your CV.",
            "Filling application fields.",
            "Interacting with the application form.",
            "Reading the page and checking application fields.",
            "Comparing the job requirements with your CVs before uploading.",
            "Answering the application questions from your CV and profile.",
        ):
            raise ValueError("Unknown progress summary")
        now = time.monotonic()
        if now - self._progress_at.get(job_id, 0) < 15:
            return
        self._progress_at[job_id] = now
        self.svc.event(job_id, "agent_progress", "system", {"message": message})

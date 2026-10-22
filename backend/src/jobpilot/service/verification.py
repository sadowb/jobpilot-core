"""Emailed security code after the owner's own Submit click (ADR 0026, amended).

In review mode the agent never submits. But once the OWNER has clicked Submit in the parked tab, a site may ask for a code
it emailed. A short dedicated run (kind `verify_email_code`) may then read the code from Gmail and type it into the same
tab. The rules, enforced here and not by the model:

* only for a job parked at awaiting_approval with no recorded application;
* only after an owner submit click is on record: the watcher saw the code prompt (which exists only after the click) or
  the owner pressed "Get the email code from Gmail"; either way the `submit_clicked` fence is written, so every
  no-repeat rule applies from then on;
* one request per challenge (`verify_requested_at`); the watcher cannot ask twice, the owner's button may retry after the
  run finished;
* the run ends in a recorded submit (only when a confirmation page is seen) or in a Needs You card; never a second try.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from ..dto.direct import DirectResult, data_block
from ..repository.verification import VerificationRepository

if TYPE_CHECKING:
    from .application import Services

log = logging.getLogger("jobpilot.verification")

VERIFY_MARK = (
    "VERIFICATION RUN (verify_email_code): the OWNER already clicked Submit in the tab DATA target_id and the site now asks "
    "for an emailed code. Do only what is listed here. Never click anything except the button that confirms the code."
)
NOTE_CHARS = 400


class VerificationService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.repository = VerificationRepository(svc.db)

    def request(self, job_id: str, source: Literal["watcher", "owner"], label: str | None = None) -> bool:
        """Open the one-shot request and hand the job to the agent. False when a request is already open or used.
        Raises PermissionError when the job is not a parked, unsubmitted application."""
        with self.svc.db.transaction():
            self.repository.lock_job(job_id)
            job = self.svc._live(job_id)
            if job["status"] != "awaiting_approval":
                raise PermissionError("There is no parked application waiting for a code.")
            if self.svc.approvals.repository.find_applications_for_record_submitted(job_id):
                raise PermissionError("This application is already recorded as submitted.")
            if not self.repository.claim_request(job_id, retry=source == "owner"):
                return False
            if not self.svc.submit_clicked(job_id):  # the owner's click on record: the no-repeat fence from here on
                method = "challenge-observed" if source == "watcher" else "owner-requested"
                self.svc.event(job_id, "submit_clicked", "user", {"owner": True, "method": method})
            self.svc.event(
                job_id, "verification_challenge", "system" if source == "watcher" else "user", {"source": source, "label": label}
            )
            self.repository.queue_run(job_id)
        self.svc.request_dispatch()
        return True

    def pending(self, job_id: str) -> bool:
        """True while the agent may run on this parked job: a request is open, the owner's click is on record, nothing
        was submitted. The runner re-checks this at the start of the run."""
        job = self.svc.job(job_id)
        return (
            job["status"] == "awaiting_approval"
            and self.repository.find_open_request(job_id) is not None
            and self.svc.submit_clicked(job_id)
            and not self.svc.approvals.repository.find_applications_for_record_submitted(job_id)
        )

    def instruction(self, job_id: str, target_id: str) -> str:
        job = self.svc.job(job_id)
        data = {
            "task": "verify_email_code",
            "job_id": job_id,
            "target_id": target_id,
            "company": job.get("company_name"),
            "role": job.get("role"),
            "link": job.get("apply_url") or job.get("url"),
        }
        steps = (
            "1) Open https://mail.google.com in a NEW Neo tab (the owner is signed in). Search the newest mail from the "
            "employer or its ATS from the last ~15 minutes (`newer_than:1h`, company or ATS name; Greenhouse sends from "
            "no-reply@us.greenhouse-mail.io with 'security code' in the subject); check Spam too. "
            "2) Read the code. 3) Switch back to the application tab (DATA target_id) and type the code into the code field. "
            "4) Click ONLY the one button that confirms the code or completes the submission the owner started. One attempt: "
            "do not retry, do not re-enter, do not touch any other field. 5) Close only the Gmail tab you opened. "
            "Never print, return or log the code. Report status `submitted` with `confirmation` set to the confirmation text "
            'the page shows, and verification {"used": true, "source": "email"}; if there is no fresh mail, Gmail asks '
            "to sign in, the code is rejected or the page shows an error, report status `blocked` with a short code-free note. "
            "Keep `fields` and `questions` empty."
        )
        return VERIFY_MARK + "\n" + steps + "\n" + data_block(data) + "\n" + VERIFY_MARK

    def finish(self, job_id: str, result: DirectResult | None, note: str | None = None) -> str:
        """Record a verification run's terminal outcome. `result` None: the report was unusable (`note` says why)."""
        from .evidence import SubmitEvidenceService  # evidence imports Services; keep the cycle lazy

        seen = False
        if result is not None and result.status == "submitted" and self.svc.job(job_id)["status"] == "awaiting_approval":
            seen = SubmitEvidenceService(self.svc).verify(job_id, polls=3)  # read-only; the report alone is not trusted
        with self.svc.db.transaction():
            self.repository.lock_job(job_id)
            self.repository.update_done(job_id)
            job = self.svc.job(job_id)
            if job["status"] != "awaiting_approval":
                return "done"  # the watcher or the owner recorded the submit while the agent worked
            if result is not None and result.verification is not None:
                self.svc.event(job_id, "email_verification_used", "system", result.verification.model_dump())
            if seen:
                self.svc.approvals.record_owner_submit(job_id, True, "email-code")
                return "done"
            if result is not None and result.status == "submitted":
                reason = (
                    "the agent entered the code but no confirmation page appeared: check the tab, then press 'I submitted it'"
                )
            else:
                detail = " ".join(((result.note if result else note) or "").split())[:NOTE_CHARS]
                reason = "Paste the code yourself in the application tab" + (f". Agent note: {detail}" if detail else "")
            self.svc.event(job_id, "verification_blocked", "system", {})
            self.svc.wait(job_id, "you", reason)
            return "blocked"

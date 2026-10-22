"""Approvals operations behind the shared Services facade."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from ..domain.state_machine import PRE_SUBMIT_ORDER, GateContext
from ..repository.approvals import ApprovalsRepository

if TYPE_CHECKING:
    from .application import Services


class ApprovalsService:
    def __init__(self, application: Services) -> None:
        self.application = application
        self.repository = ApprovalsRepository(application.db)

    def _walk_to_pre_submit(self, job_id: str, context: GateContext, actor: str = "system") -> None:
        """Move a job through every pre-submit state it has not reached yet, so `submitted` is a legal next step."""
        for to in PRE_SUBMIT_ORDER[1:]:
            if PRE_SUBMIT_ORDER.index(self.application.job(job_id)["status"]) < PRE_SUBMIT_ORDER.index(to):
                self.application.transition(job_id, to, context, actor=actor)

    def _conclude(
        self, job_id: str, insert_application: Callable[[], object], *, confirmation: tuple[str, dict[str, Any]] | None = None
    ) -> None:
        """Shared write at the end of every submit path: the application row, the approval and job markers, and (when the
        path records one) the `confirmation` event. Caller holds the transaction and has already made the transition."""
        insert_application()
        self.repository.update_approvals_for_record_submitted(job_id)
        self.repository.update_jobs_for_record_submitted(job_id)
        if confirmation:
            actor, payload = confirmation
            self.application.event(job_id, "confirmation", actor, payload)

    def _publish(self, job_id: str, *, queue_cv: bool = True) -> None:
        if queue_cv:
            self.application.queue_cv_job(job_id, include_pdf=True)
        self.application.project(job_id)

    def submit_clicked(self, job_id: str) -> bool:
        return self.repository.find_job_events_for_submit_clicked(job_id) is not None

    def record_submitted(
        self, job_id: str, kind: Literal["live-verified", "user-confirmed"], text: str, *, user_session: bool = False
    ) -> None:
        self.repository.lock_job(job_id)
        f = self.application.form(job_id)
        j = self.application.job(job_id)
        if j["status"] not in PRE_SUBMIT_ORDER:
            if self.repository.find_applications_for_record_submitted(job_id):
                return
            raise PermissionError("job is no longer pre-submit")
        user = kind == "user-confirmed"
        if user and not user_session:
            raise PermissionError("user-confirmed requires a user-session request")
        if not user and not self.application.submit_clicked(job_id):
            raise PermissionError("no submit_clicked event")
        if user:
            self._walk_to_pre_submit(job_id, GateContext(confirmation="user-confirmed"), actor="user")
        approved = self.application.approval_for(job_id, f["draft_hash"] if f else None)
        approval = approved or ((f["draft_hash"] if f and f["draft_hash"] else "user") if user else None)
        current = (f["draft_hash"] if f and f["draft_hash"] else "user") if user else (f["draft_hash"] if f else None)
        self.application.transition(
            job_id,
            "submitted",
            GateContext(approval_for_draft_hash=approval, current_draft_hash=current, confirmation=kind),
            actor="user" if user else "system",
        )
        self._conclude(
            job_id, lambda: self.repository.insert_applications_for_record_submitted(job_id, j["cv_version_id"], kind, text[:500])
        )
        self._publish(job_id)

    def verification_gates(self, job_id: str) -> GateContext:
        form = self.application.form(job_id)
        checked = self.repository.find_job_events_for_verification_gates(job_id)
        payload = checked["payload"] if checked else {}
        results = {r["name"]: r["ok"] for r in payload.get("results", [])}
        verified = bool(
            form
            and form["pages_complete"]
            and form["verified_at"]
            and payload.get("ok")
            and payload.get("draftHash") == form["draft_hash"]
        )
        return GateContext(
            agent_reported_draft=bool(
                verified and form and not form["missing"] and payload.get("verification") == "agent-reported"
            ),
            form_fully_mapped=bool(verified and form and not form["missing"] and results and all(results.values())),
            cv_checks_passed=bool(verified and results.get("cv")),
            attachment_verified=bool(verified and results.get("attachment")),
        )

    def owner_submitted(self, job_id: str, *, user_session: bool, confirmation_seen: bool | None = None) -> bool:
        """The owner clicked the final Submit in the parked Neo tab and says so (ADR 0026). Returns False when the
        application was already recorded (idempotent). Caller holds the service transaction. The approval row only
        records which draft the owner reviewed; it never authorizes an agent click, because no agent submits."""
        if not user_session:
            raise PermissionError("owner-submitted requires a user-session request")
        return self.record_owner_submit(job_id, bool(confirmation_seen), "manual")

    def record_owner_submit(
        self, job_id: str, confirmation_seen: bool, method: Literal["manual", "auto-detected", "email-code"]
    ) -> bool:
        """Record a submit the OWNER made in the parked tab. `method` says how the server learned of it: the owner's
        button, or the parked-tab watcher that saw a confirmation page (no agent, no click; actor stays `user`).
        Internal callers only: the HTTP route goes through `owner_submitted`, which requires a user session."""
        self.repository.lock_job(job_id)
        if self.repository.find_applications_for_record_submitted(job_id):
            return False
        job = self.application.job(job_id)
        form = self.application.form(job_id)
        if job["status"] != "awaiting_approval" or not form or not form["draft_hash"]:
            raise PermissionError("There is no prepared application waiting for you to submit.")
        draft = str(form["draft_hash"])
        if not self.repository.find_unconsumed_submit_approval(job_id, draft):
            self.repository.insert_approvals_for_approve_submit(job_id, draft, "owner-submitted")
        self.application.event(job_id, "approval", "user", {"scope": "owner-submitted", "draftHash": draft, "method": method})
        if not self.application.submit_clicked(job_id):
            self.application.event(job_id, "submit_clicked", "user", {"owner": True, "method": method})
        kind: Literal["live-verified", "user-confirmed"] = "live-verified" if confirmation_seen else "user-confirmed"
        text = (
            "Owner submitted in Neo; a confirmation page was seen."
            if confirmation_seen
            else "Owner submitted in Neo; no confirmation page was seen."
        )
        if method == "auto-detected":
            text = "Owner submitted in Neo; JobPilot saw the confirmation page and recorded it."
        if method == "email-code":
            text = "Owner submitted in Neo; JobPilot typed the emailed security code and saw the confirmation page."
        self.application.transition(
            job_id,
            "submitted",
            GateContext(approval_for_draft_hash=draft, current_draft_hash=draft, confirmation=kind),
            actor="user",
        )
        self._conclude(
            job_id,
            lambda: self.repository.insert_applications_for_record_submitted(job_id, job["cv_version_id"], kind, text),
            confirmation=("user", {"ok": bool(confirmation_seen), "verification": kind, "method": method}),
        )
        self._publish(job_id)
        return True

    def begin_auto_submit(self, job_id: str, mode_payload: dict[str, object]) -> None:
        """Auto-mode run start (ADR 0026): the durable no-repeat fence goes in before the agent may click, so a
        crash or timeout after the click can never lead to a second submission. Caller holds the transaction."""
        self.repository.lock_job(job_id)
        if not self.application.submit_clicked(job_id):
            self.application.event(job_id, "submit_clicked", "system", {"auto": True, **mode_payload})

    def release_auto_fence(self, job_id: str) -> None:
        """The agent reported it stopped before the click (filled / needs_answers), so the auto fence is withdrawn.
        Only rows this module wrote with auto=true are touched; owner, unapproved and legacy fences stay."""
        if self.repository.delete_auto_submit_fence(job_id):
            self.application.event(job_id, "submit_fence_released", "system", {"reason": "agent stopped before submit"})

    def record_auto_submitted(self, job_id: str, draft: str | None, text: str) -> None:
        """An auto-mode run clicked Submit and reported the confirmation. The approval is system-made ("auto-mode"),
        bound to the draft the agent reported and consumed by this run. Caller holds the transaction."""
        self.repository.lock_job(job_id)
        if self.repository.find_applications_for_record_submitted(job_id):
            return
        job = self.application.job(job_id)
        if not draft:
            # The run ended without a usable report (timeout, bad JSON) but the page showed the confirmation.
            form = self.application.form(job_id)
            draft = str((form or {}).get("draft_hash") or hashlib.sha256(f"auto-observed:{job_id}".encode()).hexdigest())
        if not self.repository.find_unconsumed_submit_approval(job_id, draft):
            self.repository.insert_approvals_for_approve_submit(job_id, draft, "auto-mode")
        self.application.event(job_id, "approval", "system", {"scope": "auto-mode", "draftHash": draft})
        context = GateContext(
            agent_reported_draft=True,
            approval_for_draft_hash=self.application.approval_for(job_id, draft),
            current_draft_hash=draft,
            confirmation="agent-reported",
        )
        self._walk_to_pre_submit(job_id, context)
        self.application.transition(job_id, "submitted", context)
        self._conclude(
            job_id,
            lambda: self.repository.insert_reported_application(job_id, job["cv_version_id"], "agent-reported", text[:500]),
            confirmation=("system", {"ok": True, "verification": "agent-reported"}),
        )
        self._publish(job_id)

    def record_agent_submitted(self, job_id: str, text: str) -> None:
        """An agent reported (or the server saw) a submission nobody authorized: the agent never clicks Submit
        (ADR 0026). Detection only: record it, stop automation and ask the owner to check. Caller holds the
        service transaction."""
        self.repository.lock_job(job_id)
        form = self.application.form(job_id)
        job = self.application.job(job_id)
        if self.repository.find_applications_for_record_submitted(job_id):
            return
        draft = (form or {}).get("draft_hash")
        kind: Literal["unapproved-reported"] = "unapproved-reported"
        context = GateContext(
            agent_reported_draft=True,
            approval_for_draft_hash=self.application.approval_for(job_id, draft),
            current_draft_hash=draft,
            confirmation=kind,
            unapproved_submit_reported=True,
        )
        self.application.event(job_id, "unapproved_submit_reported", "system", {"verification": "agent-reported"})
        # Durable no-repeat fence; this is detection, never permission to click.
        if not self.application.submit_clicked(job_id):
            self.application.event(job_id, "submit_clicked", "system", {"unapproved": True, "reported": True})
        self._walk_to_pre_submit(job_id, context)
        self.application.transition(job_id, "submitted", context)
        self._conclude(
            job_id,
            lambda: self.repository.insert_reported_application(job_id, job["cv_version_id"], kind, text),
            confirmation=("system", {"ok": True, "verification": kind}),
        )
        self.application.wait(job_id, "you", "SUBMITTED AS REPORTED WITHOUT APPROVAL: check this application")
        # Kill switch: whatever let a submission through must not run again unattended.
        self.application.set_automation(False, actor="system", reason="unapproved_submit")
        self._publish(job_id, queue_cv=False)

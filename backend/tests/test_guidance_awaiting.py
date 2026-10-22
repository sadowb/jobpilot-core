"""Guidance and resume on a parked (awaiting_approval) job reach an agent run; incomplete `filled` reports are held."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock

from jobpilot.domain.form_audit import problems
from jobpilot.repository.jobs import JobsRepository
from jobpilot.service import Services
from jobpilot.service.agent_guidance import AgentGuidanceService
from jobpilot.service.jobs import JobsService


def make(status: str = "awaiting_approval", *, submit_began: bool = False, applied: bool = False) -> tuple[JobsService, Any]:
    app: Any = MagicMock(spec=Services)
    app.db = MagicMock()
    app.submit_clicked.return_value = submit_began
    state = {"status": status, "waiting_on": "you", "archived_at": None}
    app.job.side_effect = lambda _: dict(state)
    app._live.side_effect = lambda _: dict(state)
    app.invalidate_form.side_effect = lambda _j, **_: state.update(status="preparing")
    app.approvals = MagicMock()
    app.approvals.repository.find_applications_for_record_submitted.return_value = {"x": 1} if applied else None
    service = JobsService(app)
    service.repository = MagicMock(spec=JobsRepository)
    service.repository.find_job_view.side_effect = lambda _: dict(state)
    return service, app


class ReopenTests(unittest.TestCase):
    def test_reopen_drops_the_draft_enqueues_and_records_why(self) -> None:
        service, app = make()
        service.reopen_parked("j", reason="owner guidance")
        app.invalidate_form.assert_called_once_with("j")
        app.enqueue.assert_called_once_with("j")
        service.repository.update_jobs_for_resume.assert_called_once_with("j")
        app.event.assert_called_once_with("j", "resume", "user", {"reopened": True, "reason": "owner guidance"})

    def test_refused_once_a_submit_began_or_was_recorded(self) -> None:
        for kwargs in ({"submit_began": True}, {"applied": True}):
            service, app = make(**kwargs)
            with self.assertRaisesRegex(PermissionError, "submission already begun"):
                service.reopen_parked("j")
            app.enqueue.assert_not_called()

    def test_refused_when_not_parked(self) -> None:
        service, app = make("preparing")
        with self.assertRaisesRegex(PermissionError, "not waiting for you to submit"):
            service.reopen_parked("j")

    def test_resume_on_a_parked_job_goes_through_reopen_not_a_silent_enqueue(self) -> None:
        service, app = make()
        service.resume("j")
        app.enqueue.assert_called_once_with("j")
        self.assertEqual(app.event.call_args.args[3]["reason"], "owner resume")

    def test_enqueue_never_queues_a_job_the_claim_skips(self) -> None:
        service, app = make()
        with self.assertRaisesRegex(PermissionError, "send it back to the agent"):
            service.enqueue("j")
        service.repository.insert_tasks_for_enqueue.assert_not_called()
        service, _ = make("preparing")
        service.enqueue("j")
        service.repository.insert_tasks_for_enqueue.assert_called_once()


class GuidanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc: Any = MagicMock(spec=Services)
        self.svc.db = MagicMock()
        self.svc.jobs = MagicMock()
        self.svc.submit_clicked.return_value = False
        self.guidance = AgentGuidanceService(self.svc)
        self.guidance.repository = MagicMock()

    def test_guidance_on_a_parked_job_sends_it_back_with_the_owner_guidance_reason(self) -> None:
        self.svc.job.return_value = {"status": "awaiting_approval"}
        self.guidance.send("j", "you still need to fill everything")
        self.svc.jobs.reopen_parked.assert_called_once_with("j", reason="owner guidance")
        self.svc.resume.assert_not_called()
        self.assertEqual(self.svc.event.call_args.args[1], "agent_guidance")

    def test_guidance_before_parking_resumes_as_before(self) -> None:
        self.svc.job.return_value = {"status": "preparing"}
        self.guidance.send("j", "hi")
        self.svc.resume.assert_called_once_with("j")
        self.svc.jobs.reopen_parked.assert_not_called()

    def test_guidance_on_finished_or_fenced_jobs_is_a_409_style_refusal(self) -> None:
        self.svc.job.return_value = {"status": "submitted"}
        with self.assertRaisesRegex(PermissionError, "before submission"):
            self.guidance.send("j", "hi")
        self.svc.job.return_value = {"status": "awaiting_approval"}
        self.svc.submit_clicked.return_value = True
        with self.assertRaisesRegex(PermissionError, "before submission"):
            self.guidance.send("j", "hi")
        self.svc.event.assert_not_called()


class FormAuditTests(unittest.TestCase):
    def test_wrong_email_empty_required_and_errors_are_reported(self) -> None:
        audit = {
            "emails": ["dino@gmail.com", "Me@Example.com"],
            "empty": ["Phone", "Phone"],
            "errors": ["Enter a valid phone number"],
        }
        found = problems(audit, "me@example.com")
        self.assertEqual(
            found,
            [
                "an email field shows dino@gmail.com, not your email",
                "required field still empty: Phone",
                "the page shows an error: Enter a valid phone number",
            ],
        )

    def test_clean_page_and_unknown_owner_email_report_nothing_about_email(self) -> None:
        self.assertEqual(problems({"emails": ["me@example.com"], "empty": [], "errors": []}, "ME@example.com"), [])
        self.assertEqual(problems({"emails": ["x@y.z"]}, None), [])
        self.assertEqual(problems({}, "me@example.com"), [])


if __name__ == "__main__":
    unittest.main()

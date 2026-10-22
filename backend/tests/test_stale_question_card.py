"""A job past preparation never renders as a question card; the unapproved-submit alert is owner-acknowledged."""

import unittest
from typing import Any, get_type_hints
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from jobpilot.controller import jobs as controller
from jobpilot.domain.state_machine import QUESTION_STATES
from jobpilot.repository.forms import FormsRepository
from jobpilot.repository.jobs import JobsRepository
from jobpilot.service import Services
from jobpilot.service.forms import FormsService
from jobpilot.service.jobs import JobsService


def make(status: str = "preparing") -> tuple[JobsService, Any, Any]:
    app: Any = MagicMock(spec=Services)
    app.db = MagicMock()
    app.submit_clicked.return_value = False
    app.job.return_value = {"status": status}
    app._live.return_value = {"status": status, "waiting_on": "you", "archived_at": None}
    app.required_open.return_value = []
    service = JobsService(app)
    repo: Any = MagicMock(spec=JobsRepository)
    service.repository = repo
    return service, app, repo


class ClearOnLeavingPreparation(unittest.TestCase):
    def test_transition_out_of_preparation_clears_questions_but_staying_does_not(self) -> None:
        for to, cleared in (
            ("preparing", False),
            ("ready", False),
            ("awaiting_approval", True),
            ("submitted", True),
            ("closed", True),
            ("withdrawn", True),
        ):
            with self.subTest(to=to):
                service, _, repo = make("ready" if to != "ready" else "screened")
                with patch("jobpilot.service.jobs.can_transition") as gate:
                    gate.return_value.ok = True
                    service.transition("j", to)
                self.assertEqual(repo.update_job_forms_clear_missing.called, cleared)

    def test_refused_transition_clears_nothing(self) -> None:
        service, _, repo = make("preparing")
        with patch("jobpilot.service.jobs.can_transition") as gate:
            gate.return_value.ok, gate.return_value.reason = False, "no"
            with self.assertRaises(PermissionError):
                service.transition("j", "submitted")
        repo.update_job_forms_clear_missing.assert_not_called()

    def test_question_states_exclude_awaiting_approval(self) -> None:
        self.assertNotIn("awaiting_approval", QUESTION_STATES)
        self.assertIn("preparing", QUESTION_STATES)


class QuestionActionsAfterPreparation(unittest.TestCase):
    def test_resume_and_try_anyway_refuse_a_submitted_job_with_a_human_message(self) -> None:
        for try_anyway in (False, True):
            with self.subTest(try_anyway=try_anyway):
                service, _, repo = make("submitted")
                with self.assertRaisesRegex(PermissionError, "already submitted"):
                    service.resume("j", try_anyway=try_anyway)
                repo.allow_unknown_questions.assert_not_called()
                repo.update_jobs_for_resume.assert_not_called()

    def test_try_anyway_needs_a_question_state_but_plain_resume_works_at_awaiting_approval(self) -> None:
        service, _, repo = make("awaiting_approval")
        with self.assertRaisesRegex(PermissionError, "no longer waiting for answers"):
            service.resume("j", try_anyway=True)
        with patch.object(service, "reopen_parked") as reopen:
            service.resume("j")  # nothing claims awaiting_approval: resume sends it back to the agent, never a silent enqueue
        reopen.assert_called_once_with("j", reason="owner resume")
        repo.update_jobs_for_resume.assert_not_called()

    def test_saving_answers_for_a_submitted_job_is_refused(self) -> None:
        app: Any = MagicMock(spec=Services)
        app.db = MagicMock()
        app.submit_clicked.return_value = False
        app._live.return_value = {"status": "submitted", "waiting_on": "you", "archived_at": None}
        service = FormsService(app)
        service.repository = MagicMock(spec=FormsRepository)
        with self.assertRaisesRegex(PermissionError, "no longer waiting for answers"):
            service.set_job_answers("j", [("q", "a", "job:j")])
        app.answer_set.assert_not_called()

    def test_endpoints_translate_refusals_to_409_with_the_message(self) -> None:
        with patch.object(controller, "application_service") as svc:
            svc.resume.side_effect = PermissionError("This application is already submitted; there is nothing left to resume.")
            with self.assertRaises(HTTPException) as caught:
                controller.job_resume("j", MagicMock(), try_anyway=True)
            self.assertEqual((caught.exception.status_code, caught.exception.detail[:24]), (409, "This application is alre"))
            svc.acknowledge_unapproved_submit.side_effect = PermissionError("nothing to check")
            with self.assertRaises(HTTPException) as caught:
                controller.unapproved_submit_acknowledge("j", MagicMock())
            self.assertEqual(caught.exception.status_code, 409)


class AcknowledgeUnapprovedSubmit(unittest.TestCase):
    def test_records_event_closes_task_and_never_touches_automation(self) -> None:
        service, app, repo = make("submitted")
        repo.find_job_events_for_kind.side_effect = lambda _j, kind: None if kind.endswith("acknowledged") else {"x": 1}
        service.acknowledge_unapproved_submit("j")
        repo.update_for_acknowledge_unapproved_submit.assert_called_once_with("j")
        app.event.assert_called_once_with("j", "unapproved_submit_acknowledged", "user")
        app.set_automation.assert_not_called()
        app.enqueue.assert_not_called()

    def test_is_idempotent(self) -> None:
        service, app, repo = make("submitted")
        repo.find_job_events_for_kind.return_value = {"x": 1}  # already acknowledged
        service.acknowledge_unapproved_submit("j")
        repo.update_for_acknowledge_unapproved_submit.assert_not_called()
        app.event.assert_not_called()

    def test_requires_an_unapproved_submission(self) -> None:
        service, app, repo = make("submitted")
        repo.find_job_events_for_kind.return_value = None
        with self.assertRaisesRegex(PermissionError, "no unapproved submission"):
            service.acknowledge_unapproved_submit("j")
        app.event.assert_not_called()

    def test_route_is_owner_session_only(self) -> None:
        route = next(r for r in controller.router.routes if getattr(r, "path", "").endswith("/unapproved-submit/acknowledge"))
        self.assertEqual(route.methods, {"POST"})  # type: ignore[attr-defined]
        from jobpilot.middleware.auth import UserAuth

        hints = get_type_hints(controller.unapproved_submit_acknowledge, include_extras=True)
        self.assertEqual(hints["_"], UserAuth)

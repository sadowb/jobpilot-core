"""Ingest control work and the required-answer gate, with no broker/DB/browser."""

import json
import unittest
from contextlib import nullcontext
from typing import Any
from unittest.mock import MagicMock, patch

import test_question_planner

from jobpilot.domain.answers import Known
from jobpilot.domain.posting_form import PostingForm, PostingMeta, PostingQuestion
from jobpilot.llm import LlmResult
from jobpilot.service.forms import FormsService
from jobpilot.service.jobs import JobsService
from jobpilot.service.preflight import prepare_ingested


class CombinedTests(test_question_planner.PlannerTests):
    def combined(self) -> Any:
        candidate = {"track_id": "swe", "version_id": 1, "market": "us", "cv_text": "Built Python APIs", "title": "SWE"}
        with (
            patch("jobpilot.service.question_planner.CvSelectionService._candidates", return_value=[candidate]),
            patch("jobpilot.service.direct.DirectService._assign_track", return_value="ready") as assign,
        ):
            result = self.planner.plan("j", self.form, self.check, choose_cv=True)
        return result, assign

    def test_one_completion_selects_cv_and_answers(self) -> None:
        self.completion.return_value = LlmResult(
            json.dumps(
                {
                    "cv_choice": {"track_id": "swe", "job_market": "us", "summary": "Relevant APIs", "evidence": []},
                    "answers": [{"key": "q:why", "answer": "I build Python APIs."}],
                }
            ),
            1,
            1,
            0.0,
        )
        outcome, assign = self.combined()
        self.completion.assert_called_once()
        assign.assert_called_once()
        self.assertEqual(outcome.stored, 1)

    def test_merged_ingest_optional_only_never_parks(self) -> None:
        self.form = PostingForm("greenhouse", PostingMeta(), (PostingQuestion("q:o", "Nickname", "text", False),))
        self.completion.return_value = LlmResult(
            json.dumps(
                {
                    "cv_choice": {"track_id": "swe", "job_market": "us", "summary": "Fits", "evidence": []},
                    "answers": [{"key": "q:o", "answer": None}],
                }
            ),
            1,
            1,
            0.0,
        )
        outcome, assign = self.combined()
        self.completion.assert_called_once()
        assign.assert_called_once()
        self.assertEqual([q.key for q in outcome.open_questions], ["q:o"])
        self.assertFalse(self.planner.surface("j", self.form, outcome))
        self.svc.wait.assert_not_called()
        self.planner.direct.plan.assert_not_called()

    def test_wrong_market_never_assigns_or_stores(self) -> None:
        self.svc.job.return_value["location"] = "Morocco"
        self.completion.return_value = LlmResult(
            json.dumps(
                {
                    "cv_choice": {"track_id": "swe", "job_market": "us", "summary": "x", "evidence": []},
                    "answers": [],
                }
            ),
            1,
            1,
            0.0,
        )
        with self.assertRaises(ValueError):
            self.combined()

    def test_required_overflow_stays_in_batch(self) -> None:
        self.form = PostingForm(
            "greenhouse",
            PostingMeta(),
            tuple(PostingQuestion(f"q:{n}", f"Unclear question {n}", "text", True) for n in range(65)),
        )
        self.completion.return_value = LlmResult('{"answers":[]}', 1, 1, 0.0)
        outcome = self.run_plan()
        self.assertEqual(len(outcome.open_questions), 65)
        self.completion.assert_called_once()

    def test_required_resume_text_is_not_silently_omitted(self) -> None:
        self.form = PostingForm("greenhouse", PostingMeta(), (PostingQuestion("q:r", "Resume/CV", "textarea", True),))
        self.completion.return_value = LlmResult('{"answers":[]}', 1, 1, 0.0)
        result = self.run_plan()
        self.assertEqual([q.key for q in result.open_questions], ["q:r"])
        self.assertTrue(self.planner.surface("j", self.form, result))

    def test_owner_answers_during_model_call_win_at_surface(self) -> None:
        self.completion.return_value = LlmResult('{"answers":[]}', 1, 1, 0.0)
        result = self.run_plan()
        self.svc.load_known.return_value = Known(
            answers={
                "first name": {"job:j": "Owner"},
                "are you legally authorized to work in the united states": {"job:j": "Yes"},
                "why do you want to join us": {"job:j": "Owner's answer"},
            }
        )
        self.assertFalse(self.planner.surface("j", self.form, result))
        self.svc.wait.assert_not_called()


class BoundedReadTests(unittest.TestCase):
    def test_slow_trickle_stops_at_body_deadline(self) -> None:
        from jobpilot.extract import _read_bounded

        response = MagicMock()
        response.read1.return_value = b"x"
        with patch("jobpilot.extract.time.monotonic", side_effect=[0, 0, 13]), self.assertRaises(TimeoutError):
            _read_bounded(response)
        response.read1.assert_called_once()

    def test_body_read_stops_at_size_cap(self) -> None:
        from jobpilot.extract import _read_bounded

        response = MagicMock()
        response.read1.side_effect = lambda size: b"x" * size
        with patch("jobpilot.extract._MAX_BYTES", 9):
            self.assertEqual(_read_bounded(response), b"x" * 10)
        response.read1.assert_called_once_with(10)


class ControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc: Any = MagicMock()
        self.svc.db.transaction.side_effect = lambda: nullcontext()
        for name in ("PreflightRepository", "PostingPrefetch", "QuestionPlanner"):
            p = patch(f"jobpilot.service.preflight.{name}")
            setattr(self, name, p.start().return_value)
            self.addCleanup(p.stop)
        self.PostingPrefetch.duplicate = False
        self.PreflightRepository.start.return_value = True
        self.PreflightRepository.active.return_value = True

    def test_duplicate_delivery_does_no_work(self) -> None:
        self.PreflightRepository.start.return_value = False
        prepare_ingested(self.svc, "j")
        self.PostingPrefetch.fetch.assert_not_called()

    def test_failure_releases_fence_and_requests_dispatch(self) -> None:
        self.QuestionPlanner.plan.side_effect = ValueError("private result")
        prepare_ingested(self.svc, "j")
        self.PreflightRepository.finish.assert_called_once_with("j", False)
        self.svc.request_dispatch.assert_called_once()
        self.assertNotIn("private result", str(self.svc.event.call_args))
        self.svc.ensure_tab.assert_not_called()

    def test_success_surfaces_one_batch_without_browser(self) -> None:
        prepare_ingested(self.svc, "j")
        self.QuestionPlanner.plan.assert_called_once()
        self.assertTrue(self.QuestionPlanner.plan.call_args.kwargs["choose_cv"])
        self.QuestionPlanner.surface.assert_called_once()
        self.PreflightRepository.finish.assert_called_once_with("j", True)
        self.svc.ensure_tab.assert_not_called()

    def test_expired_work_cannot_surface_questions(self) -> None:
        self.PreflightRepository.active.return_value = False
        prepare_ingested(self.svc, "j")
        self.QuestionPlanner.surface.assert_not_called()
        self.PreflightRepository.finish.assert_called_once_with("j", False)


class AnswerGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc: Any = MagicMock()
        self.svc.submit_clicked.return_value = False
        self.svc._live.return_value = {"waiting_on": "you", "status": "preparing"}
        self.jobs = JobsService(self.svc)
        self.jobs.repository = MagicMock()

    def test_partial_batch_does_not_wake(self) -> None:
        self.svc.required_open.return_value = ["q"]
        self.jobs.resume("j")
        self.svc.enqueue.assert_not_called()
        self.svc.wait.assert_called_once()

    def test_complete_batch_wakes_once(self) -> None:
        self.svc.required_open.return_value = []
        self.jobs.resume("j")
        self.svc._live.return_value = {"waiting_on": "agent", "status": "preparing"}
        self.jobs.resume("j")
        self.svc.enqueue.assert_called_once_with("j")

    def test_explicit_try_anyway_bypasses_question_gate_only(self) -> None:
        self.svc.required_open.return_value = ["q"]
        self.jobs.resume("j", try_anyway=True)
        self.jobs.repository.allow_unknown_questions.assert_called_once_with("j")
        self.svc.enqueue.assert_called_once()
        self.svc.submit_clicked.return_value = True
        with self.assertRaises(PermissionError):
            self.jobs.resume("j", try_anyway=True)

    def test_optional_only_batch_resumes_without_answers(self) -> None:
        self.svc.form.return_value = {
            "missing": ["q"],
            "inventory": [{"key": "q", "label": "Nickname", "type": "text", "required": False}],
        }
        self.svc.job.return_value = {}
        self.svc.load_known.return_value = Known()
        self.svc.required_open.side_effect = FormsService(self.svc).required_open
        self.jobs.resume("j")
        self.svc.wait.assert_not_called()
        self.svc.enqueue.assert_called_once_with("j")

    def test_gate_resolves_only_the_unknown_required_fields(self) -> None:
        self.svc.form.return_value = {
            "missing": ["q"],
            "inventory": [
                {"key": "q", "label": "Custom question", "type": "text", "required": True},
                {"key": "agent-filled", "label": "Another question", "type": "text", "required": True},
            ],
        }
        self.svc.job.return_value = {}
        self.svc.load_known.return_value = Known(answers={"custom question": {"job:j": "Answer"}})
        self.assertEqual(FormsService(self.svc).required_open("j"), [])

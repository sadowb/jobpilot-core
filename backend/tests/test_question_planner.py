"""Pre-browser question answering: deterministic sources first, one model call for the rest, owner edits win."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from jobpilot.config import Settings
from jobpilot.domain.answers import SKIPPED, Known
from jobpilot.domain.canonical import norm
from jobpilot.domain.posting_form import PostingForm, PostingMeta, PostingQuestion
from jobpilot.llm import LlmResult
from jobpilot.service.question_planner import SOURCE, QuestionPlanner

YES_NO = ("Yes", "No")
QUESTIONS = (
    PostingQuestion("q:first", "First Name", "text", True, group="standard"),
    PostingQuestion("q:resume", "Resume", "file", True, group="standard"),
    PostingQuestion("q:auth", "Are you legally authorized to work in the United States?", "select", True, YES_NO),
    PostingQuestion("q:why", "Why do you want to join us?", "textarea", True),
    PostingQuestion("q:lang", "Which programming language do you know best?", "select", False, ("Python", "Go", "Rust")),
    PostingQuestion("q:team", "Which team interests you most?", "select", False, ("Platform", "Data", "Security")),
    PostingQuestion("q:vet", "Veteran Status", "select", False, ("I am a veteran", "I don't wish to answer"), group="eeoc"),
)


def llm(answers: list[dict[str, Any]]) -> LlmResult:
    return LlmResult(json.dumps({"answers": answers}), 200, 80, 0.002)


class PlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc: Any = MagicMock()
        self.svc.cfg = Settings(_env_file=None)
        self.svc.job.return_value = {
            "company_name": "Acme", "company_slug": "acme", "role": "Intern", "url": "https://x/y", "location": "NYC",
            "cv_version_id": None,
        }  # fmt: skip
        self.svc.posting.return_value = "Build backend services in Python."
        self.svc.load_known.return_value = Known(profile={"first_name": "Ada"})

        def suggest(label: str, answer: str, job: str, source: str) -> bool:
            known = self.svc.load_known.return_value
            values = known.answers.setdefault(norm(label), {})
            if f"job:{job}" in values:
                return False
            values[f"job:{job}"] = answer
            return True

        self.svc.answer_suggest.side_effect = suggest
        self.planner = QuestionPlanner(self.svc)
        self.planner.direct = MagicMock()
        self.planner.direct.last_event_payload.return_value = {}
        self.planner.direct.owner_data.return_value = {
            "profile": {"first_name": "Ada", "country": "United States"},
            "facts": [{"claim": "Built a Python API"}],
            "answers": [],
            "skipped_questions": [],
        }
        patcher = patch("jobpilot.service.question_planner.compare_text")
        self.completion = patcher.start()
        self.addCleanup(patcher.stop)
        self.form = PostingForm("greenhouse", PostingMeta(), QUESTIONS)
        self.check = MagicMock()

    def run_plan(self) -> Any:
        return self.planner.plan("j", self.form, self.check)

    def stored(self) -> dict[str, str]:
        return {c.args[0]: c.args[1] for c in self.svc.answer_suggest.call_args_list}

    def test_profile_resolved_fields_never_reach_the_model(self) -> None:
        self.completion.return_value = llm([])
        self.run_plan()
        keys = [q["key"] for q in json.loads(self.completion.call_args.args[2])["questions"]]
        self.assertNotIn("q:first", keys)  # build_plan answers it from the profile
        self.assertNotIn("q:resume", keys)  # files are the browser's job
        self.assertNotIn("q:vet", keys)  # voluntary self-identification is handled without the model
        self.assertEqual(sorted(keys), ["q:auth", "q:lang", "q:team", "q:why"])

    def test_answers_are_stored_job_scoped_and_validated_against_options(self) -> None:
        self.completion.return_value = llm(
            [
                {"key": "q:lang", "answer": "python", "basis": "cv"},
                {"key": "q:why", "answer": "I enjoy building reliable backend systems.", "basis": "cv"},
                {"key": "q:team", "answer": "Quantum", "basis": "inferred"},  # not one of the form's options
            ]
        )
        outcome = self.run_plan()
        stored = self.stored()
        self.assertEqual(stored["Which programming language do you know best?"], "Python")
        self.assertEqual(stored["Why do you want to join us?"], "I enjoy building reliable backend systems.")
        self.assertNotIn("Which team interests you most?", stored)
        self.assertIn("Which team interests you most?", outcome.open)
        for call in self.svc.answer_suggest.call_args_list:
            self.assertEqual(call.args[2], "j")
        self.assertIn(SOURCE, [c.args[3] for c in self.svc.answer_suggest.call_args_list])

    def test_ungrounded_question_stays_open_not_guessed(self) -> None:
        self.completion.return_value = llm([{"key": "q:auth", "answer": None, "basis": "inferred"}])
        outcome = self.run_plan()
        self.assertNotIn("Are you legally authorized to work in the United States?", self.stored())
        self.assertIn("Are you legally authorized to work in the United States?", outcome.open)

    def test_voluntary_self_identification_defaults_to_the_forms_decline_option(self) -> None:
        self.completion.return_value = llm([])
        outcome = self.run_plan()
        self.assertEqual(self.stored()["Veteran Status"], "I don't wish to answer")
        self.assertEqual(outcome.declined, 1)

    def test_skipped_question_is_left_alone(self) -> None:
        self.svc.load_known.return_value = Known(
            profile={"first_name": "Ada"}, answers={"why do you want to join us": {"job:j": SKIPPED}}
        )
        self.completion.return_value = llm([])
        self.run_plan()
        keys = [q["key"] for q in json.loads(self.completion.call_args.args[2])["questions"]]
        self.assertNotIn("q:why", keys)

    def attestation(self, answer: str, evidence: str) -> Any:
        self.form = PostingForm(
            "greenhouse",
            PostingMeta(),
            (PostingQuestion("q:clr", "Are you eligible to obtain a U.S. Government Clearance?", "select", True, YES_NO),),
        )
        self.planner.direct.owner_data.return_value["profile"]["citizenships"] = "United States"
        self.completion.return_value = llm([{"key": "q:clr", "answer": answer, "evidence": evidence}])
        return self.run_plan()

    def test_attestation_with_verbatim_support_is_accepted(self) -> None:
        self.attestation("Yes", "citizenships: United States")
        self.assertEqual(self.stored(), {"Are you eligible to obtain a U.S. Government Clearance?": "Yes"})

    def test_attestation_without_support_in_the_owners_material_stays_open(self) -> None:
        outcome = self.attestation("Yes", "the candidate is clearly a citizen")  # a claim, not a quote
        self.assertEqual(self.stored(), {})
        self.assertEqual(outcome.open, ["Are you eligible to obtain a U.S. Government Clearance?"])

    def test_attestation_with_no_evidence_stays_open(self) -> None:
        self.attestation("Yes", "")
        self.assertEqual(self.stored(), {})

    def test_resume_paste_box_is_not_asked(self) -> None:
        self.form = PostingForm("greenhouse", PostingMeta(), (PostingQuestion("q:r", "Resume/CV", "textarea", False),))
        self.run_plan()
        self.completion.assert_not_called()

    def test_secret_shaped_model_output_is_dropped(self) -> None:
        self.completion.return_value = llm([{"key": "q:why", "answer": "password: hunter2hunter2", "basis": "inferred"}])
        self.run_plan()
        self.assertNotIn("Why do you want to join us?", self.stored())

    def test_malformed_model_output_stores_nothing_and_does_not_raise(self) -> None:
        self.completion.return_value = LlmResult("I cannot help with that", 10, 5, 0.0)
        outcome = self.run_plan()
        self.assertEqual(outcome.stored, 0)
        self.assertEqual(len(outcome.open), 4)

    def test_nothing_to_ask_means_no_model_call(self) -> None:
        self.form = PostingForm("greenhouse", PostingMeta(), (QUESTIONS[0], QUESTIONS[1]))
        outcome = self.run_plan()
        self.completion.assert_not_called()
        self.assertFalse(outcome.asked_model)

    def test_unchanged_open_questions_are_not_asked_again(self) -> None:
        self.completion.return_value = llm([])
        self.run_plan()
        signature = self.svc.event.call_args.args[3]["signature"]
        self.planner.direct.last_event_payload.return_value = {"signature": signature}
        self.completion.reset_mock()
        outcome = self.run_plan()
        self.completion.assert_not_called()
        self.assertFalse(outcome.asked_model)
        self.assertTrue(outcome.open)

    def test_new_owner_material_asks_again(self) -> None:
        self.completion.return_value = llm([])
        self.run_plan()
        signature = self.svc.event.call_args.args[3]["signature"]
        self.planner.direct.last_event_payload.return_value = {"signature": signature}
        self.planner.direct.owner_data.return_value["profile"]["willing_onsite"] = "Yes"
        self.completion.reset_mock()
        self.run_plan()
        self.completion.assert_called_once()

    def test_required_open_questions_are_surfaced_before_the_browser(self) -> None:
        self.completion.return_value = llm([{"key": "q:lang", "answer": "python", "evidence": ""}])
        outcome = self.run_plan()
        self.assertTrue(self.planner.surface("j", self.form, outcome))
        inventory = self.svc.set_inventory.call_args.args[1]
        self.assertTrue(all(item["type"] != "file" for item in inventory))
        _, plan, missing = self.planner.direct.plan.call_args.args
        self.assertEqual(
            sorted(missing),
            ["page:0:q:auth", "page:0:q:why"],  # required and unanswered; optional q:team stays unasked
        )
        self.assertIn("page:0:q:lang", [p["key"] for p in plan])  # what is known is carried along
        self.svc.wait.assert_called_once_with("j", "you", "answer the missing application questions")

    def test_optional_open_questions_never_block(self) -> None:
        self.form = PostingForm("greenhouse", PostingMeta(), (QUESTIONS[4],))  # optional language select
        self.completion.return_value = llm([])
        outcome = self.run_plan()
        self.assertFalse(self.planner.surface("j", self.form, outcome))
        self.svc.wait.assert_not_called()

    def test_cost_is_recorded_against_the_job(self) -> None:
        self.completion.return_value = llm([])
        self.run_plan()
        step = self.svc.cost.call_args.args[4]
        self.assertEqual(step, "answer_plan")


class SuggestWriteTests(unittest.TestCase):
    """The agent's answer must never replace one the owner (or an earlier run) already stored."""

    def setUp(self) -> None:
        from jobpilot.repository.forms import FormsRepository
        from jobpilot.service.forms import FormsService

        self.app: Any = MagicMock()
        self.service = FormsService(self.app)
        self.repo: Any = MagicMock(spec=FormsRepository)
        self.service.repository = self.repo

    def test_written_when_absent_and_recorded(self) -> None:
        self.repo.insert_answer_if_absent.return_value = 1
        self.assertTrue(self.service.answer_suggest("Why us?", "Because.", "j", SOURCE))
        self.repo.insert_answer_if_absent.assert_called_once_with("why us", "Because.", "job:j", SOURCE)
        self.app.event.assert_called_once_with("j", "answer_suggested", "system", {"questionKey": "why us"})

    def test_existing_answer_is_left_untouched(self) -> None:
        self.repo.insert_answer_if_absent.return_value = 0  # ON CONFLICT DO NOTHING
        self.assertFalse(self.service.answer_suggest("Why us?", "Because.", "j", SOURCE))
        self.app.event.assert_not_called()


if __name__ == "__main__":
    unittest.main()

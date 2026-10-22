"""Re-asked questions, new-vs-answered classification and the owner's "I did it myself" actions (Task 10)."""

from __future__ import annotations

import unittest
from contextlib import nullcontext
from typing import Any
from unittest.mock import MagicMock

from jobpilot.domain.answers import SKIPPED, JobContext, Known
from jobpilot.domain.question_view import is_generic, step_of
from jobpilot.domain.reask import classify_reported
from jobpilot.dto.direct import DirectResult
from jobpilot.service import Services
from jobpilot.service.dashboard import DashboardService
from jobpilot.service.direct import MAX_REASK_RUNS, DirectService
from jobpilot.service.forms import OWNER_COMPLETED_GUIDANCE, FormsService

CTX = JobContext()


def question(label: str, kind: str = "text", options: list[str] | None = None) -> dict[str, Any]:
    return {"label": label, "kind": kind, "options": options or []}


class ClassifyTests(unittest.TestCase):
    def test_saved_answer_matches_by_normalised_label_not_by_the_runs_key(self) -> None:
        known = Known(answers={"referral source": {"global": "LinkedIn"}, "favourite colour": {"job:j1": SKIPPED}})
        found = classify_reported([question("Referral  Source?"), question("Salary band")], known, "j1", "acme", CTX)
        self.assertEqual(found.new, [1])
        ((index, settled),) = found.settled
        self.assertEqual((index, settled.value, settled.source), (0, "LinkedIn", "answer:global"))

    def test_job_scope_wins_over_global_and_other_jobs_do_not_leak(self) -> None:
        known = Known(answers={"why us": {"global": "G", "job:j1": "mine", "job:j2": "other"}})
        ((_, settled),) = classify_reported([question("Why us")], known, "j1", "", CTX).settled
        self.assertEqual(settled.value, "mine")
        self.assertEqual(
            classify_reported([question("Why us")], Known(answers={"why us": {"job:j2": "x"}}), "j1", "", CTX).new, [0]
        )

    def test_profile_alias_and_options_are_respected(self) -> None:
        known = Known(profile={"available_from": "2026-11-01"})
        found = classify_reported([question("Available Start Date", "date")], known, "j", "", CTX)
        self.assertEqual([(s.value, s.source) for _, s in found.settled], [("2026-11-01", "profile:available_from")])
        bad = Known(answers={"pick one": {"global": "Maybe"}})
        self.assertEqual(classify_reported([question("Pick one", "select", ["Yes", "No"])], bad, "j", "", CTX).new, [0])

    def test_owner_completed_labels_settle_without_a_value(self) -> None:
        found = classify_reported([question("Referral")], Known(), "j", "", CTX, {"referral"})
        ((_, settled),) = found.settled
        self.assertEqual((settled.value, settled.source), (None, "owner-completed"))

    def test_files_and_blank_labels_stay_new(self) -> None:
        known = Known(answers={"resume": {"global": "x"}})
        self.assertEqual(classify_reported([question("Resume", "file"), question("  ")], known, "j", "", CTX).new, [0, 1])

    def test_step_and_generic_helpers(self) -> None:
        self.assertEqual(step_of("Page filled. Step 2 of 8: next is Referral"), "step 2 of 8")
        self.assertIsNone(step_of("step 9 of 8"))
        self.assertIsNone(step_of(None))
        self.assertTrue(is_generic("Available Start Date"))
        self.assertTrue(is_generic("Are you legally authorized to work in the United States?"))
        self.assertFalse(is_generic("Why do you want to work at Acme?"))


class DashboardUnsettledTests(unittest.TestCase):
    def test_hides_only_questions_the_bank_or_the_owner_covered(self) -> None:
        service = DashboardService(MagicMock())
        service.repository = MagicMock()
        service.repository.owner_completed_labels.return_value = {"referral"}
        views = [
            {"label": "Referral", "type": "text", "options": []},
            {"label": "Salary", "type": "text", "options": []},
            {"label": "Fresh question", "type": "text", "options": []},
        ]
        bank = Known(answers={"salary": {"global": "90k"}})
        kept = service.unsettled("j", "acme", views, bank)
        self.assertEqual([v["label"] for v in kept], ["Fresh question"])


class ApplyReaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc: Any = MagicMock(spec=Services)
        self.svc.db = MagicMock()
        self.svc.approvals = MagicMock()
        self.svc.db.transaction.side_effect = lambda: nullcontext()
        self.svc.job.return_value = {"status": "preparing", "company_slug": "acme", "company_name": "Acme", "role": "Eng"}
        self.svc.submit_clicked.return_value = False
        self.svc.set_meta.return_value = {"ok": True}
        self.svc.load_known.return_value = Known(answers={"referral": {"global": "LinkedIn"}})
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.svc.event.side_effect = lambda _, kind, actor, payload=None: self.events.append((kind, payload or {}))
        self.service = DirectService(self.svc)
        self.service.repository = MagicMock()
        self.service.repository.skipped_question_keys.return_value = set()
        self.service.repository.owner_completed_labels.return_value = set()
        self.service.repository.count_events_since.return_value = 0

    def result(self, *labels: str) -> DirectResult:
        return DirectResult.model_validate(
            {
                "status": "needs_answers",
                "role": "Eng",
                "company": "Acme",
                "fields": [],
                "questions": [{"label": label, "kind": "text", "required": True, "options": []} for label in labels],
                "note": "step 2 of 8",
                "confirmation": None,
            }
        )

    def test_only_settled_questions_requeue_the_agent_instead_of_asking(self) -> None:
        self.assertEqual(self.service.apply("j", self.result("Referral")), "reask")
        ((kind, payload),) = self.events
        self.assertEqual((kind, payload["retry"], payload["settled"][0]["answer"]), ("answers_reasked", True, "LinkedIn"))
        self.svc.wait.assert_not_called()
        self.svc.set_inventory.assert_not_called()

    def test_repeated_reask_stops_at_the_cap_and_tells_the_owner(self) -> None:
        self.service.repository.count_events_since.return_value = MAX_REASK_RUNS
        self.assertEqual(self.service.apply("j", self.result("Referral")), "blocked")
        self.svc.wait.assert_called_once()
        self.assertIn("I filled it in the tab", self.svc.wait.call_args.args[2])
        self.assertEqual(self.events, [])

    def test_mixed_result_asks_only_the_new_question_and_records_the_settled_one_after(self) -> None:
        self.assertEqual(self.service.apply("j", self.result("Referral", "Pet name")), "blocked")
        stored = self.svc.set_inventory.call_args.args[1]
        self.assertEqual([f["label"] for f in stored], ["Pet name"])
        kinds = [kind for kind, _ in self.events]
        self.assertEqual(kinds, ["direct_result", "answers_reasked"])  # the hint stays newer than the result
        self.service.repository.plan.assert_called_once()
        self.assertEqual(self.service.repository.plan.call_args.args[2], ["page:0:reported:0"])

    def test_reasked_hint_expires_once_a_newer_result_exists(self) -> None:
        repo = self.service.repository
        settled = [{"label": "Referral", "answer": "LinkedIn"}]
        repo.last_event.side_effect = lambda _, kind: (5, {"settled": settled}) if kind == "answers_reasked" else None
        self.assertEqual(self.service._reasked_hint("j"), settled)
        repo.last_event.side_effect = lambda _, kind: (5, {"settled": settled}) if kind == "answers_reasked" else (9, {})
        self.assertEqual(self.service._reasked_hint("j"), [])


class OwnerCompletedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app: Any = MagicMock(spec=Services)
        self.app.db = MagicMock()
        self.forms = FormsService(self.app)
        self.forms.repository = MagicMock()
        self.app.submit_clicked.return_value = False
        self.set_job("preparing", "you")
        self.app.form.return_value = {
            "missing": ["page:0:reported:1"],
            "inventory": [
                {"key": "page:0:reported:0", "label": "Name"},
                {"key": "page:0:reported:1", "label": "Referral Source"},
            ],
        }

    def set_job(self, status: str, waiting_on: str) -> None:
        job = {"status": status, "waiting_on": waiting_on}
        self.app.job.return_value = job
        self.app._live.return_value = job

    def test_effects_clear_pending_remember_labels_and_resume_with_the_delta(self) -> None:
        self.assertEqual(self.forms.owner_completed("j", user_session=True), {"ok": True, "already": False})
        self.app.invalidate_form.assert_called_once_with("j", reset_plan=True)
        self.app.event.assert_called_once_with("j", "owner_completed", "user", {"labels": ["referral source"]})
        self.app.guide_agent.assert_called_once_with("j", OWNER_COMPLETED_GUIDANCE)
        self.assertIn("do not ask about them again", OWNER_COMPLETED_GUIDANCE)

    def test_user_session_only(self) -> None:
        with self.assertRaises(PermissionError):
            self.forms.owner_completed("j", user_session=False)
        self.app.guide_agent.assert_not_called()

    def test_idempotent_while_the_agent_already_runs(self) -> None:
        self.set_job("preparing", "agent")
        self.assertEqual(self.forms.owner_completed("j", user_session=True), {"ok": True, "already": True})
        self.app.guide_agent.assert_not_called()
        self.app.invalidate_form.assert_not_called()

    def test_refused_after_submit_began_or_when_awaiting_approval_or_done(self) -> None:
        self.app.submit_clicked.return_value = True
        with self.assertRaises(PermissionError):
            self.forms.owner_completed("j", user_session=True)
        self.app.submit_clicked.return_value = False
        for status in ("awaiting_approval", "submitted"):
            self.set_job(status, "you")
            with self.assertRaises(PermissionError):
                self.forms.owner_completed("j", user_session=True)


if __name__ == "__main__":
    unittest.main()

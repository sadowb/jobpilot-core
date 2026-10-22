"""Draft changes revoke approvals and repeat preparation; no external IO."""

import unittest
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

from jobpilot.domain.answers import Known, PlanItem
from jobpilot.domain.state_machine import GateContext, can_transition
from jobpilot.repository.automation import AutomationRepository
from jobpilot.repository.cv import CvRepository
from jobpilot.repository.forms import FormsRepository
from jobpilot.repository.reporting import ReportingRepository
from jobpilot.service import Services
from jobpilot.service.cv import CvService
from jobpilot.service.forms import FormsService


class DraftLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app: Any = MagicMock(spec=Services)
        self.app.db = MagicMock()
        self.job: dict[str, Any] = {
            "status": "awaiting_approval",
            "company_slug": "co",
            "company_name": "Co",
            "role": "Engineer",
            "location": "US",
            "apply_url": "https://example.test/apply",
            "url": "https://example.test/apply",
            "cv_mode": "base",
            "cv_track_id": "base",
            "cv_version_id": 1,
        }
        self.form: dict[str, Any] = {
            "inventory": [{"key": "q", "label": "Why this company?", "type": "text"}],
            "plan": [asdict(PlanItem("q", "Before", "answer:job:j"))],
            "missing": [],
            "fill_done_at": "now",
            "verified_at": "now",
            "draft_hash": "old",
            "current_page": 0,
            "revision": 0,
            "pages_complete": True,
        }
        self.approvals = ["old"]
        self.app.job.return_value = self.job
        self.app._live.return_value = {"status": "preparing", "waiting_on": "you", "archived_at": None}
        self.app.form.return_value = self.form
        self.app.submit_clicked.return_value = False
        self.app.load_known.return_value = Known()
        self.app.verification_gates.return_value = GateContext(True, True, True)
        self.app.transition.side_effect = self.transition
        self.service = FormsService(self.app)
        self.repo: Any = MagicMock(spec=FormsRepository)
        self.service.repository = self.repo
        self.repo.update_job_forms_for_invalidate_form.side_effect = self.invalidate
        self.repo.revoke_submit_approvals.side_effect = self.revoke
        self.repo.update_job_forms_for_record_verification.side_effect = self.verify
        self.repo.update_job_forms_for_make_plan.side_effect = self.plan
        self.app.invalidate_form.side_effect = self.service.invalidate_form

    def transition(self, job_id: str, status: str, gates: GateContext | None = None) -> None:
        self.assertTrue(can_transition(self.job["status"], status, gates).ok)
        self.job["status"] = status

    def invalidate(self, reset: bool, reset_2: bool, revision: int, job_id: str) -> int:
        changed = any(self.form[k] for k in ("fill_done_at", "verified_at", "draft_hash")) or reset or revision
        self.form.update(fill_done_at=None, verified_at=None, draft_hash=None)
        self.form["revision"] += revision
        if reset:
            self.form.update(plan=None, missing=[])
        return int(bool(changed))

    def revoke(self, job_id: str) -> int:
        count = len(self.approvals)
        self.approvals.clear()
        return count

    def verify(self, draft: str | None, draft_2: str | None, job_id: str) -> int:
        self.form.update(draft_hash=draft, verified_at="now" if draft else None)
        return 1

    def plan(self, plan: Any, missing: Any, job_id: str) -> int:
        self.form.update(plan=plan.obj, missing=missing.obj)
        return 1

    def assert_invalidated(self) -> None:
        self.assertEqual(self.job["status"], "preparing")
        self.assertIsNone(self.form["draft_hash"])
        self.assertIsNone(self.form["verified_at"])
        self.assertEqual(self.approvals, [])
        self.assertIn(call("j", "approval_revoked", "system"), self.app.event.call_args_list)

    def test_base_cv_change_revokes_and_resumes_then_verification_can_make_ready(self) -> None:
        cv = CvService(self.app)
        repo: Any = MagicMock(spec=CvRepository)
        cv.repository = repo
        repo.find_cv_tracks_for_cv_use_base.return_value = {"vid": 2, "sha256": "new-cv"}
        self.app.cfg = MagicMock(vault=Path("/unused"))
        self.app.store = MagicMock()
        with patch("jobpilot.service.cv.job_dir_for", return_value=Path("/unused")):
            cv.cv_use_base("j")
        self.assert_invalidated()
        self.app.resume.assert_called_once_with("j")
        self.service.record_verification("j", "new", {"ok": True})
        self.assertEqual(self.job["status"], "ready")
        self.assertEqual(self.form["draft_hash"], "new")
        self.assertEqual(self.approvals, [])

    def test_identical_base_cv_keeps_live_approval_without_side_effects(self) -> None:
        cv = CvService(self.app)
        repo: Any = MagicMock(spec=CvRepository)
        cv.repository = repo
        repo.find_cv_tracks_for_cv_use_base.return_value = {"vid": 1, "sha256": "same"}
        self.assertEqual(cv.cv_use_base("j"), {"sha": "same"})
        self.app.invalidate_form.assert_not_called()
        repo.update_jobs_for_cv_use_base.assert_not_called()
        self.app.event.assert_not_called()
        self.app.queue_cv_job.assert_not_called()
        self.app.resume.assert_not_called()
        self.app.project.assert_not_called()
        self.assertEqual(self.approvals, ["old"])

    def test_changed_plan_is_locked_and_revokes_before_write(self) -> None:
        order = MagicMock()
        order.attach_mock(self.repo.lock_job, "lock")
        order.attach_mock(self.app.form, "form")
        order.attach_mock(self.app.invalidate_form, "invalidate")
        order.attach_mock(self.repo.update_job_forms_for_make_plan, "write")
        with patch("jobpilot.service.forms.build_plan", return_value=([PlanItem("q", "After", "profile")], [])):
            self.service.make_plan("j")
        self.assert_invalidated()
        calls = [c[0] for c in order.mock_calls]
        self.assertLess(calls.index("lock"), calls.index("form"))
        self.assertLess(calls.index("invalidate"), calls.index("write"))
        self.assertEqual(self.form["plan"][0]["value"], "After")

    def test_identical_plan_keeps_verification_and_approval(self) -> None:
        with patch("jobpilot.service.forms.build_plan", return_value=([PlanItem("q", "Before", "answer:job:j")], [])):
            self.service.make_plan("j")
        self.app.invalidate_form.assert_not_called()
        self.repo.update_job_forms_for_make_plan.assert_not_called()
        self.app.event.assert_not_called()
        self.assertEqual(self.approvals, ["old"])

    def test_identical_free_text_answer_is_no_op_after_question_normalization(self) -> None:
        self.app.load_known.return_value = Known(answers={"why this company": {"job:j": "Before"}})
        self.service.set_job_answers("j", [("q", "Before", "job:j")])
        self.assert_answers_unchanged()

    def assert_answers_unchanged(self) -> None:
        self.app.answer_set.assert_not_called()
        self.app.profile_set.assert_not_called()
        self.app.invalidate_form.assert_not_called()
        self.app.resume.assert_not_called()
        self.app.event.assert_not_called()
        self.assertEqual(self.approvals, ["old"])

    def test_identical_canonical_profile_answer_is_no_op(self) -> None:
        self.form["inventory"] = [{"key": "q", "label": "Willing to relocate?", "options": ["Yes", "No"]}]
        self.app.load_known.return_value = Known(profile={"willing_relocate": "Yes"})
        self.service.set_job_answers("j", [("q", "yes", "global")])
        self.assert_answers_unchanged()

    def test_identical_contextual_answer_uses_job_scope_and_is_no_op(self) -> None:
        self.form["inventory"] = [{"key": "q", "label": "Previously worked at this company?"}]
        self.app.load_known.return_value = Known(answers={"previously worked at this company": {"job:j": "No"}})
        self.service.set_job_answers("j", [("q", "no", "global")])
        self.assert_answers_unchanged()

    def test_site_option_is_normalized_before_persisted_comparison(self) -> None:
        self.form["inventory"][0]["options"] = ["Engineering", "Sales"]
        self.app.load_known.return_value = Known(answers={"why this company": {"global": "Engineering"}})
        self.service.set_job_answers("j", [("q", "engineering", "global")])
        self.assert_answers_unchanged()

    def test_legacy_persisted_option_spelling_is_normalized_too(self) -> None:
        self.form["inventory"][0]["options"] = ["Engineering", "Sales"]
        self.app.load_known.return_value = Known(answers={"why this company": {"global": "engineering"}})
        self.service.set_job_answers("j", [("q", "engineering", "global")])
        self.assert_answers_unchanged()

    def test_changed_answer_revokes_and_resumes_once(self) -> None:
        self.service.set_job_answers("j", [("q", "After", "job:j")])
        self.assert_invalidated()
        self.assertIsNotNone(self.form["plan"])  # resume validates missing answers before resetting the plan
        self.app.answer_set.assert_called_once_with("Why this company?", "After", "job:j", "user (dashboard)", "j")
        self.app.resume.assert_called_once_with("j")

    def test_saving_without_resume_stores_the_answer_and_waits_for_the_owner(self) -> None:
        self.service.set_job_answers("j", [("q", "After", "job:j")], resume=False)
        self.assert_invalidated()
        self.assertIsNotNone(self.form["plan"])  # kept: the job stays parked so its tab is not closed
        self.app.answer_set.assert_called_once_with("Why this company?", "After", "job:j", "user (dashboard)", "j")
        self.app.resume.assert_not_called()
        self.app.event.assert_any_call("j", "answers_saved", "user", {"count": 1})

    def test_saving_an_unchanged_answer_without_resume_records_nothing(self) -> None:
        self.app.load_known.return_value = Known(answers={"why this company": {"job:j": "Before"}})
        self.service.set_job_answers("j", [("q", "Before", "job:j")], resume=False)
        self.assert_answers_unchanged()

    def test_skipping_stores_a_job_scoped_marker_without_option_validation(self) -> None:
        from jobpilot.domain.answers import SKIPPED

        self.form["inventory"][0]["options"] = ["French", "Arabic"]
        self.service.set_job_answers("j", [("q", SKIPPED, "job:j")], resume=False)
        self.app.answer_set.assert_called_once_with("Why this company?", SKIPPED, "job:j", "user (dashboard)", "j")
        self.app.resume.assert_not_called()

    def test_invalid_answer_batch_has_no_partial_writes(self) -> None:
        self.form["inventory"].append({"key": "r", "label": "Willing to relocate?"})
        with self.assertRaises(ValueError):
            self.service.set_job_answers("j", [("q", "After", "job:j"), ("r", "invalid", "global")])
        self.assert_answers_unchanged()

    def test_inventory_replacement_revokes_even_before_upsert_clears_hash(self) -> None:
        self.service.set_inventory("j", [{"key": "q", "label": "New question", "type": "text"}])
        self.assert_invalidated()
        self.repo.insert_job_forms_for_set_inventory.assert_called_once()

    def test_verification_drift_revokes_and_passes_through_preparation(self) -> None:
        self.service.record_verification("j", "changed", {"ok": True})
        self.assertEqual(self.approvals, [])
        self.assertEqual(
            self.app.transition.call_args_list, [call("j", "preparing"), call("j", "ready", GateContext(True, True, True))]
        )
        self.assertEqual(self.form["draft_hash"], "changed")

    def test_same_hash_verification_preserves_approval(self) -> None:
        self.service.record_verification("j", "old", {"ok": True})
        self.app.invalidate_form.assert_not_called()
        self.app.transition.assert_not_called()
        self.assertEqual(self.approvals, ["old"])

    def test_failed_verification_revokes_approval(self) -> None:
        self.service.record_verification("j", None, {"ok": False})
        self.assert_invalidated()

    def test_already_invalid_draft_still_revokes_leftover_approval(self) -> None:
        self.form.update(fill_done_at=None, verified_at=None, draft_hash=None)
        self.service.invalidate_form("j")
        self.assert_invalidated()
        self.assertNotIn(call("j", "draft_invalidated", "system"), self.app.event.call_args_list)

    def test_submit_fence_refuses_all_invalidation_and_replanning(self) -> None:
        self.app.submit_clicked.return_value = True
        for operation in (self.service.invalidate_form, self.service.make_plan):
            with self.subTest(operation=operation), self.assertRaises(PermissionError):
                operation("j")
        self.repo.revoke_submit_approvals.assert_not_called()
        self.repo.update_job_forms_for_invalidate_form.assert_not_called()


class LifecycleSqlTests(unittest.TestCase):
    def test_revocation_only_deletes_unconsumed_submit_approvals_for_job(self) -> None:
        db = MagicMock()
        FormsRepository(db).revoke_submit_approvals("j")
        sql, params = db.run.call_args.args
        self.assertEqual(sql, "DELETE FROM approvals WHERE job_id=%s AND kind='submit' AND consumed_at IS NULL")
        self.assertEqual(params, ("j",))

    def test_retired_worker_rows_are_excluded_from_every_heartbeat_reader(self) -> None:
        db = MagicMock()
        db.all.return_value = []
        AutomationRepository(db).list_node_liveness(20, 300)
        AutomationRepository(db).list_browser_online_nodes(20)
        ReportingRepository(db).list_fresh_worker_processes(20)
        for query in db.all.call_args_list:
            self.assertIn("name !~ '^worker-[0-9]+$'", query.args[0])
        migration = Path(__file__).resolve().parents[1] / "migrations/0018_drop_retired_worker_rows.sql"
        self.assertIn("DELETE FROM worker_processes WHERE name ~ '^worker-[0-9]+$';", migration.read_text())

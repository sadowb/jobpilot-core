"""Owner-submitted recording (ADR 0026); mocked services/repositories only."""

import unittest
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from jobpilot.repository.approvals import ApprovalsRepository
from jobpilot.service import Services
from jobpilot.service.approvals import ApprovalsService


class OwnerSubmittedTests(unittest.TestCase):
    """ADR 0026: the owner submits in Neo and says so; no agent click is ever authorized."""

    def setUp(self) -> None:
        self.app: Any = MagicMock(spec=Services)
        self.app.db = MagicMock()
        self.app.form.return_value = {"draft_hash": "draft-one", "verified_at": datetime.now(UTC), "pages_complete": True}
        self.app.job.return_value = {"status": "awaiting_approval", "cv_version_id": 4}
        self.app.submit_clicked.return_value = False
        self.service = ApprovalsService(self.app)
        self.repository: Any = MagicMock(spec=ApprovalsRepository)
        self.service.repository = self.repository
        self.repository.find_unconsumed_submit_approval.return_value = None
        self.repository.find_applications_for_record_submitted.return_value = None

    def test_records_the_draft_the_owner_saw_the_click_fence_and_the_transition(self) -> None:
        self.assertTrue(self.service.owner_submitted("j1", user_session=True, confirmation_seen=True))
        self.repository.insert_approvals_for_approve_submit.assert_called_once_with("j1", "draft-one", "owner-submitted")
        kinds = [c.args[1] for c in self.app.event.call_args_list]
        self.assertEqual(kinds, ["approval", "submit_clicked", "confirmation"])
        self.assertEqual(self.app.event.call_args_list[1].args[2], "user")
        (job, to, context), kwargs = self.app.transition.call_args
        self.assertEqual((job, to, kwargs["actor"]), ("j1", "submitted", "user"))
        self.assertEqual(context.confirmation, "live-verified")
        self.assertEqual(context.approval_for_draft_hash, context.current_draft_hash)
        self.repository.insert_applications_for_record_submitted.assert_called_once()
        self.repository.update_approvals_for_record_submitted.assert_called_once_with("j1")
        self.app.enqueue.assert_not_called()  # nothing is dispatched to an agent

    def test_no_confirmation_page_is_owner_confirmed_and_says_so(self) -> None:
        self.service.owner_submitted("j1", user_session=True, confirmation_seen=False)
        args = self.repository.insert_applications_for_record_submitted.call_args.args
        self.assertEqual(args[2], "user-confirmed")
        self.assertIn("no confirmation page was seen", args[3])

    def test_is_idempotent_once_the_application_exists(self) -> None:
        self.repository.find_applications_for_record_submitted.return_value = {"x": 1}
        self.assertFalse(self.service.owner_submitted("j1", user_session=True))
        self.app.transition.assert_not_called()
        self.app.event.assert_not_called()
        self.repository.insert_applications_for_record_submitted.assert_not_called()

    def test_requires_a_user_session(self) -> None:
        with self.assertRaises(PermissionError):
            self.service.owner_submitted("j1", user_session=False)
        self.app.transition.assert_not_called()

    def test_refused_unless_a_prepared_draft_is_waiting(self) -> None:
        for job, form in (
            ({"status": "preparing"}, {"draft_hash": "d"}),
            ({"status": "awaiting_approval"}, None),
            ({"status": "awaiting_approval"}, {"draft_hash": None}),
        ):
            with self.subTest(job=job, form=form):
                self.app.job.return_value = {**job, "cv_version_id": 1}
                self.app.form.return_value = form
                with self.assertRaises(PermissionError):
                    self.service.owner_submitted("j1", user_session=True)
        self.app.transition.assert_not_called()

    def test_an_existing_unconsumed_approval_for_the_same_draft_is_reused(self) -> None:
        self.repository.find_unconsumed_submit_approval.return_value = {"id": 9}
        self.service.owner_submitted("j1", user_session=True)
        self.repository.insert_approvals_for_approve_submit.assert_not_called()

    def test_an_existing_click_fence_is_not_duplicated(self) -> None:
        self.app.submit_clicked.return_value = True
        self.service.owner_submitted("j1", user_session=True)
        self.assertNotIn("submit_clicked", [c.args[1] for c in self.app.event.call_args_list])

    def test_lookup_is_bound_to_hash_and_unconsumed_rows(self) -> None:
        db = MagicMock()
        ApprovalsRepository(db).find_unconsumed_submit_approval("j1", "draft-one")
        sql, params = db.one.call_args.args
        self.assertEqual(params, ("j1", "draft-one"))
        self.assertIn("draft_hash=%s", sql)
        self.assertIn("consumed_at IS NULL", sql)
        self.assertIn("kind='submit'", sql)

    def test_submission_still_consumes_all_unconsumed_submit_approvals(self) -> None:
        db = MagicMock()
        ApprovalsRepository(db).update_approvals_for_record_submitted("j1")
        sql, params = db.run.call_args.args
        self.assertEqual(params, ("j1",))
        self.assertIn("consumed_at IS NULL", sql)
        self.assertNotIn("draft_hash", sql)

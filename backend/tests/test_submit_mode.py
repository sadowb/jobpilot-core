from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock

from jobpilot.config import RuntimeSettings
from jobpilot.service import Services
from jobpilot.service.jobs import JobsService
from jobpilot.service.submit_mode import SubmitModeService


class DecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc: Any = MagicMock(spec=Services)
        self.svc.jobs = MagicMock()
        self.job: dict[str, Any] = {"board_id": "greenhouse", "submit_mode": None, "try_questions_anyway": False}
        self.events: set[str] = set()
        self.settings = RuntimeSettings()
        self.svc.job.side_effect = lambda _: self.job.copy()
        self.svc.runtime_settings.side_effect = lambda: self.settings
        self.svc.board_policy.return_value = "auto"
        self.svc.jobs.repository.find_job_events_for_kind.side_effect = lambda _, kind: {"x": 1} if kind in self.events else None
        self.mode = SubmitModeService(self.svc)

    def test_default_is_review(self) -> None:
        self.assertEqual(RuntimeSettings().submitMode, "review")
        decision = self.mode.decide("j")
        self.assertEqual((decision.mode, decision.reason), ("review", None))

    def test_global_auto_applies(self) -> None:
        self.settings = RuntimeSettings(submitMode="auto")
        self.assertEqual(self.mode.decide("j").mode, "auto")

    def test_job_override_beats_the_global_toggle_in_both_directions(self) -> None:
        self.settings = RuntimeSettings(submitMode="auto")
        self.job["submit_mode"] = "review"
        decision = self.mode.decide("j")
        self.assertEqual(decision.mode, "review")
        self.assertEqual(decision.reason, "This job is set to review.")
        self.settings = RuntimeSettings(submitMode="review")
        self.job["submit_mode"] = "auto"
        self.assertEqual(self.mode.decide("j").mode, "auto")

    def test_each_forced_review_reason_overrides_auto_and_is_named(self) -> None:
        self.settings = RuntimeSettings(submitMode="auto")
        cases: list[tuple[str, Any]] = [
            ("board", lambda: setattr(self.svc.board_policy, "return_value", "review")),
            ("answers", lambda: self.job.update(try_questions_anyway=True)),
            ("cv", lambda: self.events.add("cv_select_fallback")),
            ("mail", lambda: self.events.add("email_verification_failed")),
        ]
        reasons = set()
        for name, arrange in cases:
            with self.subTest(name):
                self.setUp()
                self.settings = RuntimeSettings(submitMode="auto")
                self.job["submit_mode"] = "auto"  # an explicit per-job auto cannot override a forced fallback
                arrange()
                decision = self.mode.decide("j")
                self.assertEqual(decision.mode, "review")
                self.assertEqual(decision.requested, "auto")
                self.assertTrue(decision.reason)
                reasons.add(decision.reason)
        self.assertEqual(len(reasons), 4)

    def test_setting_validates_and_is_live(self) -> None:
        from pydantic import ValidationError

        from jobpilot.service._support import LIVE_SETTINGS

        with self.assertRaises(ValidationError):
            RuntimeSettings(submitMode="wild")
        self.assertIn("submitMode", LIVE_SETTINGS)


class OverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app: Any = MagicMock(spec=Services)
        self.app.db = MagicMock()
        self.app._live.return_value = {"status": "ready"}
        self.app.job.return_value = {"submit_mode": None}
        self.app.submit_clicked.return_value = False
        self.service = JobsService(self.app)
        self.repo: Any = MagicMock()
        self.service.repository = self.repo

    def test_change_is_stored_and_recorded_as_a_user_event(self) -> None:
        self.service.set_submit_mode("j", "auto")
        self.repo.update_jobs_for_submit_mode.assert_called_once_with("j", "auto")
        self.app.event.assert_called_once_with("j", "mode_changed", "user", {"scope": "job", "from": None, "to": "auto"})

    def test_unchanged_override_is_a_noop(self) -> None:
        self.app.job.return_value = {"submit_mode": "review"}
        self.service.set_submit_mode("j", "review")
        self.repo.update_jobs_for_submit_mode.assert_not_called()
        self.app.event.assert_not_called()

    def test_no_change_after_submission_begins_or_on_a_finished_job(self) -> None:
        self.app.submit_clicked.return_value = True
        with self.assertRaises(PermissionError):
            self.service.set_submit_mode("j", "auto")
        self.app.submit_clicked.return_value = False
        self.app._live.return_value = {"status": "submitted"}
        with self.assertRaises(PermissionError):
            self.service.set_submit_mode("j", "auto")
        self.repo.update_jobs_for_submit_mode.assert_not_called()


if __name__ == "__main__":
    unittest.main()

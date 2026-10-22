"""Browser lifecycle and approved-draft recovery; no browser or database starts."""

import unittest
from contextlib import nullcontext
from typing import Any
from unittest.mock import MagicMock, patch

from jobpilot.browser.tabs import TabBinder
from jobpilot.repository.browser import BrowserRepository
from jobpilot.service import Services
from jobpilot.service.browser import BrowserService


class BrowserLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app: Any = MagicMock(spec=Services)
        self.app.db = MagicMock()
        self.app._close_backoff = {}
        self.app.node = "mac"
        self.app.job.return_value = {"status": "awaiting_approval", "waiting_on": "you"}
        self.service = BrowserService(self.app)
        self.repository: Any = MagicMock(spec=BrowserRepository)
        self.service.repository = self.repository
        self.repository.cleanup_lock.side_effect = lambda _: nullcontext(True)
        self.repository.find_parked_for_owner.return_value = False
        self.repository.find_browser_sessions_for_close_browser_locked.return_value = {
            "job_id": "fixture",
            "node": "mac",
            "target_id": "T-fixture",
        }
        self.binder: Any = MagicMock(spec=TabBinder)
        self.service._tabs = self.binder
        self.binder.release.return_value = True
        self.app._close_browser_locked.side_effect = self.service._close_browser_locked
        self.app.close_browser.side_effect = self.service.close_browser

    def reveal_callback(self) -> Any:
        """The after-commit hook the early-return paths register: it expands the tab group, never closes."""
        self.app.db.defer.assert_called_once()
        self.repository.find_session_tab.return_value = {"node": "mac", "target_id": "T-fixture", "close_requested": False}
        return self.app.db.defer.call_args.args[0]

    def test_approval_wait_preserves_the_filled_browser(self) -> None:
        self.service._defer_close("fixture")
        self.repository.update_browser_sessions_for_defer_close.assert_not_called()
        self.app.close_browser.assert_not_called()
        self.reveal_callback()()
        self.binder.reveal.assert_called_once_with("T-fixture")
        self.binder.release.assert_not_called()

    def test_owner_question_wait_preserves_the_filled_browser(self) -> None:
        self.app.job.return_value = {"status": "preparing", "waiting_on": "you"}
        self.repository.find_parked_for_owner.return_value = True
        self.service._defer_close("fixture")
        self.repository.find_parked_for_owner.assert_called_once_with("fixture")
        self.repository.update_browser_sessions_for_defer_close.assert_not_called()
        self.reveal_callback()()
        self.binder.reveal.assert_called_once_with("T-fixture")

    def test_other_waits_request_cleanup_after_commit(self) -> None:
        self.app.job.return_value = {"status": "applied", "waiting_on": "you"}
        self.app.db.defer.return_value = True
        self.service._defer_close("fixture")
        self.repository.update_browser_sessions_for_defer_close.assert_called_once_with("fixture")
        self.app.close_browser.assert_not_called()
        callback = self.app.db.defer.call_args.args[0]
        callback()
        self.binder.release.assert_called_once_with("T-fixture")
        self.repository.delete_browser_sessions_for_close_browser_locked.assert_called_once_with("fixture", "mac")

    def test_close_failure_remains_tracked_and_retries_after_backoff(self) -> None:
        with patch("jobpilot.service.browser.time.monotonic") as clock:
            self.binder.release.side_effect = [False, True]
            clock.return_value = 10
            self.service.close_browser("fixture")
            self.repository.delete_browser_sessions_for_close_browser_locked.assert_not_called()
            self.app.event.assert_not_called()
            clock.return_value = 11
            self.service.close_browser("fixture")
            self.assertEqual(self.binder.release.call_count, 1)
            clock.return_value = 21
            self.service.close_browser("fixture")
        self.assertEqual(self.binder.release.call_count, 2)
        self.repository.delete_browser_sessions_for_close_browser_locked.assert_called_once_with("fixture", "mac")
        self.app.event.assert_called_once_with("fixture", "browser_closed", "system")
        self.assertEqual(self.app._close_backoff, {})

    def test_a_close_that_goes_through_the_neo_binder_not_a_missing_cli(self) -> None:
        # The old path shelled out to a binary that the container image does not have, swallowed the error and never
        # closed a tab. The binder is the only closer now.
        self.service.close_browser("fixture")
        self.binder.release.assert_called_once_with("T-fixture")

    def test_running_model_prevents_cleanup_until_its_lock_is_released(self) -> None:
        self.repository.cleanup_lock.side_effect = lambda _: nullcontext(False)
        self.service.close_browser("fixture")
        self.binder.release.assert_not_called()
        self.repository.find_browser_sessions_for_close_browser_locked.assert_not_called()

    def test_cleanup_rechecks_eligibility_before_closing(self) -> None:
        self.repository.find_browser_sessions_for_close_browser_locked.return_value = None
        self.service.close_browser("fixture")
        self.binder.release.assert_not_called()
        self.repository.delete_browser_sessions_for_close_browser_locked.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""No run is released for retry silently: every requeue records its reason, and the idle reason shows it."""

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from celery.exceptions import SoftTimeLimitExceeded
from test_dispatch_admission import healthy

from jobpilot.browser.neo import BrowserUnavailable
from jobpilot.browser.tabs import BrowserOffline
from jobpilot.config import RuntimeSettings, Settings
from jobpilot.repository.reporting import ReportingRepository
from jobpilot.service import Services
from jobpilot.service.reporting import ReportingService
from jobpilot.service.submit_mode import ModeDecision
from jobpilot.worker.runner import REFUND_STREAK_LIMIT, JobRunner, SessionLost


def requeue_events(app: Any) -> list[dict[str, Any]]:
    return [c.args[3] for c in app.event.call_args_list if c.args[1] == "run_requeued"]


class RunnerRecordsEveryRequeue(unittest.TestCase):
    def setUp(self) -> None:
        self.app: Any = MagicMock(spec=Services)
        self.app.db = MagicMock()
        self.app.cfg = Settings(_env_file=None)
        self.app.job.return_value = {"source": "platform", "status": "preparing"}
        self.app.submit_mode = MagicMock()
        self.app.submit_mode.decide.return_value = ModeDecision("review", "review", None, None)
        self.app.tab_binder.return_value.online.return_value = False  # BrowserOffline below means a real outage
        self.driver = MagicMock()
        self.runner = JobRunner(self.app.cfg, self.app, self.driver)
        self.stop = MagicMock(is_set=MagicMock(return_value=False))

    def test_each_silent_exception_path_now_records_its_reason(self) -> None:
        for failure, reason, refund in (
            (SessionLost("tab"), "tab_lost", False),
            (BrowserOffline("off"), "browser_offline", True),
            (SoftTimeLimitExceeded(), "time_limit", False),
            (PermissionError("job lease lost"), "lease_lost", False),
        ):
            with self.subTest(reason=reason):
                self.setUp()
                with patch.object(self.runner, "_run_direct", side_effect=failure):
                    end = self.runner.run("j", "t", self.stop)
                self.assertEqual((end.outcome, end.detail, end.refund_attempt), ("queued", reason, refund))
                (event,) = requeue_events(self.app)
                self.assertEqual(event["reason"], reason)
                self.assertGreaterEqual(event["retryAfter"], 5)

    def test_browser_offline_while_neo_answers_is_a_page_error_that_costs_an_attempt(self) -> None:
        self.app.tab_binder.return_value.online.return_value = True
        cause = BrowserUnavailable("Invalid Neo page data")
        failure = BrowserOffline("shared browser is not available")
        failure.__cause__ = cause
        with patch.object(self.runner, "_run_direct", side_effect=failure):
            end = self.runner.run("j", "t", self.stop)
        self.assertEqual((end.detail, end.refund_attempt), ("browser_page_error", False))
        (event,) = requeue_events(self.app)
        self.assertEqual(event["detail"], "BrowserUnavailable: Invalid Neo page data")

    def test_an_unanswerable_neo_probe_counts_as_an_outage(self) -> None:
        self.app.tab_binder.return_value.online.side_effect = RuntimeError("boom")
        with patch.object(self.runner, "_run_direct", side_effect=BrowserOffline("x")):
            end = self.runner.run("j", "t", self.stop)
        self.assertEqual((end.detail, end.refund_attempt), ("browser_offline", True))

    def test_a_refunded_requeue_streak_stops_refunding_at_the_limit(self) -> None:
        for streak, refunded in ((REFUND_STREAK_LIMIT - 1, True), (REFUND_STREAK_LIMIT, False)):
            with self.subTest(streak=streak):
                self.setUp()
                self.runner.repository = MagicMock()
                self.runner.repository.count_requeues_since_progress.return_value = streak
                with patch.object(self.runner, "_run_direct", side_effect=BrowserOffline("x")):
                    end = self.runner.run("j", "t", self.stop)
                self.assertEqual(end.refund_attempt, refunded)
                self.runner.repository.count_requeues_since_progress.assert_called_once_with("j", "browser_offline")
                (event,) = requeue_events(self.app)
                self.assertEqual("refundStopped" in event, not refunded)

    def test_owner_stop_and_drain_are_not_noise(self) -> None:
        self.stop.is_set.return_value = True
        end = self.runner.run("j", "t", self.stop)
        self.assertEqual((end.outcome, end.detail), ("queued", "draining"))
        self.assertEqual(requeue_events(self.app), [])

    def test_driver_refusal_happens_before_any_neo_tab_is_opened(self) -> None:
        self.app.job.return_value = {"source": "platform", "status": "preparing", "waiting_on": "agent", "board_id": "custom"}
        self.app.board_policy.return_value = "auto"
        self.app.submit_clicked.return_value = False
        self.app.browser_session_present.return_value = False
        self.driver.preflight.side_effect = ValueError("the browser agent runs only claude-haiku-5-5")
        with (
            patch("jobpilot.worker.runner.PreflightRepository") as preflight,
            patch("jobpilot.worker.runner.DirectService"),
            patch("jobpilot.worker.runner.BulkFillService") as bulk,
        ):
            preflight.return_value.done.return_value = True
            self.runner.run("j", "t", self.stop)
        self.driver.preflight.assert_called_once()
        self.app.ensure_tab.assert_not_called()
        bulk.assert_not_called()
        self.driver.run_direct.assert_not_called()


class IdleReasonShowsRequeues(unittest.TestCase):
    def setUp(self) -> None:
        app: Any = MagicMock(spec=Services)
        app.db = MagicMock()
        app.runtime_settings.return_value = RuntimeSettings(workerConcurrency=3, maxOpenBrowsers=6)
        app.automation = MagicMock()
        app.automation.open_browsers.return_value = 0
        app.automation.admission_cap.return_value = 3
        self.service = ReportingService(app)
        self.repo: Any = MagicMock(spec=ReportingRepository)
        self.service.repository = self.repo
        self.repo.find_automation_for_automation_status.return_value = {"enabled": True}
        self.repo.list_fresh_worker_processes.return_value = healthy()
        self.repo.list_active_workers.return_value = []
        self.repo.count_cap_blocked_jobs.return_value = 0
        self.repo.find_task_outcomes_today.return_value = {}
        self.repo.queue_depth.return_value = 1
        self.repo.usage_windows.return_value = []
        self.repo.recent_failed_runs.return_value = []

    def test_recent_requeues_become_the_idle_reason(self) -> None:
        self.repo.find_recent_requeue.return_value = {"reason": "browser_offline", "n": 12}
        reason = self.service.automation_status()["idleReason"]
        self.assertIn("12", reason)
        self.assertIn("Neo was unreachable", reason)

    def test_unknown_reason_codes_still_say_something(self) -> None:
        self.repo.find_recent_requeue.return_value = {"reason": "something_new", "n": 2}
        self.assertIn("unexpected interruption", self.service.automation_status()["idleReason"])

    def test_running_work_hides_a_stale_requeue_reason(self) -> None:
        self.repo.list_active_workers.return_value = [{"worker": "t", "job": "j", "started": True}]
        self.repo.find_recent_requeue.return_value = {"reason": "tab_lost", "n": 1}
        self.assertIsNone(self.service.automation_status()["idleReason"])

    def test_no_requeues_keeps_the_idle_message(self) -> None:
        self.repo.find_recent_requeue.return_value = None
        self.assertIn("Nothing is waiting", self.service.automation_status()["idleReason"])

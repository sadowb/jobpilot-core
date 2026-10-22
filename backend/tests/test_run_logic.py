"""Run/tab regressions: all resources are mocked; no automation or submission is started."""

import json
import sqlite3
import unittest
from contextlib import nullcontext
from typing import Any
from unittest.mock import MagicMock, patch

from jobpilot.browser.neo import BrowserUnavailable, Tab
from jobpilot.browser.tabs import BrowserOffline, TabBinder
from jobpilot.config import Settings
from jobpilot.repository.automation import AutomationRepository
from jobpilot.repository.browser import BrowserRepository
from jobpilot.repository.forms import FormsRepository
from jobpilot.repository.jobs import JobsRepository
from jobpilot.repository.profile import ProfileRepository
from jobpilot.repository.reporting import ReportingRepository
from jobpilot.repository.tracker import TrackerRepository
from jobpilot.service import Services
from jobpilot.service.automation import AutomationService
from jobpilot.service.browser import BrowserService
from jobpilot.service.forms import FormsService
from jobpilot.service.jobs import JobsService
from jobpilot.service.reporting import ReportingService
from jobpilot.worker.claude import ClaudeDriver, ClaudeRunError, run_failure
from jobpilot.worker.runner import JobRunner, RunEnd


class SessionRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app: Any = MagicMock(spec=Services)
        self.app.db = MagicMock()
        self.app.node = "mac"
        self.app.cfg = Settings(_env_file=None)
        self.app.db.transaction.side_effect = lambda: nullcontext()
        self.app.browser_lock.side_effect = lambda _: nullcontext()
        self.app.job.return_value = {
            "source": "platform",
            "status": "awaiting_approval",
            "waiting_on": "agent",
            "board_id": "custom",
            "role": "Role",
            "company_name": "Co",
            "url": "https://example.test",
        }
        self.service = BrowserService(self.app)
        self.repo: Any = MagicMock(spec=BrowserRepository)
        self.service.repository = self.repo
        self.binder: Any = MagicMock(spec=TabBinder)
        self.binder.browser = MagicMock()
        self.service._tabs = self.binder
        self.binder.browser.tabs.return_value = [Tab("live", "about:blank")]
        self.session: dict[str, Any] | None = {
            "node": "mac",
            "target_id": "live",
            "close_requested": False,
        }
        self.repo.find_session_tab.side_effect = lambda *_: self.session
        self.repo.delete_legacy_session.side_effect = self.delete_session
        self.repo.delete_tabless_session.side_effect = self.delete_session
        self.app.browser_is_open.side_effect = self.service.browser_is_open
        self.app.browser_session_present.side_effect = self.service.browser_session_present
        self.app.submit_clicked.return_value = False
        self.driver: Any = MagicMock()
        self.runner = JobRunner(self.app.cfg, self.app, self.driver)

    def delete_session(self, *_: object) -> int:
        changed = int(self.session is not None)
        self.session = None
        return changed

    def test_missing_tab_is_dropped_once_and_not_counted_open(self) -> None:
        self.binder.browser.tabs.return_value = []
        self.assertFalse(self.service.browser_is_open("j"))
        self.assertFalse(self.service.browser_is_open("j"))
        self.repo.delete_tabless_session.assert_called_once_with("j", "mac", "live")
        self.app.event.assert_called_once_with("j", "browser_lost", "system", {"node": "mac", "reason": "tab gone"})

    def test_cleanup_paths_drop_legacy_rows_only_with_a_loss_event(self) -> None:
        self.app._close_backoff = {}
        for cleanup in (self.service.drop_tab, self.service._close_browser_locked):
            for node, target in ((None, "old"), ("mac", None)):
                with self.subTest(cleanup=cleanup.__name__, node=node, target=target):
                    self.session = {"node": node, "target_id": target, "close_requested": True}
                    self.repo.find_browser_sessions_for_close_browser_locked.return_value = self.session
                    self.app.event.reset_mock()
                    cleanup("j")
                    self.app.event.assert_called_once_with(
                        "j", "browser_lost", "system", {"node": "mac", "reason": "legacy session"}
                    )
        self.binder.release.assert_not_called()
        self.repo.delete_browser_sessions_for_close_browser_locked.assert_not_called()

    def test_legacy_cleanup_that_loses_a_race_emits_no_duplicate_loss(self) -> None:
        self.session = {"node": "mac", "target_id": None, "close_requested": False}
        self.repo.delete_legacy_session.side_effect = None
        self.repo.delete_legacy_session.return_value = 0
        self.assertFalse(self.service.browser_session_present("j"))
        self.app.event.assert_not_called()
        self.binder.browser.tabs.assert_not_called()

    def test_live_tab_survives_an_idle_daemon_and_never_refills(self) -> None:
        self.assertTrue(self.service.browser_is_open("j"))
        self.repo.delete_tabless_session.assert_not_called()
        self.app.event.assert_not_called()

    def test_unreachable_neo_keeps_the_row_and_requeues_without_owner_wait(self) -> None:
        self.binder.browser.tabs.side_effect = BrowserUnavailable("private diagnostic")
        with self.assertRaises(BrowserOffline):
            self.service.browser_is_open("j")
        self.repo.delete_tabless_session.assert_not_called()
        self.app.wait.assert_not_called()


class RunnerBrowserOutageTests(unittest.TestCase):
    """What the runner does when Neo or a job's tab is not there (the direct run is the only run)."""

    def setUp(self) -> None:
        self.app: Any = MagicMock(spec=Services)
        self.app.db = MagicMock()
        self.app.cfg = Settings(_env_file=None)
        self.app.db.one.return_value = {"enabled": True}
        self.app.job.return_value = {"source": "platform", "status": "preparing"}
        self.app.tab_binder.return_value.online.return_value = False  # a real outage unless a test says Neo answers
        self.runner = JobRunner(self.app.cfg, self.app, MagicMock())
        self.stop = MagicMock(is_set=MagicMock(return_value=False))

    def test_offline_neo_before_any_model_work_requeues_and_refunds_the_attempt(self) -> None:
        with patch.object(self.runner, "_run_direct", side_effect=BrowserOffline("offline")):
            self.assertEqual(self.runner.run("j", "t", self.stop), RunEnd("queued", delay=30, refund_attempt=True))
        self.app.wait.assert_not_called()

    def test_offline_neo_after_the_model_started_does_not_refund(self) -> None:
        def run(_job: str, _worker: str, _stop: Any, usage: Any) -> RunEnd:
            usage.model_started = True
            raise BrowserOffline("offline")

        with patch.object(self.runner, "_run_direct", side_effect=run):
            self.assertEqual(self.runner.run("j", "t", self.stop), RunEnd("queued", delay=30))
        self.app.wait.assert_not_called()


class UnapprovedSubmissionKillSwitchTests(unittest.TestCase):
    def test_an_unapproved_submission_report_stops_all_automation(self) -> None:
        from jobpilot.service.approvals import ApprovalsService

        app: Any = MagicMock(spec=Services)
        app.db = MagicMock()
        app.form.return_value = {"draft_hash": "d"}
        app.job.return_value = {"status": "preparing", "cv_version_id": 1}
        app.approval_for.return_value = None
        app.submit_clicked.return_value = False
        service = ApprovalsService(app)
        service.repository = MagicMock()
        service.repository.find_applications_for_record_submitted.return_value = None
        service.record_agent_submitted("j", "Thanks")
        app.set_automation.assert_called_once_with(False, actor="system", reason="unapproved_submit")
        app.wait.assert_called_once()


class IdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app: Any = MagicMock(spec=Services)
        self.app.db = MagicMock()
        self.app.submit_clicked.return_value = False
        self.app.job.return_value = {"status": "preparing"}
        self.app._live.return_value = {"status": "preparing", "waiting_on": "you", "archived_at": None}

    def test_already_invalid_draft_emits_no_write_event_on_retry(self) -> None:
        service = FormsService(self.app)
        repo: Any = MagicMock(spec=FormsRepository)
        service.repository = repo
        repo.update_job_forms_for_invalidate_form.side_effect = [1, 0]
        repo.revoke_submit_approvals.return_value = 0
        service.invalidate_form("j")
        service.invalidate_form("j")
        self.app.event.assert_called_once_with("j", "draft_invalidated", "system")
        self.app.transition.assert_not_called()

    def test_agent_edit_of_invalid_approval_still_requires_preparation(self) -> None:
        self.app.job.return_value = {"status": "awaiting_approval"}
        service = FormsService(self.app)
        repo: Any = MagicMock(spec=FormsRepository)
        service.repository = repo
        repo.update_job_forms_for_invalidate_form.return_value = 0
        repo.revoke_submit_approvals.return_value = 0
        service.invalidate_form("j", agent_edit=True)
        self.app.transition.assert_called_once_with("j", "preparing")
        self.app.event.assert_not_called()

    def test_repeated_owner_wait_has_no_duplicate_event_projection_or_close(self) -> None:
        service = JobsService(self.app)
        repo: Any = MagicMock(spec=JobsRepository)
        service.repository = repo
        repo.update_jobs_for_wait.side_effect = [1, 0]
        service.wait("j", "you", "approve submission")
        service.wait("j", "you", "approve submission")
        self.app.event.assert_called_once_with("j", "wait", "agent", {"who": "you", "reason": "approve submission"})
        self.app.project.assert_called_once_with("j")
        self.app._defer_close.assert_called_once_with("j")

    def test_resume_replans_only_when_answers_were_saved_unsent(self) -> None:
        service = JobsService(self.app)
        repo: Any = MagicMock(spec=JobsRepository)
        service.repository = repo
        self.app.required_open.return_value = []
        repo.find_unsent_answers.side_effect = [True, False]
        service.resume("j")
        self.app.invalidate_form.assert_called_once_with("j", reset_plan=True)
        self.app.invalidate_form.reset_mock()
        service.resume("j")
        self.app.invalidate_form.assert_not_called()
        self.assertEqual(repo.update_jobs_for_resume.call_count, 2)

    def test_duplicate_finish_after_lease_release_records_one_outcome(self) -> None:
        service = AutomationService(self.app)
        repo: Any = MagicMock(spec=AutomationRepository)
        service.repository = repo
        repo.has_finished_run.side_effect = [False, True]
        repo.find_task_lease.return_value = None  # agent already called jp done
        service.finish_run("j", "t", "queued", 3.0)
        service.finish_run("j", "t", "queued", 3.0)
        self.app.event.assert_called_once_with(
            "j",
            "task_finished",
            "system",
            {"task": "t", "node": None, "outcome": "queued", "seconds": 3.0, "refunded": False, "released": False},
        )
        repo.find_task_lease.assert_called_once_with("j", "t")
        self.assertEqual(repo.lock_run_for_finish.call_count, 2)
        repo.update_tasks_for_finish_run.assert_not_called()

    def test_duplicate_system_stop_emits_once_and_preserves_the_first_reason(self) -> None:
        service = AutomationService(self.app)
        repo: Any = MagicMock(spec=AutomationRepository)
        service.repository = repo
        repo.update_automation_for_set_automation.side_effect = [1, 0]
        service.set_automation(False, actor="system", reason="not_logged_in")
        service.set_automation(False, actor="system", reason="usage_limit")
        self.app.event.assert_called_once_with(
            None,
            "automation_changed",
            "system",
            {"enabled": False, "reason": "not_logged_in"},
        )
        self.app.request_dispatch.assert_not_called()

    def test_duplicate_start_has_no_event_or_dispatch_side_effect(self) -> None:
        service = AutomationService(self.app)
        repo: Any = MagicMock(spec=AutomationRepository)
        service.repository = repo
        repo.update_automation_for_set_automation.return_value = 0
        service.set_automation(True)
        self.app.event.assert_not_called()
        self.app.request_dispatch.assert_not_called()

    def test_invalidation_query_only_changes_valid_state_or_a_plan_being_reset(self) -> None:
        FormsRepository(self.app.db).update_job_forms_for_invalidate_form(True, True, 1, "j")
        sql, params = self.app.db.run.call_args.args
        self.assertIn("fill_done_at IS NOT NULL OR verified_at IS NOT NULL OR draft_hash IS NOT NULL", sql)
        self.assertIn("plan IS NOT NULL OR missing IS DISTINCT FROM '[]'::jsonb", sql)
        self.assertEqual(params, (True, True, 1, "j", 1, True))

    def test_automation_query_is_atomic_and_does_not_refresh_an_unchanged_switch(self) -> None:
        AutomationRepository(self.app.db).update_automation_for_set_automation(False)
        sql, params = self.app.db.run.call_args.args
        self.assertIn("enabled IS DISTINCT FROM %s", sql)
        self.assertEqual(params, (False, False))


class UnavailableAgentTests(unittest.TestCase):
    def test_classifications_never_include_cli_output(self) -> None:
        for status, stderr, reason in (
            (401, "secret", "not_logged_in"),
            (429, "secret", "usage_limit"),
            (None, "Not logged in: secret", "not_logged_in"),
            (None, "You've hit your usage limit: secret", "usage_limit"),
            (None, "secret", "cli_error"),
        ):
            with self.subTest(reason=reason):
                failure = run_failure(1, stderr, {"api_error_status": status})
                assert failure is not None
                self.assertTrue(failure.environment)
                self.assertEqual(failure.reason, reason)
                self.assertNotIn("secret", str(failure))
        for subtype in ("error_max_budget_usd", "error_max_turns"):
            failure = run_failure(1, "secret", {"subtype": subtype})
            assert failure is not None
            self.assertFalse(failure.environment)

    def test_missing_binary_is_an_environment_failure_without_exposing_paths(self) -> None:
        app: Any = MagicMock(spec=Services)
        cfg = Settings(_env_file=None)
        driver = ClaudeDriver(cfg, app)
        with (
            patch("jobpilot.worker.claude.Path.read_text", return_value="instructions"),
            patch("jobpilot.worker.claude.run_stream", side_effect=FileNotFoundError(2, "private path", "claude")),
            self.assertRaises(ClaudeRunError) as raised,
        ):
            driver._run(
                "j",
                "instructions",
                model="claude-haiku-5-5",
                check=lambda: None,
                session_id="00000000-0000-4000-8000-000000000001",
            )
        self.assertEqual(raised.exception.reason, "binary_missing")
        self.assertTrue(raised.exception.environment)
        self.assertNotIn("private", str(raised.exception))

    def test_missing_working_directory_does_not_claim_the_binary_is_missing(self) -> None:
        driver = ClaudeDriver(Settings(_env_file=None), MagicMock(spec=Services))
        with (
            patch("jobpilot.worker.claude.Path.read_text", return_value="instructions"),
            patch("jobpilot.worker.claude.run_stream", side_effect=FileNotFoundError(2, "secret", "/private/agent")),
            self.assertRaises(ClaudeRunError) as raised,
        ):
            driver._run(
                "j",
                "instructions",
                model="claude-haiku-5-5",
                check=lambda: None,
                session_id="00000000-0000-4000-8000-000000000001",
            )
        self.assertEqual(raised.exception.reason, "cli_error")
        self.assertNotIn("private", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    def test_idle_reason_explains_stop_using_only_known_reason_codes(self) -> None:
        app: Any = MagicMock(spec=Services)
        app.db = MagicMock()
        app.automation = MagicMock()
        service = ReportingService(app)
        repo: Any = MagicMock(spec=ReportingRepository)
        service.repository = repo
        repo.list_fresh_worker_processes.return_value = []
        repo.find_task_outcomes_today.return_value = {}
        for reason, expected in (
            ("not_logged_in", "automation stopped: agent CLI not logged in"),
            ("usage_limit", "automation stopped: agent CLI usage limit reached"),
            ("binary_missing", "automation stopped: agent CLI binary missing"),
            ("private arbitrary message", "automation stopped"),
        ):
            with self.subTest(reason=reason):
                repo.find_automation_for_automation_status.return_value = {"enabled": False, "stop_reason": reason}
                self.assertEqual(service.automation_status()["idleReason"], expected)


class LeaseReadTests(unittest.TestCase):
    def test_expired_leases_are_excluded_from_every_running_reader(self) -> None:
        db: Any = MagicMock()
        db.one.return_value = {"n": 0}
        readers = (
            lambda: TrackerRepository(db).find_tasks_for_archive("j"),
            lambda: TrackerRepository(db).find_tasks_for_merge("j"),
            lambda: AutomationRepository(db).count_active_leases(),
            lambda: AutomationRepository(db).count_open_browsers(["mac"], "mac"),
            lambda: AutomationRepository(db).count_node_browsers("mac", "j"),
            lambda: AutomationRepository(db).find_tasks_for_configure_runtime(),
            lambda: ProfileRepository(db).find_tasks_for_board_update(),
            lambda: ReportingRepository(db).list_active_workers(),
            lambda: ReportingRepository(db).find_task_outcomes_today(),
        )
        for read in readers:
            db.reset_mock()
            read()
            query = (db.one.call_args or db.all.call_args).args[0]
            self.assertIn("visible_at>now()", query)

    def test_legacy_rows_do_not_exempt_preparation_or_consume_tab_capacity(self) -> None:
        db: Any = MagicMock()
        db.one.return_value = {"n": 0}
        repository = AutomationRepository(db)
        readers = (
            lambda: repository.count_open_browsers(["mac"], "mac"),
            lambda: repository.count_node_browsers("mac", "j"),
            lambda: repository.update_tasks_for_start_run("j", "t", "mac", 600),
            lambda: repository.claim_next_task("t", 90, False, current_node="mac", session_nodes=["mac"], skip_nodes=["other"]),
        )
        for read in readers:
            db.reset_mock()
            read()
            query = db.one.call_args.args[0]
            self.assertIn("target_id IS NOT NULL", query)
            self.assertNotIn("node IS NULL OR", query)


class NodeSessionPredicateTests(unittest.TestCase):
    """Execute the session-count predicates with mixed local, remote and legacy rows; no PostgreSQL is needed."""

    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE browser_sessions(job_id TEXT, node TEXT, target_id TEXT, close_requested BOOLEAN);
            CREATE TABLE tasks(job_id TEXT, state TEXT, visible_at TEXT, started_at TEXT, leased_node TEXT, attempts INT);
            CREATE TABLE jobs(id TEXT, status TEXT, archived_at TEXT, waiting_on TEXT);
            CREATE TABLE job_events(job_id TEXT, kind TEXT);
        """)
        sessions = [
            ("local", "mac", "T-local", False),
            ("local-legacy", "mac", None, False),
            ("null-node", None, "T-legacy", False),
            ("remote", "box-2", "T-remote", False),
            ("remote-tabless", "box-2", None, False),
        ]
        self.connection.executemany("INSERT INTO browser_sessions VALUES (?, ?, ?, ?)", sessions)
        for job, *_ in sessions:
            self.connection.execute("INSERT INTO jobs VALUES (?, 'awaiting_approval', NULL, 'agent')", (job,))
            self.connection.execute("INSERT INTO tasks VALUES (?, 'queued', '2000-01-01', NULL, NULL, 0)", (job,))
        self.connection.execute("INSERT INTO jobs VALUES ('fresh', 'preparing', NULL, 'agent')")  # no browser yet
        self.connection.execute("INSERT INTO tasks VALUES ('fresh', 'queued', '2000-01-01', NULL, NULL, 0)")
        self.db: Any = MagicMock()
        self.db.one.side_effect = self.select

    def select(self, query: str, args: tuple[Any, ...]) -> Any:
        # Adapt PostgreSQL syntax only; run the repository's complete WHERE predicates unchanged.
        query = query.replace(" = ANY(%s::text[])", " IN (SELECT value FROM json_each(%s))")
        query = query.replace("::int", "").replace("now()", "CURRENT_TIMESTAMP").replace("%s", "?")
        params = tuple(json.dumps(arg) if isinstance(arg, list) else arg for arg in args)
        return self.connection.execute(query, params).fetchone()

    def test_capacity_counts_remote_rows_and_only_local_tab_backed_rows(self) -> None:
        repository = AutomationRepository(self.db)
        self.assertEqual(repository.count_open_browsers(["mac", "box-2"], "mac"), 3)
        self.assertEqual(repository.count_node_browsers("mac", "excluded"), 1)
        self.assertEqual(repository.count_open_browsers(["mac"], "mac"), 1)  # remote past takeover
        self.connection.execute("UPDATE browser_sessions SET close_requested=true WHERE job_id='local'")
        self.assertEqual(repository.count_open_browsers(["mac", "box-2"], "mac"), 2)
        self.assertEqual(repository.count_node_browsers("mac", "excluded"), 0)

    def test_reporting_does_not_classify_another_nodes_rows_as_legacy(self) -> None:
        repository = ReportingRepository(self.db)
        self.assertEqual(repository.count_parked_forms("mac"), 3)
        # Only the preparing job without a browser is held back by the cap; awaiting_approval jobs wait for the owner.
        self.assertEqual(repository.count_cap_blocked_jobs("mac"), 1)
        self.assertEqual(repository.count_parked_forms("box-2"), 3)
        self.assertEqual(repository.count_cap_blocked_jobs("box-2"), 1)

    def test_dispatch_applies_node_aware_filter_to_reuse_hold_and_routing(self) -> None:
        db: Any = MagicMock()
        AutomationRepository(db).claim_next_task(
            "t", 90, False, current_node="mac", session_nodes=["mac", "box-2"], skip_nodes=["box-3"]
        )
        query, params = db.one.call_args.args
        self.assertEqual(query.count("(s.node<>%s OR s.target_id IS NOT NULL)"), 4)
        self.assertEqual(
            params, ("t", None, 90, 1, False, True, ["mac", "box-2"], "mac", ["box-3"], "mac", "mac", ["mac", "box-2"], "mac")
        )
        self.assertEqual(query.count("%s"), len(params))

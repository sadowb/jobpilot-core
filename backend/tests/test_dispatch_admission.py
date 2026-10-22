"""Dispatch admission, run leases, automation switch and status reporting; no database, broker or network starts."""

import unittest
import uuid
from contextlib import nullcontext
from typing import Any
from unittest.mock import MagicMock, call

from jobpilot.config import RuntimeSettings
from jobpilot.domain.next_step import JobFacts
from jobpilot.repository.automation import AutomationRepository
from jobpilot.repository.reporting import ReportingRepository
from jobpilot.service import Services
from jobpilot.service.automation import CAP_RETRY_SECONDS, DISPATCH_RETRY_SECONDS, AutomationService
from jobpilot.service.reporting import ReportingService
from jobpilot.worker.names import DISPATCH_LEASE_SECONDS, RUN_LEASE_SECONDS

IDLE_TEXT = "Nothing is waiting for the agent right now."
OPEN_FACTS: dict[str, Any] = {
    "waiting_on": "agent", "meta_captured": True, "has_inventory": True, "has_plan": True, "missing_answers": 0,
    "cv_attached": True, "fill_done": False, "verified": False, "submit_clicked": False,
    "automation": "auto",
}  # fmt: skip


def row(job_id: str, **fields: Any) -> dict[str, Any]:
    """A `claim_next_task` RETURNING row: an unstarted preparation job with no parked browser, unless overridden."""
    return {"job_id": job_id, "has_session": False, "session_node": None, **fields}


def node(name: str, *, alive: bool, recent: bool) -> dict[str, Any]:
    """A `list_node_liveness` row: alive = fresh job heartbeat, recent = healthy inside the takeover window."""
    return {"node": name, "alive": alive, "recent": recent}


def process(node_name: str, role: str, **status: Any) -> dict[str, Any]:
    """A `worker_processes` row: `{node}:control` (queue heartbeat) or `{node}:jobs` (slots, offline flag)."""
    return {"name": f"{node_name}:{role}", "node": node_name, "status": {"role": role, "node": node_name, **status}}


def healthy() -> list[dict[str, Any]]:
    return [
        process("mac", "control", offline=False),
        process("mac", "jobs", slots=3, offline=False),
        process("mac", "browser", offline=False),
    ]


def facts(status: str) -> JobFacts:
    """Real domain facts: `submitted` is terminal (next_step idle); `preparing` still has work to do."""
    return JobFacts(status=status, **OPEN_FACTS)


class AutomationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app: Any = MagicMock(spec=Services)
        self.app.db = MagicMock()
        self.app.db.transaction.return_value = nullcontext()
        self.app.node = "mac"
        self.app.runtime_settings.return_value = RuntimeSettings(workerConcurrency=3, maxOpenBrowsers=6)
        self.app.submit_clicked.return_value = False
        self.app.job.return_value = {"pacing_hostname": "127.0.0.1"}
        self.service = AutomationService(self.app)
        self.repository: Any = MagicMock(spec=AutomationRepository)
        self.service.repository = self.repository
        self.repository.find_automation_for_configure_runtime.return_value = {"enabled": True}
        self.repository.find_automation_enabled.return_value = True
        self.repository.has_finished_run.return_value = False
        self.repository.find_tasks_for_configure_runtime.return_value = None
        self.repository.block_exhausted_tasks.return_value = []
        self.repository.count_active_leases.return_value = 0
        self.repository.sum_job_slots.return_value = 0  # no measurement: the persisted setting stands
        self.repository.count_open_browsers.return_value = 0
        self.repository.list_node_liveness.return_value = []
        self.repository.claim_next_task.return_value = None
        # Every node the liveness rows name has a reachable Neo unless a test says otherwise.
        self.repository.list_browser_online_nodes.side_effect = lambda *_: [
            r["node"] for r in self.repository.list_node_liveness.return_value
        ]
        self.app.tab_binder.return_value.online.return_value = True

    def allow_flags(self) -> list[bool]:
        return [c.kwargs["allow_new_browser"] for c in self.repository.claim_next_task.call_args_list]


class DispatchAdmissionTests(AutomationTestCase):
    def test_spend_is_never_read_during_dispatch_claim_or_start(self) -> None:
        # No cost query exists in the admission repository. Tripwire on reporting catches a regression.
        self.app.reporting = MagicMock()
        self.app.reporting.monitoring.side_effect = AssertionError("spend must not gate admission")
        self.repository.claim_next_task.side_effect = [row("j1"), None, row("j2")]
        self.assertEqual(len(self.service.dispatch_claim("mac")), 1)
        self.assertEqual(self.service.claim("jp"), "j2")
        self.repository.update_tasks_for_start_run.return_value = row("j1")
        self.repository.count_node_browsers.return_value = 0
        self.assertTrue(self.service.start_run("j1", "t1", "mac"))
        self.app.reporting.monitoring.assert_not_called()

    def test_nothing_is_admitted_when_every_worker_slot_is_leased(self) -> None:
        self.repository.count_active_leases.return_value = 3
        self.assertEqual(self.service.dispatch_claim("mac"), [])
        self.repository.claim_next_task.assert_not_called()

    def test_admission_never_exceeds_the_measured_pool_capacity(self) -> None:
        """workerConcurrency 3 but the workers only have 2 pool children: a third lease would idle until it lapsed."""
        self.repository.sum_job_slots.return_value = 2
        self.repository.claim_next_task.side_effect = [row("j1"), row("j2"), row("j3")]
        self.assertEqual([d.job_id for d in self.service.dispatch_claim("mac")], ["j1", "j2"])

    def test_a_lower_setting_throttles_below_the_capacity(self) -> None:
        self.repository.sum_job_slots.return_value = 8
        self.repository.claim_next_task.side_effect = [row("j1"), row("j2"), row("j3"), row("j4")]
        self.assertEqual(len(self.service.dispatch_claim("mac")), 3)

    def test_admits_at_most_the_free_slots_with_fresh_task_ids(self) -> None:
        self.repository.count_active_leases.return_value = 1
        self.repository.claim_next_task.side_effect = [row("j1"), row("j2"), row("j3")]
        out = self.service.dispatch_claim("mac")
        self.assertEqual([d.job_id for d in out], ["j1", "j2"])
        calls = self.repository.claim_next_task.call_args_list
        self.assertEqual(len(calls), 2)
        task_ids = [c.args[0] for c in calls]
        self.assertEqual([str(uuid.UUID(t)) for t in task_ids], task_ids)  # fresh, well-formed ids
        self.assertEqual(len(set(task_ids)), 2)
        self.assertEqual({c.args[1] for c in calls}, {DISPATCH_LEASE_SECONDS})
        self.assertEqual([d.task_id for d in out], task_ids)

    def test_stops_at_the_first_empty_claim(self) -> None:
        self.repository.claim_next_task.side_effect = [row("j1"), None, row("j2")]
        self.assertEqual([d.job_id for d in self.service.dispatch_claim("mac")], ["j1"])
        self.assertEqual(self.repository.claim_next_task.call_count, 2)

    def test_limit_lowers_the_count_but_never_exceeds_the_free_slots(self) -> None:
        self.repository.claim_next_task.side_effect = [row("j1"), row("j2"), row("j3"), row("j4")]
        self.assertEqual(len(self.service.dispatch_claim("mac", limit=1)), 1)
        self.assertEqual(self.repository.claim_next_task.call_count, 1)
        self.repository.claim_next_task.reset_mock(side_effect=True)
        self.repository.claim_next_task.side_effect = [row("j1"), row("j2"), row("j3"), row("j4")]
        self.assertEqual(len(self.service.dispatch_claim("mac", limit=10)), 3)
        self.assertEqual(self.repository.claim_next_task.call_count, 3)

    def test_new_browsers_are_closed_once_open_browsers_reach_the_limit(self) -> None:
        self.app.runtime_settings.return_value = RuntimeSettings(workerConcurrency=3, maxOpenBrowsers=2)
        self.repository.count_open_browsers.return_value = 1
        self.repository.claim_next_task.side_effect = [row("j1"), row("j2"), None]
        self.service.dispatch_claim("mac")
        # j1 opens the second browser in-loop, so the cap trips for j2 and for the final probe
        self.assertEqual(self.allow_flags(), [True, False, False])

    def test_open_sessions_do_not_count_toward_the_browser_cap(self) -> None:
        self.app.runtime_settings.return_value = RuntimeSettings(workerConcurrency=4, maxOpenBrowsers=2)
        self.repository.count_open_browsers.return_value = 1
        self.repository.claim_next_task.side_effect = [row("j1", has_session=True), row("j2", has_session=True), row("j3"), None]
        self.service.dispatch_claim("mac")
        self.assertEqual(self.allow_flags(), [True, True, True, False])

    def test_parked_sessions_route_to_their_live_node_and_others_to_the_shared_queue(self) -> None:
        self.repository.list_node_liveness.return_value = [
            node("mac", alive=True, recent=True),
            node("box", alive=False, recent=False),
        ]
        self.repository.claim_next_task.side_effect = [
            row("j1", has_session=True, session_node="mac"),
            row("j2", has_session=True, session_node="box"),
            row("j3", has_session=True),
        ]
        out = self.service.dispatch_claim("mac")
        self.assertEqual([(d.job_id, d.node) for d in out], [("j1", "mac"), ("j2", None), ("j3", None)])

    def test_jobs_parked_on_recently_healthy_offline_nodes_are_held_back(self) -> None:
        self.repository.list_node_liveness.return_value = [
            node("mac", alive=True, recent=True),
            node("box", alive=False, recent=True),
            node("old", alive=False, recent=False),
        ]
        self.service.dispatch_claim("mac")
        self.assertEqual(self.repository.claim_next_task.call_args.kwargs["skip_nodes"], ["box"])
        self.assertEqual(self.repository.claim_next_task.call_args.kwargs["session_nodes"], ["mac"])

    def test_workers_up_but_no_shared_chrome_is_backpressure_not_a_lease(self) -> None:
        self.repository.list_node_liveness.return_value = [node("mac", alive=True, recent=True)]
        self.repository.list_browser_online_nodes.side_effect = None
        self.repository.list_browser_online_nodes.return_value = []
        self.repository.claim_next_task.side_effect = [row("j1"), None]
        self.assertEqual(self.service.dispatch_claim("mac"), [])
        self.repository.claim_next_task.assert_not_called()

    def test_a_chrome_on_one_live_node_is_enough_to_admit(self) -> None:
        self.repository.list_node_liveness.return_value = [
            node("mac", alive=True, recent=True),
            node("box", alive=True, recent=True),
        ]
        self.repository.list_browser_online_nodes.side_effect = None
        self.repository.list_browser_online_nodes.return_value = ["box"]
        self.repository.claim_next_task.side_effect = [row("j1"), None]
        self.assertEqual([d.job_id for d in self.service.dispatch_claim("mac")], ["j1"])

    def test_no_live_worker_keeps_admitting_so_jobs_wait_for_one(self) -> None:
        self.repository.list_node_liveness.return_value = []
        self.repository.list_browser_online_nodes.side_effect = None
        self.repository.list_browser_online_nodes.return_value = []
        self.repository.claim_next_task.side_effect = [row("j1"), None]
        self.assertEqual(len(self.service.dispatch_claim("mac")), 1)

    def test_dispatch_leaves_the_attempt_for_start_run_but_in_process_claim_charges_it(self) -> None:
        # A lease no worker ever picked up (workers down) expires and is re-dispatched: it must not burn retries.
        self.repository.claim_next_task.side_effect = [row("j1"), None]
        self.service.dispatch_claim("mac")
        self.assertIs(self.repository.claim_next_task.call_args.kwargs["charge_attempt"], False)
        self.repository.claim_next_task.side_effect = None
        self.repository.claim_next_task.return_value = row("j2")
        self.service.claim("jp-1")
        self.assertIs(self.repository.claim_next_task.call_args.kwargs["charge_attempt"], True)

    def test_reused_sessions_are_those_on_live_nodes(self) -> None:
        self.repository.list_node_liveness.return_value = [
            node("box", alive=True, recent=True),
            node("old", alive=False, recent=True),
        ]
        self.service.dispatch_claim("mac")
        kwargs = self.repository.claim_next_task.call_args.kwargs
        self.assertEqual(kwargs["session_nodes"], ["box"])
        self.assertEqual(kwargs["skip_nodes"], ["old"])

    def test_tasks_out_of_attempts_are_handed_to_the_owner(self) -> None:
        self.repository.block_exhausted_tasks.return_value = [{"job_id": "j9"}, {"job_id": "j8"}]
        self.repository.find_last_requeue_reason.side_effect = [None, "browser_page_error"]
        self.service.dispatch_claim("mac")
        reason = "automation retry limit reached"
        self.assertEqual(
            self.app.wait.call_args_list, [call("j9", "you", reason), call("j8", "you", f"{reason}: browser_page_error")]
        )


class ClaimTests(AutomationTestCase):
    """`jp next` obeys the same admission as dispatch_claim, but claims a single job for an external caller."""

    def test_claim_returns_none_while_this_nodes_chrome_is_down(self) -> None:
        self.repository.claim_next_task.return_value = row("j1")
        self.app.tab_binder.return_value.online.return_value = False
        self.assertIsNone(self.service.claim("jp-1"))
        self.repository.claim_next_task.assert_not_called()

    def test_claim_returns_none_without_a_free_slot(self) -> None:
        self.repository.count_active_leases.return_value = 3
        self.assertIsNone(self.service.claim("jp-1"))
        self.repository.claim_next_task.assert_not_called()

    def test_claim_runs_on_own_node_and_skips_other_live_or_recent_nodes(self) -> None:
        self.repository.list_node_liveness.return_value = [
            node("mac", alive=True, recent=True),
            node("box", alive=True, recent=True),
            node("old", alive=False, recent=True),
            node("gone", alive=False, recent=False),
        ]
        self.repository.claim_next_task.return_value = row("j1")
        self.assertEqual(self.service.claim("jp-1"), "j1")
        args, kwargs = self.repository.claim_next_task.call_args
        self.assertEqual(args[:3], ("jp-1", 600, False))
        self.assertEqual(kwargs["node"], "mac")
        self.assertEqual(kwargs["skip_nodes"], ["box", "old"])

    def test_claim_does_not_add_domain_pacing_debt(self) -> None:
        self.repository.claim_next_task.return_value = row("j1")
        self.app.job.return_value = {"pacing_hostname": "jobs.example.com"}
        self.assertEqual(self.service.claim("jp-1"), "j1")
        self.repository.reserve_domain_hit.assert_not_called()
        self.app.pace_browser.assert_not_called()

    def test_dispatch_does_not_reserve_pacing_slots_even_on_repeated_admissions(self) -> None:
        self.app.job.return_value = {"pacing_hostname": "jobs.example.com"}
        self.repository.claim_next_task.side_effect = [row(f"j{i}") for i in range(7)]
        for _ in range(7):  # expired unstarted leases must not grow the action queue
            self.assertEqual(len(self.service.dispatch_claim("mac", limit=1)), 1)
        self.repository.reserve_domain_hit.assert_not_called()
        self.app.pace_browser.assert_not_called()

    def test_admission_sql_does_not_filter_busy_domains_and_keeps_other_gates(self) -> None:
        db: Any = MagicMock()
        AutomationRepository(db).claim_next_task("t", 90, False, current_node="mac")
        query, params = db.one.call_args.args
        for removed in (
            "cost_ledger",
            "budget",
            "board_daily_usage",
            "max_per_day",
            "max_prepare_per_day",
            "max_browser_per_day",
            "max_account_per_day",
        ):
            self.assertNotIn(removed, query)
        self.assertNotIn("domain_pacing", query)
        self.assertNotIn("last_hit", query)
        self.assertIn("t.visible_at<=now()", query)
        self.assertIn("t.attempts<5", query)
        self.assertIn("browser_sessions", query)
        self.assertIn("j.status <> 'awaiting_approval'", query)  # the owner submits those (ADR 0026)
        self.assertNotIn("approvals", query)
        self.assertNotIn("submit_clicked", query)
        self.assertEqual(query.count("%s"), len(params))


class RunLifecycleTests(AutomationTestCase):
    def test_open_session_starts_without_a_node_cap_check(self) -> None:
        self.repository.count_node_browsers.return_value = 99
        self.repository.update_tasks_for_start_run.return_value = row("j1", has_session=True)
        self.assertTrue(self.service.start_run("j1", "task-1", "mac"))
        self.repository.update_tasks_for_start_run.assert_called_with("j1", "task-1", "mac", RUN_LEASE_SECONDS)
        self.repository.count_node_browsers.assert_not_called()
        self.repository.release_unstarted_for_cap.assert_not_called()

    def test_start_serializes_per_node_before_counting_and_starting(self) -> None:
        order: list[str] = []

        def locked(node: str) -> None:
            order.append(f"lock:{node}")

        def started(*args: Any) -> dict[str, Any]:
            order.append("start")
            return row("j1")

        def counted(*args: Any) -> int:
            order.append("count")
            return 0

        self.repository.lock_node_for_start.side_effect = locked
        self.repository.update_tasks_for_start_run.side_effect = started
        self.repository.count_node_browsers.side_effect = counted
        self.assertTrue(self.service.start_run("j1", "task-1", "mac"))
        self.assertEqual(order, ["lock:mac", "start", "count"])

    def test_start_with_the_shared_chrome_down_is_handed_back_not_failed(self) -> None:
        self.repository.update_tasks_for_start_run.return_value = row("j1")
        self.app.tab_binder.return_value.online.return_value = False
        self.assertFalse(self.service.start_run("j1", "task-1", "mac"))
        self.repository.release_unstarted_for_cap.assert_called_once_with("j1", "task-1", CAP_RETRY_SECONDS)
        self.app.event.assert_called_once_with(
            "j1", "start_deferred", "system", {"task": "task-1", "node": "mac", "reason": "Neo offline"}
        )

    def test_duplicate_or_stale_start_is_refused_silently(self) -> None:
        self.repository.update_tasks_for_start_run.return_value = None
        self.assertFalse(self.service.start_run("j1", "task-1", "mac"))
        self.repository.release_unstarted_for_cap.assert_not_called()
        self.app.event.assert_not_called()

    def test_preparation_run_is_gated_by_the_node_cap(self) -> None:
        self.app.runtime_settings.return_value = RuntimeSettings(maxOpenBrowsers=2)
        self.repository.update_tasks_for_start_run.return_value = row("j1")
        self.repository.count_node_browsers.return_value = 1
        self.assertTrue(self.service.start_run("j1", "task-1", "mac"))
        self.repository.count_node_browsers.assert_called_once_with("mac", "j1")
        self.repository.count_node_browsers.return_value = 2
        self.assertFalse(self.service.start_run("j1", "task-1", "mac"))
        self.repository.release_unstarted_for_cap.assert_called_once_with("j1", "task-1", CAP_RETRY_SECONDS)
        self.app.event.assert_called_once_with("j1", "start_deferred", "system", {"task": "task-1", "node": "mac", "limit": 2})

    def test_capture_only_and_clicked_runs_can_start(self) -> None:
        self.repository.update_tasks_for_start_run.return_value = row("j1", has_session=True)
        self.repository.find_automation_enabled.return_value = False  # read-only capture of an already-applied job
        self.assertTrue(self.service.start_run("j1", "task-1", "mac"))
        self.repository.find_automation_enabled.return_value = True
        self.app.submit_clicked.return_value = True  # submit already clicked: confirm only
        self.assertTrue(self.service.start_run("j1", "task-1", "mac"))

    def test_done_gate_refuses_done_while_work_remains(self) -> None:
        self.repository.find_task_lease.return_value = {"node": "mac", "started_at": None}
        self.app.facts.return_value = facts("preparing")
        with self.assertRaises(PermissionError):
            self.service.finish_run("j1", "task-1", "done", 12.0)
        self.repository.update_tasks_for_finish_run.assert_not_called()
        self.app.event.assert_not_called()
        self.app.request_dispatch.assert_not_called()

    def test_owned_queued_finish_releases_records_both_events_and_wakes_dispatch(self) -> None:
        self.repository.find_task_lease.return_value = {"node": "mac", "started_at": None}
        self.service.finish_run("j1", "task-1", "queued", 12.34)
        self.repository.update_tasks_for_finish_run.assert_called_once_with("queued", False, 5, "j1", "task-1")
        finished = {"task": "task-1", "node": "mac", "outcome": "queued", "seconds": 12.3, "refunded": False, "released": True}
        self.assertEqual(
            self.app.event.call_args_list,
            [
                call("j1", "lease_released", "system", {"worker": "task-1", "state": "queued"}),
                call("j1", "task_finished", "system", finished),
            ],
        )
        self.app._defer_close.assert_called_once_with("j1")
        self.app.request_dispatch.assert_called_once_with()

    def test_owned_done_or_blocked_finish_does_not_wake_dispatch(self) -> None:
        self.repository.find_task_lease.return_value = {"node": "mac", "started_at": None}
        self.app.facts.return_value = facts("submitted")  # terminal, so the done gate is open
        self.service.finish_run("j1", "task-1", "done", 5.0)
        self.service.finish_run("j1", "task-2", "blocked", 5.0)
        self.assertEqual(self.repository.update_tasks_for_finish_run.call_count, 2)
        self.app.request_dispatch.assert_not_called()

    def test_finish_after_losing_the_lease_only_records_the_outcome(self) -> None:
        self.repository.find_task_lease.return_value = None
        self.service.finish_run("j1", "task-1", "queued", 3.0)
        self.repository.update_tasks_for_finish_run.assert_not_called()
        self.app._defer_close.assert_not_called()
        self.app.request_dispatch.assert_not_called()
        finished = {"task": "task-1", "node": None, "outcome": "queued", "seconds": 3.0, "refunded": False, "released": False}
        self.app.event.assert_called_once_with("j1", "task_finished", "system", finished)

    def test_dispatch_failed_is_recorded_only_when_the_lease_changed(self) -> None:
        self.repository.update_tasks_for_dispatch_failed.return_value = 1
        self.service.dispatch_failed("j1", "task-1", "r" * 300)
        self.repository.update_tasks_for_dispatch_failed.assert_called_once_with("j1", "task-1", DISPATCH_RETRY_SECONDS)
        self.app.event.assert_called_once_with("j1", "dispatch_failed", "system", {"task": "task-1", "reason": "r" * 200})
        self.app.event.reset_mock()
        self.repository.update_tasks_for_dispatch_failed.return_value = 0
        self.service.dispatch_failed("j1", "task-1", "broker down")
        self.app.event.assert_not_called()

    def test_queued_release_wakes_dispatch_and_blocked_release_does_not(self) -> None:
        self.repository.update_tasks_for_release.return_value = 1
        self.service.release("j1", "task-1")
        self.repository.update_tasks_for_release.assert_called_once_with("queued", False, "queued", 5, "j1", "task-1")
        self.app._defer_close.assert_called_once_with("j1")
        self.app.request_dispatch.assert_called_once_with()
        self.app.request_dispatch.reset_mock()
        self.service.release("j1", "task-1", state="blocked")
        self.app.event.assert_called_with("j1", "lease_released", "system", {"worker": "task-1", "state": "blocked"})
        self.app.request_dispatch.assert_not_called()

    def test_enabling_automation_wakes_dispatch_and_disabling_does_not(self) -> None:
        self.service.set_automation(True)
        self.app.event.assert_called_once_with(None, "automation_changed", "user", {"enabled": True})
        self.app.request_dispatch.assert_called_once_with()
        self.app.request_dispatch.reset_mock()
        self.service.set_automation(False)
        self.repository.update_automation_for_set_automation.assert_called_with(False)
        self.app.request_dispatch.assert_not_called()

    def test_max_open_browsers_below_worker_concurrency_is_refused_before_saving(self) -> None:
        with self.assertRaises(PermissionError):
            self.service.configure_runtime(RuntimeSettings(maxOpenBrowsers=2))
        self.app.save_runtime_settings.assert_not_called()
        self.app.load_runtime_settings.assert_not_called()

    def test_max_open_browsers_equal_to_worker_concurrency_is_accepted(self) -> None:
        self.service.configure_runtime(RuntimeSettings(maxOpenBrowsers=3))
        self.app.save_runtime_settings.assert_called_once()
        saved: RuntimeSettings = self.app.save_runtime_settings.call_args.args[0]
        self.assertEqual((saved.workerConcurrency, saved.maxOpenBrowsers), (3, 3))
        self.app.load_runtime_settings.assert_called_once_with()

    def test_restart_only_setting_is_refused_while_automation_runs(self) -> None:
        with self.assertRaises(PermissionError):
            self.service.configure_runtime(RuntimeSettings(effort="high"))
        self.app.save_runtime_settings.assert_not_called()


class ReportingStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app: Any = MagicMock(spec=Services)
        self.app.db = MagicMock()
        self.app.runtime_settings.return_value = RuntimeSettings(workerConcurrency=3, maxOpenBrowsers=6)
        self.app.automation = MagicMock()  # open_browsers() lives on the automation service
        self.service = ReportingService(self.app)
        self.repository: Any = MagicMock(spec=ReportingRepository)
        self.service.repository = self.repository
        self.repository.find_automation_for_automation_status.return_value = {"enabled": True}
        self.repository.list_fresh_worker_processes.return_value = healthy()
        self.repository.list_active_workers.return_value = [{"worker": "t1", "job": "j1"}]
        self.repository.count_cap_blocked_jobs.return_value = 0
        self.repository.count_parked_forms.return_value = 0
        self.repository.find_recent_requeue.return_value = None
        self.repository.find_task_outcomes_today.return_value = {"finished_total": 7, "finished": 4, "failed": 1, "running": 2}
        self.app.automation.open_browsers.return_value = 0
        self.app.automation.admission_cap.return_value = 3

    def test_healthy_system_reports_no_idle_reason_and_online_flags(self) -> None:
        status = self.service.automation_status()
        self.assertIsNone(status["idleReason"])
        self.assertTrue(status["running"])
        self.assertTrue(status["queueOnline"])
        self.assertTrue(status["workerOnline"])
        self.assertEqual((status["concurrency"], status["browserLimit"]), (3, 6))

    def test_stopped_automation_outranks_every_other_problem(self) -> None:
        self.repository.find_automation_for_automation_status.return_value = {"enabled": False}
        self.repository.list_fresh_worker_processes.return_value = []
        status = self.service.automation_status()
        self.assertEqual(status["idleReason"], "automation stopped")
        self.assertFalse(status["running"])

    def test_missing_control_heartbeat_reports_queue_offline(self) -> None:
        self.repository.list_fresh_worker_processes.return_value = [process("mac", "jobs", slots=3, offline=False)]
        status = self.service.automation_status()
        self.assertTrue(status["idleReason"].startswith("queue offline"))
        self.assertFalse(status["queueOnline"])
        self.assertTrue(status["workerOnline"])

    def test_control_row_that_lost_its_own_ping_reports_queue_offline(self) -> None:
        # The heartbeat thread keeps writing PostgreSQL while Redis is down; it marks the control row offline.
        self.repository.list_fresh_worker_processes.return_value = [
            process("mac", "control", offline=True),
            process("mac", "jobs", slots=3, offline=True),
        ]
        status = self.service.automation_status()
        self.assertFalse(status["queueOnline"])
        self.assertTrue(status["idleReason"].startswith("queue offline"))

    def test_missing_or_offline_job_worker_reports_no_worker(self) -> None:
        cases = {
            "no jobs row": [process("mac", "control", offline=False)],
            "offline jobs row": [process("mac", "control", offline=False), process("mac", "jobs", slots=3, offline=True)],
        }
        for label, processes in cases.items():
            with self.subTest(label):
                self.repository.list_fresh_worker_processes.return_value = processes
                status = self.service.automation_status()
                self.assertEqual(status["idleReason"], "no job worker online (docker compose up -d worker)")
                self.assertFalse(status["workerOnline"])
                self.assertTrue(status["queueOnline"])

    def test_missing_or_offline_shared_chrome_reports_browser_offline(self) -> None:
        cases = {
            "no chrome row": healthy()[:2],
            "offline chrome row": [*healthy()[:2], process("mac", "browser", offline=True)],
        }
        for label, processes in cases.items():
            with self.subTest(label):
                self.repository.list_fresh_worker_processes.return_value = processes
                status = self.service.automation_status()
                self.assertTrue(status["idleReason"].startswith("Neo is not reachable"))
                self.assertFalse(status["browserOnline"])
                self.assertTrue(status["workerOnline"])

    def test_browser_cap_message_carries_open_limit_and_parked_counts(self) -> None:
        self.app.automation.open_browsers.return_value = 6
        self.repository.count_cap_blocked_jobs.return_value = 4
        self.repository.count_parked_forms.return_value = 2
        status = self.service.automation_status()
        self.assertIn("open-browser limit reached (6/6)", status["idleReason"])
        self.assertIn("2 filled forms", status["idleReason"])
        self.assertEqual((status["browsersOpen"], status["browserLimit"]), (6, 6))

    def test_full_browsers_without_cap_blocked_jobs_do_not_claim_the_cap(self) -> None:
        self.app.automation.open_browsers.return_value = 6
        self.repository.list_active_workers.return_value = []
        self.assertEqual(self.service.automation_status()["idleReason"], IDLE_TEXT)

    def test_idle_fallback_is_absent_while_work_runs(self) -> None:
        self.repository.list_active_workers.return_value = []
        self.assertEqual(self.service.automation_status()["idleReason"], IDLE_TEXT)
        self.repository.list_active_workers.return_value = [{"worker": "t1", "job": "j1"}]
        self.assertIsNone(self.service.automation_status()["idleReason"])

    def test_capacity_sums_the_slots_of_online_job_workers_only(self) -> None:
        self.repository.list_fresh_worker_processes.return_value = [
            process("mac", "control", offline=False),
            process("mac", "jobs", slots=3, offline=False),
            process("box", "jobs", slots=2, offline=False),
            process("ghost", "jobs", slots=4, offline=True),
        ]
        self.assertEqual(self.service.automation_status()["capacity"], 5)

    def test_run_counters_come_from_todays_task_events(self) -> None:
        status = self.service.automation_status()
        # started = runs finished today (released) + runs still running that started today
        self.assertEqual((status["started"], status["finished"], status["failed"]), (9, 4, 1))
        self.repository.find_task_outcomes_today.return_value = None
        status = self.service.automation_status()
        self.assertEqual((status["started"], status["finished"], status["failed"]), (0, 0, 0))

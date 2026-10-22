"""Execution contracts with mocked services: no broker, database, browser or model."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

from celery.exceptions import SoftTimeLimitExceeded

from jobpilot.config import Settings
from jobpilot.domain.ports import AgentDriver
from jobpilot.service import Services
from jobpilot.service._support import Dispatch
from jobpilot.worker import client, runtime, tasks
from jobpilot.worker.app import app
from jobpilot.worker.claude import ClaudeRunError
from jobpilot.worker.names import HARD_TIME_LIMIT, QUEUE_CONTROL, SOFT_TIME_LIMIT, TASK_DISPATCH, TASK_PARTITIONS, draining_flag
from jobpilot.worker.runner import JobRunner, RunEnd

POOL4 = {"pool": {"max-concurrency": 4}}


class TaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc: Any = MagicMock(spec=Services)
        self.rt: Any = MagicMock()
        self.rt.svc = self.svc
        self.rt.cfg = Settings(_env_file=None)
        self.rt.runner.run.return_value = RunEnd("done", delay=17, refund_attempt=True)
        self.svc.tab_binder.return_value.online.return_value = True
        self.runtime_patch = patch.object(runtime, "get_runtime", return_value=self.rt)
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)

    def run_task(self) -> None:
        tasks.run_job.push_request(id="task-1")
        try:
            tasks.run_job.run("job-1")
        finally:
            tasks.run_job.pop_request()

    def test_dispatch_preserves_ids_and_routes_every_claim(self) -> None:
        self.svc.dispatch_claim.return_value = [Dispatch("j1", "t1", None), Dispatch("j2", "t2", "box-2")]
        with patch.object(tasks.run_job, "apply_async") as publish:
            tasks.dispatch.run()
        self.svc.dispatch_claim.assert_called_once_with("mac")
        self.assertEqual(
            publish.call_args_list,
            [
                call(("j1",), task_id="t1", queue="jobs", retry=False),
                call(("j2",), task_id="t2", queue="node.box-2", retry=False),
            ],
        )

    def test_dispatch_publish_failure_refunds_and_continues(self) -> None:
        self.svc.dispatch_claim.return_value = [Dispatch("j1", "t1", None), Dispatch("j2", "t2", None)]
        with patch.object(tasks.run_job, "apply_async", side_effect=[ConnectionError("sensitive detail"), None]) as publish:
            tasks.dispatch.run()
        self.assertEqual(publish.call_count, 2)
        self.svc.dispatch_failed.assert_called_once_with("j1", "t1", "ConnectionError")

    def test_failed_refund_does_not_stop_relay(self) -> None:
        self.svc.dispatch_claim.return_value = [Dispatch("j1", "t1", None), Dispatch("j2", "t2", None)]
        self.svc.dispatch_failed.side_effect = RuntimeError("database down")
        with (
            patch.object(tasks.run_job, "apply_async", side_effect=[ConnectionError(), None]) as publish,
            self.assertLogs("jobpilot.worker.tasks", level="ERROR"),
        ):
            tasks.dispatch.run()
        self.assertEqual(publish.call_count, 2)

    def test_duplicate_delivery_touches_nothing_after_start(self) -> None:
        self.svc.start_run.return_value = False
        with patch.object(client, "request_dispatch") as request:
            self.run_task()
        self.svc.start_run.assert_called_once_with("job-1", "task-1", "mac")
        self.rt.runner.run.assert_not_called()
        self.svc.finish_run.assert_not_called()
        request.assert_not_called()

    def test_run_finishes_and_refills_once(self) -> None:
        with (
            patch("jobpilot.worker.tasks.time.monotonic", side_effect=[10.0, 12.5]),
            patch.object(client, "request_dispatch") as request,
        ):
            self.run_task()
        self.rt.runner.run.assert_called_once()
        job, worker, drain = self.rt.runner.run.call_args.args
        self.assertEqual((job, worker), ("job-1", "task-1"))
        self.assertEqual(drain.path, draining_flag(self.rt.cfg.data_dir, "mac"))
        self.svc.finish_run.assert_called_once_with("job-1", "task-1", "done", 2.5, delay=17, refund_attempt=True)
        request.assert_called_once_with()

    def test_finish_failure_retries_queued_once_and_swallows_retry_failure(self) -> None:
        for failures in ([ValueError("work remains"), None], [ValueError("work remains"), RuntimeError("down")]):
            with self.subTest(failures=failures):
                self.svc.finish_run.reset_mock(side_effect=True)
                self.svc.finish_run.side_effect = failures
                with (
                    patch("jobpilot.worker.tasks.time.monotonic", side_effect=[1.0, 3.0]),
                    patch.object(client, "request_dispatch") as request,
                    self.assertLogs("jobpilot.worker.tasks", level="WARNING"),
                ):
                    self.run_task()
                self.assertEqual(self.svc.finish_run.call_count, 2)
                self.assertEqual(
                    self.svc.finish_run.call_args, call("job-1", "task-1", "queued", 2.0, delay=5, refund_attempt=True)
                )
                request.assert_called_once_with()

    def test_node_ping_reports_local_jobs_only(self) -> None:
        for replies, control_offline, jobs_offline in (
            (None, True, True),
            ({"jobs-mac@host": POOL4}, True, False),
            ({"control-mac@host": {}, "jobs-mac@host": POOL4}, False, False),
        ):
            with self.subTest(replies=replies), patch.object(app.control, "inspect") as inspect:
                self.svc.publish_worker.reset_mock()

                def stats(replies: Any = replies) -> Any:
                    self.svc.publish_worker.assert_not_called()
                    return replies

                inspect.return_value.stats.side_effect = stats
                tasks.node_ping.run()
                inspect.assert_called_once_with(timeout=1.0)
                inspect.return_value.stats.assert_called_once_with()
                self.assertEqual(
                    self.svc.publish_worker.call_args_list,
                    [
                        call("mac:control", {"role": "control", "node": "mac", "offline": control_offline}, node="mac"),
                        call(
                            "mac:jobs",
                            {
                                "role": "jobs",
                                "node": "mac",
                                "slots": 4,
                                "workers": 0 if jobs_offline else 1,
                                "offline": jobs_offline,
                            },
                            node="mac",
                        ),
                        call("mac:browser", {"role": "browser", "node": "mac", "offline": False}, node="mac"),
                    ],
                )

    def test_node_ping_broker_failure_publishes_both_offline(self) -> None:
        for failure_at in ("inspect", "stats"):
            with (
                self.subTest(failure_at=failure_at),
                patch.object(app.control, "inspect") as inspect,
                self.assertLogs("jobpilot.worker.tasks", level="WARNING") as logs,
            ):
                self.svc.publish_worker.reset_mock()
                failing_call = inspect if failure_at == "inspect" else inspect.return_value.stats
                failing_call.side_effect = ConnectionError("sensitive broker detail")
                tasks.node_ping.run()
                inspect.assert_called_once_with(timeout=1.0)
                self.assertEqual(logs.output, ["WARNING:jobpilot.worker.tasks:node ping failed (ConnectionError)"])
                self.assertEqual(
                    self.svc.publish_worker.call_args_list,
                    [
                        call("mac:control", {"role": "control", "node": "mac", "offline": True}, node="mac"),
                        call("mac:jobs", {"role": "jobs", "node": "mac", "slots": 4, "workers": 0, "offline": True}, node="mac"),
                        call("mac:browser", {"role": "browser", "node": "mac", "offline": False}, node="mac"),
                    ],
                )

    def test_node_ping_reports_the_shared_chrome_independently_of_the_broker(self) -> None:
        self.svc.tab_binder.return_value.online.return_value = False
        with patch.object(app.control, "inspect") as inspect:
            inspect.return_value.stats.return_value = {"control-mac@host": {}, "jobs-mac@host": {}}
            tasks.node_ping.run()
        self.assertEqual(
            self.svc.publish_worker.call_args_list[-1],
            call("mac:browser", {"role": "browser", "node": "mac", "offline": True}, node="mac"),
        )

    def test_node_ping_control_only_reply(self) -> None:
        with patch.object(app.control, "inspect") as inspect:
            inspect.return_value.stats.return_value = {"control-mac@host": {"ok": "pong"}}
            tasks.node_ping.run()
        self.assertEqual(
            self.svc.publish_worker.call_args_list,
            [
                call("mac:control", {"role": "control", "node": "mac", "offline": False}, node="mac"),
                call("mac:jobs", {"role": "jobs", "node": "mac", "slots": 4, "workers": 0, "offline": True}, node="mac"),
                call("mac:browser", {"role": "browser", "node": "mac", "offline": False}, node="mac"),
            ],
        )

    def test_node_ping_other_node_reply_keeps_local_workers_offline(self) -> None:
        with patch.object(app.control, "inspect") as inspect:
            inspect.return_value.stats.return_value = {
                "control-other@host": {"ok": "pong"},
                "jobs-other@host": {"ok": "pong"},
            }
            tasks.node_ping.run()
        self.assertEqual(
            self.svc.publish_worker.call_args_list,
            [
                call("mac:control", {"role": "control", "node": "mac", "offline": True}, node="mac"),
                call("mac:jobs", {"role": "jobs", "node": "mac", "slots": 4, "workers": 0, "offline": True}, node="mac"),
                call("mac:browser", {"role": "browser", "node": "mac", "offline": False}, node="mac"),
            ],
        )

    def test_partitions_use_existing_database_function(self) -> None:
        tasks.ensure_partitions.run()
        self.rt.db.one.assert_called_once_with("SELECT ensure_job_event_partitions(3)")


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc: Any = MagicMock(spec=Services)
        self.svc.db = MagicMock()
        self.svc.db.one.return_value = {"enabled": True, "attempts": 3}
        self.svc.job.return_value = {"source": "platform", "board_id": "custom", "role": "Role", "company_name": "Co"}
        self.cfg = Settings(_env_file=None)
        self.driver: Any = MagicMock(spec=AgentDriver)
        self.runner = JobRunner(self.cfg, self.svc, self.driver)
        self.stop: Any = MagicMock()
        self.stop.is_set.return_value = False

    def run_job(self) -> RunEnd:
        result = self.runner.run("j", "t", self.stop)
        self.svc.release.assert_not_called()
        self.svc.load_runtime_settings.assert_called_once_with()
        return result

    def test_a_platform_job_runs_the_direct_agent_under_the_lease(self) -> None:
        with patch.object(self.runner, "_run_direct", return_value=RunEnd("done")) as direct:
            self.assertEqual(self.run_job(), RunEnd("done"))
        direct.assert_called_once()
        self.svc.lease_run.assert_called_with("t")

    def test_an_external_row_runs_no_agent_and_asks_the_owner_for_what_is_missing(self) -> None:
        self.svc.job.return_value = {"source": "manual"}
        self.svc.external_missing.return_value = ["location"]
        self.assertEqual(self.run_job(), RunEnd("blocked"))
        self.svc.wait.assert_called_once_with("j", "you", "Couldn't find: location. Add them on the job page.")
        self.driver.run_direct.assert_not_called()

    def test_a_complete_external_row_is_done(self) -> None:
        self.svc.job.return_value = {"source": "imported"}
        self.svc.external_missing.return_value = []
        self.assertEqual(self.run_job(), RunEnd("done"))
        self.svc.wait.assert_not_called()

    def test_exception_mapping_and_side_effects(self) -> None:
        cases = (
            (subprocess.TimeoutExpired("agent", 240), RunEnd("queued", delay=120)),
            (PermissionError("job lease lost"), RunEnd("queued")),
            (ClaudeRunError("agent unavailable", environment=True), RunEnd("queued")),
            (ValueError("fixture failure"), RunEnd("blocked")),
            (SoftTimeLimitExceeded(), RunEnd("queued", delay=30)),
        )
        for exc, expected in cases:
            with self.subTest(exc=exc):
                self.svc.reset_mock()
                with (
                    patch.object(self.runner, "_run_direct", side_effect=exc),
                    self.assertLogs("jobpilot.worker", level="ERROR")
                    if isinstance(exc, (subprocess.TimeoutExpired, PermissionError, ClaudeRunError, ValueError))
                    else patch.object(self.runner, "clock"),
                ):
                    self.assertEqual(self.run_job(), expected)
                if isinstance(exc, subprocess.TimeoutExpired):
                    self.svc.event.assert_called_once_with("j", "agent_timeout", "system", {"retryAfter": 120})
                elif isinstance(exc, ClaudeRunError):
                    self.svc.set_automation.assert_called_once_with(False, actor="system", reason="cli_error")
                    self.svc.event.assert_called_once_with("j", "agent_unavailable", "system", {"message": "agent unavailable"})
                elif isinstance(exc, ValueError):
                    self.svc.event.assert_called_once_with("j", "error", "system", {"message": "fixture failure"})
                    self.svc.wait.assert_called_once_with("j", "you", "automation error: fixture failure")
                else:
                    self.svc.wait.assert_not_called()
                self.stop.set.assert_not_called()

    def test_timeout_backoff_is_capped(self) -> None:
        self.svc.db.one.return_value = {"enabled": True, "attempts": 50}
        with (
            patch.object(self.runner, "_run_direct", side_effect=subprocess.TimeoutExpired("agent", 240)),
            self.assertLogs("jobpilot.worker", level="ERROR"),
        ):
            self.assertEqual(self.run_job().delay, 300)

    def test_drain_file_returns_queued_before_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            flag = draining_flag(Path(directory), "mac")
            self.stop = tasks.DrainSignal(flag)
            self.assertFalse(self.stop.is_set())
            flag.parent.mkdir()
            flag.touch()
            self.assertEqual(self.run_job(), RunEnd("queued"))
        self.svc.lease_run.assert_not_called()

    def test_automation_off_returns_queued(self) -> None:
        # find_automation_enabled reads through an independent connection (never the job's IO-lock connection).
        connection = self.svc.db.independent_conn.return_value.__enter__.return_value
        connection.execute.return_value.fetchone.return_value = {"enabled": False}
        self.assertEqual(self.run_job(), RunEnd("queued"))
        self.svc.heartbeat.assert_not_called()


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        breaker = patch.object(client, "_blocked_until", 0.0)
        breaker.start()
        self.addCleanup(breaker.stop)

    def test_dispatch_request_has_no_job_data(self) -> None:
        with patch.object(app, "send_task") as send:
            client.request_dispatch()
        send.assert_called_once_with(TASK_DISPATCH, queue=QUEUE_CONTROL, expires=10, retry=False)

    def test_broker_errors_are_swallowed_for_one_window(self) -> None:
        with (
            patch("jobpilot.worker.client.time.monotonic", return_value=10.0) as clock,
            patch.object(app, "send_task", side_effect=ConnectionError("secret diagnostic")) as send,
            self.assertLogs("jobpilot.worker.client", level="WARNING") as logs,
        ):
            client.request_dispatch()
            clock.return_value = 39.9
            client.request_dispatch()
            self.assertEqual(send.call_count, 1)
            clock.return_value = 40.0
            client.request_dispatch()
        self.assertEqual(send.call_count, 2)
        self.assertEqual(len(logs.output), 2)
        self.assertNotIn("secret diagnostic", " ".join(logs.output))

    def test_concurrent_request_skips_in_progress_send(self) -> None:
        with client._lock, patch.object(app, "send_task") as send:
            client.request_dispatch()
        send.assert_not_called()


class RuntimeLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        for name, value in (("_runtime", None), ("_pid", None)):
            replacement = patch.object(runtime, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_lazy_runtime_is_pid_scoped_and_pool_sized_for_role(self) -> None:
        for role, pool, size in (("control", "prefork", 8), ("jobs", "threads", 12), ("jobs", "prefork", 4)):
            with self.subTest(role=role, pool=pool):
                cfg = Settings(_env_file=None, jobpilot_pool=pool)
                with (
                    patch.dict(os.environ, {"JOBPILOT_WORKER_ROLE": role}),
                    patch.object(runtime, "get_settings", return_value=cfg),
                    patch.object(runtime, "current_process") as process,
                    patch.object(os, "getpid", return_value=1) as pid,
                    patch.object(runtime, "Database") as database,
                    patch.object(runtime, "Services") as services,
                ):
                    process.return_value.name = "ForkPoolWorker-1"
                    first = runtime.get_runtime()
                    self.assertIs(first, runtime.get_runtime())
                    database.assert_called_once_with(cfg, pool_size=size)
                    self.assertIs(services.return_value.dispatch_hook, client.request_dispatch)
                    pid.return_value = 2
                    self.assertIsNot(first, runtime.get_runtime())
                    self.assertEqual(database.call_count, 2)
                    runtime.close()
                    database.return_value.close.assert_called_once_with()

    def test_prefork_parent_never_opens_database(self) -> None:
        with (
            patch.dict(os.environ, {"JOBPILOT_WORKER_ROLE": "jobs"}),
            patch.object(runtime, "get_settings", return_value=Settings(_env_file=None)),
            patch.object(runtime, "current_process") as process,
            patch.object(runtime, "Database") as database,
        ):
            process.return_value.name = "MainProcess"
            with self.assertRaises(RuntimeError):
                runtime.get_runtime()
        database.assert_not_called()

    def test_jobs_signals_create_private_flag_then_remove_it_without_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = Settings(_env_file=None, jobpilot_home=Path(directory))
            flag = draining_flag(cfg.data_dir, "mac")
            with (
                patch.object(tasks, "get_settings", return_value=cfg),
                patch.object(runtime, "worker_role", return_value="jobs"),
                patch.object(runtime, "get_runtime") as get_runtime,
            ):
                tasks.worker_shutting_down()
                self.assertEqual(flag.stat().st_mode & 0o777, 0o600)
                self.assertEqual(flag.parent.stat().st_mode & 0o777, 0o700)
                tasks.worker_ready()
                self.assertFalse(flag.exists())
                get_runtime.assert_not_called()

    def test_control_signals_start_stop_threads_publish_offline_and_close(self) -> None:
        rt: Any = MagicMock()
        rt.cfg.jobpilot_node = "mac"
        stop = MagicMock()
        with (
            patch.object(runtime, "worker_role", return_value="control"),
            patch.object(runtime, "get_runtime", return_value=rt),
            patch.object(runtime, "close") as close,
            patch.object(tasks, "_stop", stop),
            patch.object(tasks, "_threads", []),
            patch("jobpilot.worker.tasks.threading.Thread") as thread,
        ):
            tasks.worker_ready()
            self.assertEqual(thread.call_count, 2)
            tasks.worker_shutting_down()
            stop.set.assert_called_once_with()
            tasks.worker_shutdown()
            self.assertEqual(thread.return_value.join.call_count, 2)
            self.assertEqual(rt.svc.publish_worker.call_count, 2)
            for args in rt.svc.publish_worker.call_args_list:
                self.assertTrue(args.args[1]["offline"])
            close.assert_called_once_with()

    def test_child_shutdown_closes_runtime(self) -> None:
        with patch.object(runtime, "close") as close:
            tasks.worker_process_shutdown()
        close.assert_called_once_with()

    def test_celery_configuration_keeps_postgres_authoritative(self) -> None:
        self.assertIsNone(app.conf.result_backend)
        self.assertTrue(app.conf.task_ignore_result)
        self.assertFalse(app.conf.task_acks_late)
        self.assertEqual(app.conf.accept_content, ["json"])
        self.assertEqual(app.conf.worker_prefetch_multiplier, 1)
        self.assertTrue(app.conf.worker_enable_remote_control)
        self.assertEqual((app.conf.task_soft_time_limit, app.conf.task_time_limit), (SOFT_TIME_LIMIT, HARD_TIME_LIMIT))
        self.assertEqual((tasks.run_job.soft_time_limit, tasks.run_job.time_limit), (None, None))
        self.assertEqual(app.conf.beat_schedule["dispatch"]["options"]["expires"], 10)
        for name in (TASK_DISPATCH, TASK_PARTITIONS):
            self.assertEqual(app.conf.task_routes[name]["queue"], QUEUE_CONTROL)
            self.assertEqual((app.tasks[name].soft_time_limit, app.tasks[name].time_limit), (60, 60))


if __name__ == "__main__":
    unittest.main()

"""Long runs use real fake subprocesses; no browser, model, database or broker."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from jobpilot.config import RuntimeSettings, Settings
from jobpilot.repository.automation import AutomationRepository
from jobpilot.service import Services
from jobpilot.worker.claude import AgentInterrupted, ClaudeDriver, run_stream
from jobpilot.worker.runner import JobRunner

ROOT = Path(__file__).resolve().parents[2]
FINAL = json.dumps(
    {
        "type": "result",
        "result": '{"status":"blocked"}',
        "usage": {
            "input_tokens": 12,
            "output_tokens": 3,
            "cache_read_input_tokens": 5,
        },
        "total_cost_usd": 0.8,
    }
)


class ProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.processes: list[subprocess.Popen[bytes]] = []
        self.svc: Any = MagicMock(spec=Services)
        self.svc.db = MagicMock()
        self.svc.job.return_value = {"status": "preparing"}
        self.svc.latest_agent_guidance.return_value = None
        self.runner = JobRunner(Settings(_env_file=None), self.svc, MagicMock())
        self.runner.repository = MagicMock()
        self.runner.repository.find_automation_enabled.return_value = True
        self.stop = MagicMock(is_set=MagicMock(return_value=False))
        self.monitor = self.runner._direct_monitor("j", "t", self.stop)

    def run_fake(self, script: str, *, stall: float = 0, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        popen = subprocess.Popen

        def launch(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
            process = popen(*args, **kwargs)
            self.processes.append(process)
            return process

        with patch("jobpilot.worker.claude.subprocess.Popen", side_effect=launch):
            return run_stream(
                [sys.executable, "-u", "-c", script],
                "DATA",
                cwd=ROOT,
                env=dict(os.environ),
                check=self.monitor,
                stall_seconds=stall,
                timeout=timeout,
            )

    def assert_dead(self) -> None:
        self.assertEqual(len(self.processes), 1)
        self.assertIsNotNone(self.processes[0].poll())

    def test_long_active_run_keeps_lease_and_returns_exact_final_envelope(self) -> None:
        expiry = time.monotonic() + 0.15
        beats: list[float] = []

        def heartbeat(*_: Any) -> None:
            nonlocal expiry
            now = time.monotonic()
            self.assertLess(now, expiry, "long fake run let its lease expire")
            beats.append(now)
            expiry = now + 0.15

        self.svc.heartbeat.side_effect = heartbeat
        script = (
            "import sys,time\nsys.stdin.read()\n"
            'for i in range(12):\n print(\'{"type":"stream_event"}\', flush=True); time.sleep(.05)\n'
            f"print({FINAL!r}, flush=True)"
        )
        with patch("jobpilot.worker.runner.RUN_HEARTBEAT_SECONDS", 0.05):
            result = self.run_fake(script, stall=0.3)
        self.assertEqual(result.stdout, FINAL)
        self.assertGreater(len(beats), 3)
        self.assertGreater(beats[-1] - beats[0], 0.15)
        self.assert_dead()

    def test_lost_lease_terminates_live_process(self) -> None:
        self.svc.heartbeat.side_effect = [None, None, PermissionError("job lease lost")]
        with patch("jobpilot.worker.runner.RUN_HEARTBEAT_SECONDS", 0.05), self.assertRaisesRegex(PermissionError, "lease lost"):
            self.run_fake("import time; time.sleep(30)")
        self.assert_dead()

    def test_stop_and_drain_terminate_without_renewing_when_draining(self) -> None:
        for drain in (False, True):
            with self.subTest(drain=drain):
                self.setUp()
                self.svc.heartbeat.side_effect = [None, None]
                if drain:
                    self.stop.is_set.side_effect = [False, False, False, True]
                else:
                    self.runner.repository.find_automation_enabled.side_effect = [True, False]
                with self.assertRaises(AgentInterrupted):
                    self.run_fake("import time; time.sleep(30)")
                self.assert_dead()
                self.svc.heartbeat.assert_called_once()

    def test_stall_terminates_despite_healthy_lease_heartbeats(self) -> None:
        with (
            patch("jobpilot.worker.runner.RUN_HEARTBEAT_SECONDS", 0.05),
            self.assertRaisesRegex(AgentInterrupted, "agent_stalled"),
        ):
            self.run_fake("import time; time.sleep(30)", stall=0.35)
        self.assert_dead()
        self.assertGreater(self.svc.heartbeat.call_count, 1)

    def test_disabled_stall_allows_silence_and_timeout_remains_optional(self) -> None:
        result = self.run_fake(f"import time; time.sleep(.35); print({FINAL!r})", stall=0)
        self.assertEqual(result.stdout, FINAL)
        self.assert_dead()
        self.setUp()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_fake("import time; time.sleep(30)", timeout=0.3)
        self.assert_dead()

    def test_sigterm_resistance_escalates_to_sigkill(self) -> None:
        with self.assertRaises(AgentInterrupted):
            self.run_fake("import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)", stall=0.3)
        self.assert_dead()
        self.assertEqual(self.processes[0].returncode, -9)

    def test_stderr_activity_does_not_reset_stall(self) -> None:
        with self.assertRaisesRegex(AgentInterrupted, "agent_stalled"):
            self.run_fake(
                "import sys,time\nwhile True:\n print('activity',file=sys.stderr,flush=True); time.sleep(.05)", stall=0.3
            )
        self.assert_dead()


class LimitsTests(unittest.TestCase):
    def test_defaults_zero_and_explicit_timeout_bounds(self) -> None:
        for cls, fields in (
            (Settings, ("browser_budget_usd", "browser_run_timeout_seconds", "agent_stall_seconds")),
            (RuntimeSettings, ("browserBudgetUsd", "browserRunTimeoutSeconds", "agentStallSeconds")),
        ):
            settings = cls()
            self.assertEqual(tuple(getattr(settings, field) for field in fields), (0, 0, 900))
            for timeout in (0, 30, 240, 540):
                cls.model_validate({fields[1]: timeout})
            for timeout in (-1, 1, 29, 541):
                with self.assertRaises(ValidationError):
                    cls.model_validate({fields[1]: timeout})

    def test_zero_omits_budget_and_timeout_nonzero_preserves_them(self) -> None:
        for budget, timeout in ((0, 0), (0.5, 240)):
            with self.subTest(budget=budget):
                cfg = Settings(_env_file=None, jobpilot_home=ROOT, browser_budget_usd=budget, browser_run_timeout_seconds=timeout)
                svc: Any = MagicMock()
                driver = ClaudeDriver(cfg, svc)
                with patch("jobpilot.worker.claude.run_stream") as run:
                    run.return_value = subprocess.CompletedProcess("claude", 0, FINAL, "")
                    result = driver._run(
                        "j",
                        "DATA",
                        model="claude-haiku-5-5",
                        check=lambda: None,
                        session_id="00000000-0000-4000-8000-000000000001",
                    )
                args = run.call_args.args[0]
                if budget:
                    self.assertEqual(args[args.index("--max-budget-usd") + 1], "0.5")
                    self.assertEqual(run.call_args.kwargs["timeout"], 240)
                else:
                    self.assertNotIn("--max-budget-usd", args)
                    self.assertNotIn("timeout", run.call_args.kwargs)
                self.assertEqual(result, '{"status":"blocked"}')
                self.assertEqual(args[args.index("--output-format") + 1], "stream-json")
                svc.cost.assert_called_once_with("j", "claude-haiku-5-5", 17, 3, "agent_run", 0.8)

    def test_heartbeat_uses_short_independent_connection(self) -> None:
        db: Any = MagicMock()
        connection = db.independent_conn.return_value.__enter__.return_value
        connection.execute.return_value.rowcount = 1
        self.assertEqual(AutomationRepository(db).update_tasks_for_heartbeat(600, "j", "t"), 1)
        db.run.assert_not_called()
        self.assertIn("statement_timeout", connection.execute.call_args_list[0].args[0])
        self.assertEqual(connection.execute.call_args.args[1], (600, "j", "t"))
        db.independent_conn.return_value.__exit__.assert_called_once()

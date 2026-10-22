"""Guidance interruption, manual completion and tab ownership regressions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from jobpilot.config import Settings
from jobpilot.dto.direct import DirectResult
from jobpilot.service.agent_guidance import AgentGuidanceService
from jobpilot.service.direct import DirectService
from jobpilot.worker.claude import AgentInterrupted, ClaudeDriver, run_stream
from jobpilot.worker.runner import JobRunner


class GuidanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc: Any = MagicMock()
        self.svc.job.return_value = {"status": "preparing"}
        self.svc.submit_clicked.return_value = False
        self.service = AgentGuidanceService(self.svc)
        self.service.repository = MagicMock()

    def test_guidance_is_recorded_then_resumed_and_never_approves(self) -> None:
        self.service.send("j", "  I closed the popup. Continue.  ")
        self.svc.event.assert_called_once_with("j", "agent_guidance", "user", {"message": "I closed the popup. Continue."})
        self.svc.resume.assert_called_once_with("j")
        self.svc.owner_submitted.assert_not_called()

    def test_latest_guidance_survives_arbitrary_progress_events_and_other_jobs(self) -> None:
        other = [{"id": 300 - i, "jobId": "other", "actor": "user"} for i in range(101)]
        expected = {"id": 100, "jobId": "j", "actor": "user", "payload": {"message": "Continue"}}
        self.service.repository.event_feed.side_effect = [other, [expected]]
        self.assertEqual(self.service.latest("j"), expected)
        self.assertEqual(self.service.repository.event_feed.call_args.args, (["agent_guidance"], 100, 200))

    def test_every_literal_progress_message_in_the_source_is_accepted(self) -> None:
        # A message the allowlist does not know raises ValueError at runtime; CV selection once failed on every job
        # because of exactly that. Scan the source so a new literal cannot be added without being listed.
        import re
        from pathlib import Path

        import jobpilot

        literals = set()
        for path in Path(jobpilot.__file__).parent.rglob("*.py"):
            literals.update(re.findall(r'agent_progress\(\s*\w+,\s*"([^"]+)"', path.read_text()))
        self.assertTrue(literals)
        self.service._progress_at.clear()
        for message in sorted(literals):
            self.service._progress_at.clear()
            self.service.progress("j", message)

    def test_secrets_and_blank_guidance_are_rejected_without_persistence(self) -> None:
        for message in (" ", "token=private-value", "x" * 2001):
            with self.assertRaises(ValueError):
                self.service.send("j", message)
        self.svc.event.assert_not_called()
        self.svc.resume.assert_not_called()

    def test_guidance_cannot_restart_submitted_or_submit_clicked_jobs(self) -> None:
        self.svc.job.return_value = {"status": "submitted"}
        with self.assertRaises(PermissionError):
            self.service.send("j", "Continue")
        self.svc.job.return_value = {"status": "awaiting_approval"}
        self.svc.submit_clicked.return_value = True
        with self.assertRaises(PermissionError):
            self.service.send("j", "Continue")
        self.svc.resume.assert_not_called()

    def test_monitor_stops_when_owner_submits_or_sends_guidance(self) -> None:
        self.svc.latest_agent_guidance.return_value = None
        runner = JobRunner(Settings(_env_file=None), self.svc, MagicMock())
        runner.repository = MagicMock()
        runner.repository.find_automation_enabled.return_value = True
        stop = MagicMock(is_set=MagicMock(return_value=False))
        check = runner._direct_monitor("j", "task", stop)
        self.svc.job.return_value = {"status": "submitted"}
        with self.assertRaisesRegex(AgentInterrupted, "owner_finished"):
            check()
        self.svc.heartbeat.assert_not_called()
        self.svc.job.return_value = {"status": "preparing"}
        self.svc.latest_agent_guidance.return_value = {"ts": "new"}
        with self.assertRaisesRegex(AgentInterrupted, "guidance_updated"):
            check()

    def test_late_agent_result_cannot_overwrite_owner_submission(self) -> None:
        self.svc.job.return_value = {"status": "submitted"}
        self.svc.db.transaction.return_value.__enter__.return_value = None
        report = DirectResult(
            status="blocked", role=None, company=None, fields=[], questions=[], note="stale result", confirmation=None
        )
        self.assertEqual(DirectService(self.svc).apply("j", report), "done")
        self.svc.wait.assert_not_called()
        self.svc.set_meta.assert_not_called()


class StreamTests(unittest.TestCase):
    def test_final_envelope_is_normalized_and_intermediate_text_discarded(self) -> None:
        result = {"status": "SUCCESS", "response": "OK", "usage": {}}
        events = [
            {"event": "step_update", "step_update": {"text_delta": "do-not-persist"}},
            {"event": "result", "result": result},
        ]
        script = "import json\n" + "\n".join(f"print({json.dumps(event)!r}, flush=True)" for event in events)
        p = run_stream(
            [sys.executable, "-u", "-c", script], "", cwd=Path.cwd(), env=dict(os.environ), check=lambda: None, stall_seconds=5
        )
        self.assertEqual(json.loads(p.stdout), result)
        self.assertNotIn("do-not-persist", p.stdout)


class NeoDriverTests(unittest.TestCase):
    def run_driver(self, **settings: object) -> list[str]:
        cfg = Settings(_env_file=None, jobpilot_home=Path.cwd().parent, **settings)  # type: ignore[arg-type]
        svc: Any = MagicMock()
        svc.browser_target.return_value = "7"
        driver = ClaudeDriver(cfg, svc)
        with (
            patch("jobpilot.worker.claude.run_stream") as stream,
            patch.object(Path, "resolve", return_value=Path("/cv.pdf")),
        ):
            stream.return_value = subprocess.CompletedProcess(
                [], 0, json.dumps({"type": "result", "result": '{"status":"blocked"}', "usage": {}}), ""
            )
            with patch("jobpilot.service.agent_sessions.AgentSessions") as sessions:
                sessions.return_value.start.return_value = ("00000000-0000-4000-8000-000000000001", False, {})
                driver.run_direct("j", "DATA", worker="t", cv=Path("/cv.pdf"), check=lambda: None)
        self.prompt = stream.call_args.args[1]
        return list(stream.call_args.args[0])

    def test_agent_gets_the_whole_neo_server_and_no_built_in_tools(self) -> None:
        args = self.run_driver(neo_mcp_url="http://host.docker.internal:9010/mcp")
        config = json.loads(args[args.index("--mcp-config") + 1])
        self.assertEqual(config, {"mcpServers": {"neo": {"type": "http", "url": "http://host.docker.internal:9010/mcp"}}})
        allowed = args[args.index("--allowedTools") + 1 : args.index("--disallowedTools")]
        self.assertEqual(allowed, ["mcp__neo"])
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertIn("--strict-mcp-config", args)
        self.assertIn("Bash", args[args.index("--disallowedTools") :])
        self.assertIn("Neo browser MCP tools", self.prompt)
        self.assertIn("BrowserOS neo", args[args.index("--append-system-prompt") + 1])

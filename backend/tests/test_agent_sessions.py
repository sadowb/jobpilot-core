"""Private session persistence and delta prompts; filesystem/CLI/DB are isolated."""

import json
import os
import subprocess
import tempfile
import time
import unittest
from contextlib import nullcontext
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

from jobpilot.config import Settings
from jobpilot.dto.direct import data_block
from jobpilot.repository.database import Database
from jobpilot.service.agent_sessions import RETENTION, AgentSessions, delta_prompt
from jobpilot.service.jobs import JobsService
from jobpilot.worker.claude import ClaudeDriver, ClaudeRunError, child_environment

SID = "00000000-0000-4000-8000-000000000001"
JOB = "j_abcde"


class SessionsTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.svc: Any = MagicMock()
        self.svc.cfg = Settings(_env_file=None, jobpilot_home=self.root)
        self.svc.db.transaction.side_effect = lambda: nullcontext()
        self.svc.db.try_session_lock.side_effect = lambda *_: nullcontext(True)
        self.sessions = AgentSessions(self.svc)
        self.sessions.repo = MagicMock()
        self.sessions.repo.get.return_value = {"id": SID, "context": {"x": "hash"}}

    def transcript(self, text: str | None = None) -> Path:
        path = self.sessions.directory(JOB) / "projects" / "encoded-stable-cwd" / f"{SID}.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(text or json.dumps({"type": "user", "sessionId": SID, "message": {"content": "DATA"}}))
        return path

    def test_try_lock_reuses_held_connection_and_restores_context(self) -> None:
        db = Database.__new__(Database)
        db._connection = ContextVar("test_connection", default=None)
        db._single = None
        db.pool = MagicMock()
        connection = db.pool.connection.return_value.__enter__.return_value
        connection.execute.return_value.fetchone.return_value = {"acquired": True}
        with db.try_session_lock(JOB, 3) as acquired:
            self.assertTrue(acquired)
            self.assertIs(db._connection.get(), connection)
            with db.try_session_lock(JOB, 3) as nested:
                self.assertTrue(nested)
        db.pool.connection.assert_called_once()
        self.assertIsNone(db._connection.get())

    def test_terminal_transition_schedules_cleanup(self) -> None:
        jobs = JobsService(self.svc)
        jobs.repository = MagicMock()
        self.svc.job.return_value = {"status": "preparing"}
        with patch("jobpilot.service.jobs.can_transition", return_value=MagicMock(ok=True)):
            jobs.transition(JOB, "submitted")
        self.svc.cleanup_agent_session.assert_called_once_with(JOB)

    def test_first_id_is_persisted_before_invocation(self) -> None:
        self.sessions.repo.get.return_value = {"id": None, "context": {}}
        self.sessions.repo.create.side_effect = lambda _, sid: {"id": sid, "context": {}}
        sid, resume, _ = self.sessions.start(JOB)
        self.assertEqual(UUID(sid).version, 4)
        self.assertFalse(resume)
        self.sessions.repo.create.assert_called_once_with(JOB, sid)
        self.assertEqual(self.sessions.directory(JOB).stat().st_mode & 0o777, 0o700)

    def test_existing_session_id_is_reused(self) -> None:
        self.transcript()
        self.assertEqual(self.sessions.start(JOB), (SID, True, {"x": "hash"}))
        self.sessions.repo.create.assert_not_called()

    def test_missing_and_corrupt_sessions_reset_with_safe_event(self) -> None:
        for content in (None, "private broken JSON"):
            if content:
                self.transcript(content)
            sid, resume, context = self.sessions.start(JOB)
            self.assertNotEqual(sid, SID)
            self.assertFalse(resume)
            self.assertEqual(context, {})
            self.svc.event.assert_called_with(JOB, "agent_session_reset", "system", {"reason": "missing_or_corrupt"})

    def test_cleanup_removes_only_jobs_private_directory(self) -> None:
        path = self.transcript()
        other = self.sessions.directory("j_other") / "keep"
        other.write_text("keep")
        self.sessions.cleanup(JOB)
        self.assertFalse(path.exists())
        self.assertTrue(other.exists())
        self.sessions.repo.clear.assert_called_once_with(JOB)

    def test_sweep_removes_orphans_and_old_sessions_but_not_active_runs(self) -> None:
        self.transcript()
        self.sessions.repo.active_jobs.return_value = {JOB}
        self.sessions.sweep()
        self.assertTrue((self.sessions.root / JOB).exists())
        old = time.time() - RETENTION - 60
        os.utime(self.sessions.root / JOB, (old, old))
        self.svc.db.try_session_lock.side_effect = lambda *_: nullcontext(False)
        self.sessions.sweep()
        self.assertTrue((self.sessions.root / JOB).exists())
        self.svc.db.try_session_lock.side_effect = lambda *_: nullcontext(True)
        self.sessions.sweep()
        self.assertFalse((self.sessions.root / JOB).exists())
        self.transcript()
        self.sessions.repo.active_jobs.return_value = set()
        self.sessions.sweep()
        self.assertFalse((self.sessions.root / JOB).exists())

    def test_transient_tool_payloads_and_reasoning_are_removed(self) -> None:
        rows = [
            {
                "type": "assistant",
                "sessionId": SID,
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "123456"},
                        {"type": "tool_use", "id": "t", "name": "neo", "input": {"code": "123456"}},
                    ]
                },
            },
            {
                "type": "user",
                "sessionId": SID,
                "toolUseResult": "123456",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t", "content": "123456"},
                    ]
                },
            },
        ]
        path = self.transcript("\n".join(map(json.dumps, rows)))
        self.sessions.sanitize(JOB)
        self.assertNotIn("123456", path.read_text())
        self.assertTrue(self.sessions.valid(path.parents[2], SID))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_ancillary_cli_files_are_removed_without_following_symlinks(self) -> None:
        path = self.transcript()
        config = self.sessions.root / JOB
        debug = config / "debug"
        debug.mkdir()
        (debug / "trace").write_text("123456")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep").write_text("untouched")
        (path.parent / "subagents").symlink_to(outside, target_is_directory=True)
        self.sessions.sanitize(JOB)
        self.assertFalse(debug.exists())
        self.assertFalse((path.parent / "subagents").exists())
        self.assertTrue((outside / "keep").exists())
        self.assertTrue(path.exists())

    def test_delta_resends_owner_data_and_adds_the_current_approved_draft(self) -> None:
        original = {"job_id": JOB, "target_id": "42", "profile": {"name": "Owner"}, "answers": []}
        _, hashes = delta_prompt("PREPARATION\n" + data_block(original), {})
        new = {
            **original,
            "answers": [{"question_key": "start", "scope": "job:" + JOB, "answer": "Tomorrow"}],
            "approved_draft_hash": "abc",
            "approved_fields": [{"label": "Name", "value": "Owner"}],
        }
        delta, _ = delta_prompt("SUBMIT RUN\n" + data_block(new), hashes)
        data = json.loads(delta.split("DATA JSON (", 1)[1].split("\n", 1)[1].rsplit("\nEND DATA", 1)[0])
        self.assertEqual(data["profile"], original["profile"])  # profile, answers and facts are resent in full every turn
        self.assertEqual(data["answers"], new["answers"])
        self.assertEqual(data["approved_draft_hash"], "abc")
        self.assertEqual(data["target_id"], "42")

    def test_failed_resume_starts_fresh_with_full_data_only_on_startup_failure(self) -> None:
        cv = self.root / "cv.pdf"
        cv.write_bytes(b"fixture")
        driver = ClaudeDriver(self.svc.cfg, self.svc)
        prompt = "PREPARATION\n" + data_block({"job_id": JOB, "profile": {"name": "Owner"}})
        with (
            patch("jobpilot.service.agent_sessions.AgentSessions", return_value=self.sessions),
            patch.object(driver, "_run") as run,
        ):
            self.transcript()
            run.side_effect = [ClaudeRunError("missing", reason="resume_failed"), "result"]
            driver.run_direct(JOB, prompt, worker="w", cv=cv, check=lambda: None)
            self.assertTrue(run.call_args_list[0].kwargs["resume"])
            self.assertEqual(run.call_args_list[1].args[1], prompt)
            self.assertNotEqual(run.call_args_list[1].kwargs["session_id"], SID)
            self.sessions.repo.remember.assert_called_once()

    def test_resume_scrubs_crash_leftovers_before_loading(self) -> None:
        path = self.transcript(
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": SID,
                    "uuid": "a",
                    "parentUuid": None,
                    "message": {"content": [{"type": "tool_use", "id": "t", "name": "neo", "input": {"text": "123456"}}]},
                }
            )
        )
        self.assertTrue(self.sessions.start(JOB)[1])
        self.assertNotIn("123456", path.read_text())
        self.assertEqual(json.loads(path.read_text())["uuid"], "a")

    def test_private_config_directory_and_stable_cwd_reach_cli(self) -> None:
        agent = self.root / "agent"
        agent.mkdir()
        (agent / "NEO.md").write_text("Instructions")
        driver = ClaudeDriver(self.svc.cfg, self.svc)
        with patch(
            "jobpilot.worker.claude.run_stream", return_value=subprocess.CompletedProcess([], 0, '{"result":"ok"}', "")
        ) as run:
            driver._run(JOB, "PREPARATION\nDATA", model="claude-haiku-5-5", check=lambda: None, session_id=SID)
        self.assertEqual(run.call_args.kwargs["cwd"], agent)
        self.assertEqual(run.call_args.kwargs["env"]["CLAUDE_CONFIG_DIR"], str(self.sessions.root / JOB))

    def test_non_startup_resume_failure_is_never_replayed(self) -> None:
        cv = self.root / "cv.pdf"
        cv.write_bytes(b"fixture")
        self.transcript()
        driver = ClaudeDriver(self.svc.cfg, self.svc)
        with (
            patch("jobpilot.service.agent_sessions.AgentSessions", return_value=self.sessions),
            patch.object(driver, "_run", side_effect=ClaudeRunError("after browser action")) as run,
        ):
            with self.assertRaises(ClaudeRunError):
                driver.run_direct(JOB, "PREPARATION", worker="w", cv=cv, check=lambda: None)
        run.assert_called_once()
        self.sessions.repo.reset.assert_not_called()

    def test_argv_and_auth_isolation(self) -> None:
        agent = self.root / "agent"
        agent.mkdir()
        (agent / "NEO.md").write_text("Instructions")
        driver = ClaudeDriver(self.svc.cfg, self.svc)
        for resume, flag in ((False, "--session-id"), (True, "--resume")):
            args = driver._args("claude-haiku-5-5", SID, resume=resume)
            self.assertEqual(args[args.index(flag) + 1], SID)
            self.assertNotIn("--no-session-persistence", args)
        with patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "fixture"}):
            self.assertEqual(child_environment()["CLAUDE_CODE_OAUTH_TOKEN"], "fixture")

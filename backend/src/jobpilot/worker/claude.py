from __future__ import annotations

import getpass
import json
import logging
import math
import os
import re
import selectors
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypedDict

from ..config import Settings
from ..service import Services

# The agent gets the browser's whole MCP toolset (`mcp__neo` allows every tool of that server) and simply uses it. The
# guards that remain do not depend on the browser: no built-in tools, the server-side submit fence, approvals created
# only from the dashboard session, and the untrusted-data rules in agent/NEO.md (ADR 0019).
NEO_SERVER = "neo"
NEO_ALLOWED = (f"mcp__{NEO_SERVER}",)
BROWSER_MODEL = "claude-haiku-5-5"  # the only model that drives a browser (owner decision)
PROGRESS_SECONDS = 15


class _TimeoutOptions(TypedDict, total=False):
    timeout: float


class ClaudeRunError(RuntimeError):
    """Safe diagnostic with a classification derived only from the CLI envelope."""

    def __init__(self, message: str, *, environment: bool = False, reason: str = "cli_error", count: int | None = None) -> None:
        super().__init__(message)
        self.environment = environment
        self.reason = reason
        self.count = count


class AgentInterrupted(RuntimeError):
    """Stop/drain, a failed lease check, or a stalled model; keep attempt accounting."""


def _terminate(process: subprocess.Popen[bytes]) -> None:
    """Stop Claude and its in-flight Bash children, escalating after a short grace."""
    try:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def run_stream(
    args: list[str],
    prompt: str,
    *,
    cwd: Path,
    env: dict[str, str],
    check: Callable[[], None],
    stall_seconds: float,
    timeout: float | None = None,
    progress: Callable[[str], None] | None = None,
    timing: Callable[[dict[str, float]], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Monitor without a work limit. Discard intermediate events; retain only the final CLI envelope.

    Nonblocking pipes keep Stop/lease checks responsive even while writing a large prompt or while
    Claude is silent. Stdout bytes (including partial model messages) alone reset the stall clock.
    Raw stream events and stderr never enter logs, prompts or the database.
    """
    check()
    with (
        subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            start_new_session=True,
            umask=0o077,
        ) as process,
        selectors.DefaultSelector() as selector,
    ):
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        for pipe, events in (
            (process.stdin, selectors.EVENT_WRITE),
            (process.stdout, selectors.EVENT_READ),
            (process.stderr, selectors.EVENT_READ),
        ):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, events)
        pending = memoryview(prompt.encode())
        line = bytearray()
        stderr = bytearray()
        final = ""
        started = last_activity = time.monotonic()
        first_output: float | None = None

        def consume(raw: bytes) -> None:
            nonlocal final
            try:
                message = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise ClaudeRunError("agent CLI returned invalid stream JSON") from None
            if isinstance(message, dict) and message.get("type") == "result":
                final = raw.decode()
            elif isinstance(message, dict) and message.get("event") == "result" and isinstance(message.get("result"), dict):
                final = json.dumps(message["result"])
            elif progress is not None and isinstance(message, dict) and message.get("type") in ("assistant", "tool_use"):
                progress("Working on the application and checking the form.")
            elif progress is not None and isinstance(message, dict) and message.get("event") == "step_update":
                progress("Working on the application and checking the form.")

        try:
            while selector.get_map() or process.poll() is None:
                check()
                now = time.monotonic()
                if timeout is not None and now - started >= timeout:
                    raise subprocess.TimeoutExpired(args, timeout)
                if stall_seconds and now - last_activity >= stall_seconds:
                    raise AgentInterrupted("agent_stalled")
                for key, _ in selector.select(timeout=0.2):
                    ready_pipe = key.fileobj
                    if ready_pipe is process.stdin:
                        try:
                            written = os.write(process.stdin.fileno(), pending[:65536])
                            pending = pending[written:]
                        except BrokenPipeError:
                            pending = memoryview(b"")
                        if not pending:
                            selector.unregister(process.stdin)
                            process.stdin.close()
                        continue
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(ready_pipe)
                        if ready_pipe is process.stdout and line:
                            consume(bytes(line))
                            line.clear()
                        continue
                    if ready_pipe is process.stderr:
                        stderr.extend(data)
                        del stderr[:-65536]
                        continue
                    last_activity = time.monotonic()
                    if first_output is None:
                        first_output = last_activity - started
                    line.extend(data)
                    while b"\n" in line:
                        raw, _, rest = line.partition(b"\n")
                        if len(raw) > 2_000_000:
                            raise ClaudeRunError("agent CLI output exceeds size limit")
                        if raw.strip():
                            consume(bytes(raw))
                        line = bytearray(rest)
                    if len(line) > 2_000_000:
                        raise ClaudeRunError("agent CLI output exceeds size limit")
            return subprocess.CompletedProcess(args, process.wait(), final, stderr.decode(errors="replace"))
        except BaseException:
            _terminate(process)
            raise
        finally:
            if timing is not None:
                timing({"streamSeconds": round(time.monotonic() - started, 3), "firstOutputSeconds": round(first_output or 0, 3)})


def report_agent_timing(svc: Services, job_id: str, metrics: dict[str, float]) -> None:
    """Numbers only: time to the first model output and total stream time, including interrupted runs."""
    try:
        with svc.db.transaction():
            svc.event(job_id, "agent_timing", "system", dict(metrics))
    except Exception:  # noqa: BLE001 - telemetry cannot turn a completed application into a retry
        logging.getLogger(__name__).debug("agent timing unavailable")


def progress_monitor(svc: Services, job_id: str, check: Callable[[], None]) -> tuple[Callable[[], None], Callable[[str], None]]:
    """(per-tick check, throttled progress publisher). Progress text is fixed by the stream parser, never page-derived."""
    last_publish = 0.0

    def publish(message: str) -> None:
        nonlocal last_publish
        now = time.monotonic()
        if now - last_publish >= PROGRESS_SECONDS:
            last_publish = now
            try:
                svc.agent_progress(job_id, message)
            except Exception:  # noqa: BLE001 - a status line must never stop the run
                logging.getLogger(__name__).debug("agent progress unavailable")

    return check, publish


def child_environment(node: str | None = None) -> dict[str, str]:
    """Only OS essentials and the Claude login. The CLI reads `CLAUDE_CODE_OAUTH_TOKEN` itself (a container has no
    keychain); the agent has no shell, so it cannot read its own environment."""
    user = getpass.getuser()
    env = {
        "HOME": str(Path.home()),
        "USER": user,
        "LOGNAME": user,
        "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin",
        "LANG": "en_US.UTF-8",
    }
    if node:
        env["JOBPILOT_NODE"] = node
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


def run_failure(returncode: int, stderr: str, envelope: Mapping[str, object] | None) -> ClaudeRunError | None:
    """Never inspect result/stdout prose: it may contain hostile page content."""
    if returncode != 0 and re.search(
        r"No conversation found with session ID|Failed to resume session|Unable to load transcript", stderr, re.I
    ):
        return ClaudeRunError("agent session could not be resumed", reason="resume_failed")
    subtype = envelope.get("subtype") if envelope else None
    if subtype in ("error_max_budget_usd", "error_max_turns"):
        return ClaudeRunError("agent per-run limit exhausted")
    status = envelope.get("api_error_status") if envelope else None
    api_problem = isinstance(status, int) and not isinstance(status, bool) and status >= 400
    if status in (401, 403) or subtype == "error_authentication" or re.search(r"not logged in|/login", stderr, re.I):
        return ClaudeRunError("agent CLI not logged in", environment=True, reason="not_logged_in")
    if status == 429 or re.search(r"usage limit|rate limit|limit reached|out of extra usage", stderr, re.I):
        return ClaudeRunError("agent CLI usage limit reached", environment=True, reason="usage_limit")
    stderr_problem = bool(re.search(r"not logged in|/login|unrecognized_model|api_error", stderr, re.I))
    environment = returncode != 0 or api_problem or stderr_problem or subtype in ("error_api", "error_authentication")
    if environment:
        return ClaudeRunError(f"agent CLI unavailable (exit code {returncode})", environment=True)
    if envelope is None:
        return ClaudeRunError("agent CLI returned invalid JSON")
    if envelope.get("is_error") is True:
        return ClaudeRunError("agent run failed")
    return None


class ClaudeDriver:
    """AgentDriver adapter: one scoped `claude -p` process per job run, driving the job's Neo tab over MCP."""

    name = "claude"

    def __init__(self, cfg: Settings, svc: Services) -> None:
        self.cfg, self.svc = cfg, svc

    def preflight(self, cv: Path) -> None:
        if self.cfg.agent_model_browser != BROWSER_MODEL:
            raise ValueError(f"the browser agent runs only {BROWSER_MODEL}")
        cv.resolve(strict=True)  # Neo reads the upload from this same absolute path on the host

    def run_direct(self, job_id: str, instruction: str, *, worker: str, cv: Path, check: Callable[[], None]) -> str:
        if not self.svc.browser_target(job_id):
            raise ClaudeRunError("assigned job tab is missing")
        self.preflight(cv)  # the runner already ran this before opening the tab; kept so the driver is safe standalone
        from ..service.agent_sessions import AgentSessions, delta_prompt

        sessions = AgentSessions(self.svc)
        session_id, resume, previous = sessions.start(job_id)
        delta, context = delta_prompt(instruction, previous)
        directory = sessions.directory(job_id)
        directory.touch(exist_ok=True)
        try:
            result = self._run(
                job_id,
                delta if resume else instruction,
                model=BROWSER_MODEL,
                check=check,
                session_id=session_id,
                resume=resume,
            )
        except ClaudeRunError as error:
            # Only a CLI startup failure proves no browser action happened. Never repeat a possibly clicked submit.
            if not resume or error.reason != "resume_failed":
                raise
            check()
            session_id = sessions.reset(job_id, "resume_failed")
            result = self._run(job_id, instruction, model=BROWSER_MODEL, check=check, session_id=session_id)
        finally:
            sessions.sanitize(job_id)
        sessions.repo.remember(job_id, session_id, context)
        return result

    def _args(self, model: str, session_id: str, *, resume: bool = False) -> list[str]:
        """No built-in tool at all; the Neo MCP server is the agent's only capability (all of its tools). No
        --safe-mode: it also disables every MCP server, including the explicitly configured one. The remaining flags
        give the same isolation: no settings sources (no CLAUDE.md, hooks, user MCP or plugins), only the server below,
        no slash commands, no Chrome integration and no built-in tools."""
        config = {"mcpServers": {NEO_SERVER: {"type": "http", "url": self.cfg.neo_mcp_url}}}
        args = [
            "claude",
            "-p",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            json.dumps(config, separators=(",", ":")),
            "--no-chrome",
            "--disable-slash-commands",
            "--resume" if resume else "--session-id",
            session_id,
            "--tools",
            "",
            "--model",
            model,
            "--effort",
            self.cfg.agent_effort,
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--allowedTools",
            *NEO_ALLOWED,
            "--disallowedTools",
            "Bash",
            "Read",
            "Edit",
            "Write",
            "WebFetch",
            "WebSearch",
            "--append-system-prompt",
            (self.cfg.home / "agent" / "NEO.md").read_text(),
            "--permission-mode",
            "dontAsk",
        ]
        if self.cfg.browser_budget_usd:
            args.extend(["--max-budget-usd", str(self.cfg.browser_budget_usd)])
        return args

    def _run(
        self, job_id: str, instruction: str, *, model: str, check: Callable[[], None], session_id: str, resume: bool = False
    ) -> str:
        first, _, rest = instruction.partition("\n")
        prompt = first + "\nUse the Neo browser MCP tools. Your tab is the page whose id is DATA target_id.\n" + rest
        env = child_environment(self.cfg.jobpilot_node)
        env["CLAUDE_CONFIG_DIR"] = str(self.cfg.data_dir / "agent-sessions" / job_id)
        monitor, progress = progress_monitor(self.svc, job_id, check)
        timeout: _TimeoutOptions = (
            {"timeout": self.cfg.browser_run_timeout_seconds} if self.cfg.browser_run_timeout_seconds else {}
        )
        try:
            p = run_stream(
                self._args(model, session_id, resume=resume),
                prompt,
                cwd=self.cfg.home / "agent",
                env=env,
                check=monitor,
                stall_seconds=self.cfg.agent_stall_seconds,
                progress=progress,
                timing=lambda metrics: report_agent_timing(self.svc, job_id, metrics),
                **timeout,
            )
        except FileNotFoundError as exc:
            if exc.filename == "claude":
                raise ClaudeRunError("agent CLI binary missing", environment=True, reason="binary_missing") from exc
            raise ClaudeRunError("agent CLI could not start", environment=True) from exc
        if len(p.stdout.encode()) > 2_000_000:
            raise ClaudeRunError("agent CLI output exceeds size limit")
        try:
            parsed = json.loads(p.stdout)
        except json.JSONDecodeError:
            parsed = None
        j = parsed if isinstance(parsed, dict) else None
        if j is not None:
            self._record_cost(job_id, model, j)
        failure = run_failure(p.returncode, p.stderr, j)
        if failure:
            raise failure
        denials = j.get("permission_denials") if j is not None else None
        if isinstance(denials, list) and denials:
            # Count only: denied tool input may carry page-derived text and is never stored.
            raise ClaudeRunError("agent tool permission denied", reason="permission_denied", count=len(denials))
        result = j.get("result") if j is not None else None
        if not isinstance(result, str):
            raise ClaudeRunError("agent result missing")
        return result

    def _record_cost(self, job_id: str, model: str, envelope: Mapping[str, object]) -> None:
        usage = envelope.get("usage")
        usd = envelope.get("total_cost_usd")
        if not (
            isinstance(usage, dict)
            and "input_tokens" in usage
            and "output_tokens" in usage
            and isinstance(usd, (int, float))
            and not isinstance(usd, bool)
        ):
            return
        counts = [
            usage.get(key, 0)
            for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
        ]
        if all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in counts) and math.isfinite(usd) and usd >= 0:
            self.svc.cost(job_id, model, sum(counts[:3]), counts[3], "agent_run", float(usd))

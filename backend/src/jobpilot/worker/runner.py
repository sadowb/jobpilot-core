from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from celery.exceptions import SoftTimeLimitExceeded

from ..boards import adapter_by_id
from ..browser.neo import BrowserUnavailable
from ..browser.tabs import BrowserOffline
from ..browser.verify import check_confirmation
from ..config import Settings
from ..domain.ports import AgentDriver, Clock
from ..domain.posting_form import PostingForm
from ..domain.state_machine import PRE_SUBMIT_ORDER
from ..dto.direct import ResultInvalid, parse_result
from ..repository.automation import AutomationRepository
from ..repository.browser import BrowserRepository
from ..repository.direct import DirectRepository
from ..repository.preflight import PreflightRepository
from ..service import Services
from ..service._support import RunOutcome
from ..service.account_wall import AccountWallService
from ..service.agent_sessions import AgentSessions
from ..service.bulk_fill import BulkFillService
from ..service.direct import DirectService
from ..service.evidence import SubmitEvidenceService
from ..service.park_watch import ParkWatchService
from ..service.posting_prefetch import PostingPrefetch
from ..service.question_planner import QuestionPlanner
from ..service.verification import VerificationService
from .claude import AgentInterrupted, ClaudeRunError
from .clock import SystemClock

RUN_HEARTBEAT_SECONDS = 15  # well below the 600 s lease; no transaction spans the model run
# After an authorized submit run that did not report a confirmation, the server watches the tab this long for one.
SETTLE_SECONDS = 30.0
MAX_RUN_FAILURES = 3  # refused agent results in a row (since the last owner resume) before the owner is asked
SETTLE_INTERVAL_SECONDS = 3.0

log = logging.getLogger("jobpilot.worker")


def _failure_detail(error: Exception) -> str:
    """Store only what is safe: fixed validator messages, field names + error types, a CLI failure reason."""
    if isinstance(error, ResultInvalid):
        return error.detail
    if isinstance(error, ClaudeRunError):
        return f"{error.reason}: {str(error)[:120]}"
    return type(error).__name__


def _offline_detail(error: BaseException) -> str:
    """Class and fixed message of the underlying Neo client error; never page-derived text."""
    cause = error.__cause__ or error
    text = str(cause) if isinstance(cause, BrowserUnavailable) else ""
    return f"{type(cause).__name__}: {text[:100]}" if text else type(cause).__name__


class SessionLost(RuntimeError):
    """Retry a submit run after loss of its tab; the next run uses guarded refill."""


class StopSignal(Protocol):
    def is_set(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class RunEnd:
    outcome: RunOutcome
    delay: int = 5
    refund_attempt: bool = False
    detail: str = field(default="", compare=False)  # why a queued run was released; outcome/delay/refund are the contract


# Requeues that are the owner's own Stop/drain: not worth an event. Everything else records why the run was released.
QUIET_REQUEUE = frozenset({"draining", "automation_stopped"})
# The same reason this many times in a row, with no progress between, stops refunding the attempt: the loop then ends at
# the task's attempt cap and the job is handed to the owner with that reason.
REFUND_STREAK_LIMIT = 5


@dataclass(slots=True)
class _RunUsage:
    model_started: bool = False


class JobRunner:
    """One leased job; the Celery task alone completes and releases the run."""

    def __init__(self, cfg: Settings, svc: Services, driver: AgentDriver, clock: Clock | None = None) -> None:
        self.cfg, self.svc, self.driver = cfg, svc, driver
        self.repository = AutomationRepository(svc.db)
        self.clock = clock or SystemClock()

    def run(self, job_id: str, worker: str, stop: StopSignal) -> RunEnd:
        usage = _RunUsage()
        try:
            self.svc.load_runtime_settings()
            if stop.is_set():
                return RunEnd("queued", detail="draining")
            with self.svc.lease_run(worker):
                if self.svc.job(job_id).get("source") == "platform":
                    return self._run_direct(job_id, worker, stop, usage)
                return self._run_external(job_id)
        except SessionLost:
            return self._requeue(job_id, "tab_lost", 30)
        except BrowserOffline as offline:
            detail = _offline_detail(offline)
            if self._neo_reachable():
                # Neo answers, so this is about one page or tab, not an outage: charge the attempt so it cannot loop.
                return self._requeue(job_id, "browser_page_error", 30, detail=detail)
            return self._requeue(job_id, "browser_offline", 30, refund_attempt=not usage.model_started, detail=detail)
        except SoftTimeLimitExceeded:
            return self._requeue(job_id, "time_limit", 30)
        except Exception as exc:  # noqa: BLE001 - worker boundary
            log.error("job run failed worker=%s job=%s error=%s", worker, job_id, type(exc).__name__)
            if isinstance(exc, subprocess.TimeoutExpired):
                task = self.repository.find_task_attempts(job_id)
                delay = min(300, 15 * 2 ** min(int(task["attempts"]) if task else 1, 5))
                self._safe(lambda: self.svc.event(job_id, "agent_timeout", "system", {"retryAfter": delay}))
                return RunEnd("queued", delay=delay, detail="agent_timeout")
            if isinstance(exc, PermissionError) and str(exc) == "job lease lost":
                log.info("worker no longer owns job=%s", job_id)
                return self._requeue(job_id, "lease_lost")
            if isinstance(exc, ClaudeRunError) and exc.environment:
                message, reason = str(exc), exc.reason
                self._safe(lambda: self.svc.set_automation(False, actor="system", reason=reason))
                self._safe(lambda: self.svc.event(job_id, "agent_unavailable", "system", {"message": message}))
                return RunEnd("queued", detail="agent_unavailable")
            message = str(exc)
            self._safe(lambda: self.svc.event(job_id, "error", "system", {"message": message}))
            self._safe(lambda: self.svc.wait(job_id, "you", f"automation error: {message}"[:200]))
            return RunEnd("blocked")

    def _neo_reachable(self) -> bool:
        try:
            return bool(self.svc.tab_binder().online())
        except Exception:  # noqa: BLE001 - an unanswerable probe counts as an outage
            return False

    def _requeue(
        self, job_id: str, reason: str, delay: int = 5, *, refund_attempt: bool = False, detail: str | None = None
    ) -> RunEnd:
        """Release the job for a retry and say why: an event the job page and the idle reason can show, never silence."""
        payload: dict[str, Any] = {"reason": reason, "retryAfter": delay}
        if detail:
            payload["detail"] = detail
        if refund_attempt:
            try:
                streak = self.repository.count_requeues_since_progress(job_id, reason)
            except Exception:  # noqa: BLE001 - never lose the release over a bookkeeping query
                streak = 0
            if streak >= REFUND_STREAK_LIMIT:
                refund_attempt = False
                payload["refundStopped"] = streak
        if reason not in QUIET_REQUEUE:
            self._safe(lambda: self.svc.event(job_id, "run_requeued", "system", payload))
        return RunEnd("queued", delay=delay, refund_attempt=refund_attempt, detail=reason)

    @staticmethod
    def _safe(fn: Callable[[], Any]) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - cleanup must not lose the outcome
            log.error("cleanup failed: %s", type(exc).__name__)

    def _run_external(self, job_id: str) -> RunEnd:
        """A row the owner applied to outside JobPilot: its details are read from public posting data when it is added,
        and no agent browses for it. Whatever is still missing goes back to the owner to fill in."""
        gaps = self.svc.external_missing(self.svc.job(job_id))
        if gaps:
            self.svc.wait(job_id, "you", f"Couldn't find: {', '.join(gaps)}. Add them on the job page.")
            return RunEnd("blocked")
        return RunEnd("done")

    def _direct_monitor(self, job_id: str, worker: str, stop: StopSignal) -> Callable[[], None]:
        next_beat = next_control = 0.0
        guidance = self.svc.latest_agent_guidance(job_id)
        guidance_at = guidance["ts"] if isinstance(guidance, dict) else None

        def check() -> None:
            nonlocal next_beat, next_control
            if stop.is_set():
                raise AgentInterrupted("draining")
            now = time.monotonic()
            try:
                if now >= next_control:
                    if not self.repository.find_automation_enabled():
                        raise AgentInterrupted("automation_stopped")
                    if self.svc.job(job_id)["status"] not in PRE_SUBMIT_ORDER:
                        raise AgentInterrupted("owner_finished")
                    latest = self.svc.latest_agent_guidance(job_id)
                    if (latest["ts"] if isinstance(latest, dict) else None) != guidance_at:
                        raise AgentInterrupted("guidance_updated")
                    next_control = now + 1
                if now >= next_beat:
                    if stop.is_set():
                        raise AgentInterrupted("draining")
                    self.svc.heartbeat(job_id, worker)
                    next_beat = now + RUN_HEARTBEAT_SECONDS
            except (PermissionError, AgentInterrupted):
                raise
            except Exception:
                # Fail closed on DB outages too: do not keep using a browser without a provable lease.
                raise AgentInterrupted("lease_check_failed") from None

        return check

    def _run_direct(self, job_id: str, worker: str, stop: StopSignal, usage: _RunUsage) -> RunEnd:
        if stop.is_set() or not self.repository.find_automation_enabled():
            return RunEnd("queued", detail="automation_stopped")
        job = self.svc.job(job_id)
        if job["status"] not in ("discovered", "screened", "preparing", "ready", "awaiting_approval"):
            return RunEnd("done")
        if job["status"] == "awaiting_approval":
            # The owner submits in Neo (ADR 0026): no agent run starts on a finished draft, except the one emailed-code
            # verification run the owner's own click made necessary.
            return (
                self._run_verify(job_id, worker, stop, usage)
                if VerificationService(self.svc).pending(job_id)
                else RunEnd("blocked")
            )
        if job["waiting_on"] == "you":
            return RunEnd("blocked")
        if self.svc.board_policy(job["board_id"]) == "manual":
            self.svc.wait(job_id, "you", "apply manually")
            return RunEnd("blocked")
        if self.svc.submit_clicked(job_id):
            self.svc.wait(job_id, "you", "submission uncertain: check the application; no second submit will be attempted")
            return RunEnd("blocked")
        posting_form: PostingForm | None = None
        service = DirectService(self.svc)
        self.svc.heartbeat(job_id, worker)
        monitor = self._direct_monitor(job_id, worker, stop)
        prefetched = PreflightRepository(self.svc.db).done(job_id)
        if not prefetched:

            def selection_started() -> None:
                usage.model_started = True

            prefetch = PostingPrefetch(self.svc)
            try:
                posting_form = prefetch.fetch(job_id)
            except Exception as error:  # noqa: BLE001 - best effort: the browser path covers a failed prefetch
                log.warning("posting prefetch failed job=%s: %s", job_id, type(error).__name__)
                posting_form = None
            if prefetch.duplicate:
                return RunEnd("blocked")
            try:
                selection = service.ensure_track(job_id, monitor, selection_started)
            except AgentInterrupted as error:
                return RunEnd("done") if str(error) == "owner_finished" else self._requeue(job_id, str(error))
            if selection == "blocked":
                return RunEnd("blocked")
            if selection == "retry":
                return self._requeue(job_id, "cv_selection_retry", 30)
            self.svc.heartbeat(job_id, worker)
        cv = service.cv_path(job_id)
        if posting_form is not None:
            try:
                planner = QuestionPlanner(self.svc)
                planned = planner.plan(job_id, posting_form, monitor, selection_started)
                if planner.surface(job_id, posting_form, planned):
                    return RunEnd("blocked")  # required questions only the owner can answer: ask before any tab opens
            except AgentInterrupted as error:
                return RunEnd("done") if str(error) == "owner_finished" else self._requeue(job_id, str(error))
            except (ValueError, ClaudeRunError, subprocess.TimeoutExpired) as error:
                # The browser agent still answers whatever is left; only note why the early pass produced nothing.
                reason = type(error).__name__
                self._safe(lambda: self.svc.event(job_id, "answers_prefetch_failed", "system", {"reason": reason}))
        if stop.is_set() or not self.repository.find_automation_enabled():
            return RunEnd("queued", detail="automation_stopped")
        # The mode is snapshotted once, here, and drives this whole run (ADR 0026): a toggle flipped mid-run only
        # affects the next one. Server-side forced-review reasons are decided by the same call.
        decision = self.svc.submit_mode.decide(job_id)
        auto = decision.mode == "auto"
        self.svc.event(
            job_id,
            "submit_mode",
            "system",
            {"mode": decision.mode, "requested": decision.requested, "override": decision.override, "reason": decision.reason},
        )
        self.svc.event(job_id, "browser_attempt", "system", {"step": "direct", "model": self.cfg.agent_model_browser})
        with self.svc.db.session_lock(job_id, 3):
            try:
                # Everything that can refuse the run without a browser happens before a Neo tab is opened.
                self.driver.preflight(cv)
                self.svc.ensure_tab(job_id)
                prefill = BulkFillService(self.svc).run(job_id, monitor)
                instruction = service.prepare(job_id, cv, prefill=prefill, mode=decision.mode)
                if auto:
                    # Durable no-repeat fence BEFORE the agent may click: whatever happens next, no second submit.
                    with self.svc.db.transaction():
                        self.svc.approvals.begin_auto_submit(job_id, {"requested": decision.requested})
                usage.model_started = True
                self.svc.agent_progress(job_id, "Starting the browser agent.")
                raw = self.driver.run_direct(job_id, instruction, worker=worker, cv=cv, check=monitor)
                monitor()
                self.svc.heartbeat(job_id, worker)
                result = parse_result(raw)
            except (AgentInterrupted, PermissionError) as error:
                if isinstance(error, AgentInterrupted) and str(error) == "owner_finished":
                    return RunEnd("done")
                if auto and not isinstance(error, PermissionError):
                    return self._auto_unresolved(job_id)
                if isinstance(error, AgentInterrupted) and str(error) == "agent_stalled":
                    self._safe(lambda: self.svc.event(job_id, "agent_stalled", "system", {"step": "direct"}))
                if isinstance(error, PermissionError):
                    raise  # preserve the existing lost-lease outcome and diagnostic
                if self._detect_direct_submission(job_id):
                    return RunEnd("blocked")
                return self._requeue(job_id, str(error), 30 if str(error) in ("agent_stalled", "lease_check_failed") else 5)
            except (ValueError, ClaudeRunError) as error:
                if auto:
                    return self._auto_unresolved(job_id)
                if self._detect_direct_submission(job_id):
                    return RunEnd("blocked")
                if isinstance(error, ClaudeRunError) and error.environment:
                    raise
                failed: dict[str, Any] = {"step": "direct", "reason": "invalid_result", "detail": _failure_detail(error)}
                if isinstance(error, ClaudeRunError) and error.reason == "permission_denied":
                    failed = {"step": "direct", "reason": "permission_denied", "count": error.count}
                log.warning("direct run failed job=%s %s", job_id, failed)
                self.svc.event(job_id, "agent_run_failed", "system", failed)
                # One model invocation per task. The same refusal again and again is a loop, not a transient failure:
                # after MAX_RUN_FAILURES since the owner last resumed the job, the owner gets it with the reason.
                self._safe(lambda: AgentSessions(self.svc).reset(job_id, "invalid_result"))  # next run starts from full DATA
                if DirectRepository(self.svc.db).count_events_since(job_id, "agent_run_failed", "resume") >= MAX_RUN_FAILURES:
                    self.svc.wait(
                        job_id,
                        "you",
                        f"The agent's result was refused {MAX_RUN_FAILURES} times: {failed.get('detail', failed['reason'])}"[
                            :200
                        ],
                    )
                    return RunEnd("blocked", detail="agent_run_failed_repeatedly")
                return RunEnd("queued", delay=30, detail="agent_run_failed")
            except subprocess.TimeoutExpired:
                if auto:
                    return self._auto_unresolved(job_id)
                if self._detect_direct_submission(job_id):
                    return RunEnd("blocked")
                raise
            if result.account_wall is not None:
                # The agent stopped at a login wall (ADR 0031): the server types the stored/generated password itself.
                if AccountWallService(self.svc).handle(job_id, result.account_wall, auto=auto) == "retry":
                    return RunEnd("queued", delay=5, refund_attempt=True, detail="account_ready")
                return RunEnd("blocked")
            if result.downgraded:
                self.svc.event(job_id, "direct_result_downgraded", "system", {"questions": result.downgraded})
            if auto and result.status == "submitted":
                # Read-only evidence from the tab before the record closes it; the agent's report alone is not trusted.
                SubmitEvidenceService(self.svc).verify(job_id, polls=2)
            elif result.status != "submitted" and self._detect_direct_submission(job_id, auto=auto):
                return RunEnd("done" if self.svc.job(job_id)["status"] == "submitted" else "blocked")
            if result.status == "filled":
                held = self._hold_incomplete_form(job_id, service, auto=auto)
                if held is not None:
                    return held
            outcome = service.apply(job_id, result, auto=auto)
            if outcome == "reask":
                # Every reported question already has an answer: run again with them in DATA instead of asking the owner.
                return RunEnd("queued", delay=5, refund_attempt=True, detail="answers_reasked")
            if self.svc.job(job_id)["status"] == "awaiting_approval":
                # Baseline of the parked tab for the watcher (ADR 0026); outside the apply transaction, best effort.
                self._safe(lambda: ParkWatchService(self.svc).store_baseline(job_id))
            return RunEnd("done" if outcome == "done" else "blocked")

    def _run_verify(self, job_id: str, worker: str, stop: StopSignal, usage: _RunUsage) -> RunEnd:
        """Kind verify_email_code: read the emailed code from Gmail and type it into the parked tab the owner already
        submitted from. One attempt; every terminal outcome is recorded and nothing is retried (the owner's button may ask
        again). A run that could not start (stop, Neo offline) stays pending and is requeued."""
        verification = VerificationService(self.svc)
        row = BrowserRepository(self.svc.db).find_session_tab(job_id, self.svc.node)
        if not row or not row["target_id"]:
            return (
                RunEnd("done")
                if verification.finish(job_id, None, "the application tab is gone") == "done"
                else RunEnd("blocked")
            )
        service = DirectService(self.svc)
        monitor = self._direct_monitor(job_id, worker, stop)
        self.svc.heartbeat(job_id, worker)
        cv = service.cv_path(job_id)
        self.svc.event(job_id, "browser_attempt", "system", {"step": "verify_email_code", "model": self.cfg.agent_model_browser})
        with self.svc.db.session_lock(job_id, 3):
            self.driver.preflight(cv)
            instruction = verification.instruction(job_id, str(row["target_id"]))
            usage.model_started = True
            self.svc.agent_progress(job_id, "Starting the browser agent.")
            try:
                raw = self.driver.run_direct(job_id, instruction, worker=worker, cv=cv, check=monitor)
                result = parse_result(raw)
            except AgentInterrupted as error:
                if str(error) == "owner_finished":
                    return RunEnd("done")
                outcome = verification.finish(job_id, None, "the agent stopped before it finished")
                return RunEnd("done" if outcome == "done" else "blocked")
            except (ValueError, ClaudeRunError, subprocess.TimeoutExpired) as error:
                if isinstance(error, ClaudeRunError) and error.environment:
                    raise
                outcome = verification.finish(job_id, None, "the agent's report could not be read")
                return RunEnd("done" if outcome == "done" else "blocked")
        outcome = verification.finish(job_id, result)
        return RunEnd("done" if outcome == "done" else "blocked")

    def _hold_incomplete_form(self, job_id: str, service: DirectService, *, auto: bool) -> RunEnd | None:
        """The agent said `filled` but the page visibly is not: do not park it. One automatic retry with the list in DATA,
        then Needs You with the same list. None when the page looks complete (or could not be read)."""
        found = service.audit_filled(job_id)
        if not found:
            return None
        if auto:
            with self.svc.db.transaction():
                self.svc.approvals.release_auto_fence(job_id)  # nothing was clicked; same as apply() for filled
        retry = service.form_retry_available(job_id)
        self.svc.event(job_id, "form_incomplete", "system", {"problems": found, "retry": retry})
        if retry:
            return RunEnd("queued", delay=5, refund_attempt=True, detail="form_incomplete")
        self.svc.wait(job_id, "you", "the agent reported the form filled but it is not: " + "; ".join(found))
        return RunEnd("blocked")

    def _auto_unresolved(self, job_id: str) -> RunEnd:
        """An auto run ended without a usable report after the fence was written: the click may or may not have
        happened. Observe the tab once; a confirmation is recorded, anything else is handed to the owner. Never retried."""
        if self._detect_direct_submission(job_id, auto=True) and self.svc.job(job_id)["status"] == "submitted":
            return RunEnd("done")
        if self.svc.job(job_id)["status"] in PRE_SUBMIT_ORDER and self.svc.job(job_id)["waiting_on"] != "you":
            self.svc.wait(job_id, "you", "submission uncertain: check the parked application; no second submit will be attempted")
        return RunEnd("blocked")

    def _detect_direct_submission(self, job_id: str, *, auto: bool = False) -> bool:
        """Observe known confirmation patterns even on invalid/no-JSON/timeout runs, before a retry. In an auto run a
        confirmation is the expected outcome and is recorded as such; in a review run it is an unapproved submit."""
        if not self.svc.browser_session_present(job_id):
            self.svc.event(job_id, "post_run_check_skipped", "system", {"reason": "tab gone"})
            return False
        try:
            text = DirectService(self.svc).page_text(job_id)
        except (RuntimeError, ValueError):
            self.svc.wait(job_id, "you", "could not inspect page after direct agent run")
            return True
        adapter = adapter_by_id(self.svc.job(job_id)["board_id"])
        if not check_confirmation(text, adapter.confirmation_patterns).ok:
            return False
        with self.svc.db.transaction():
            if auto:
                self.svc.approvals.record_auto_submitted(job_id, None, "Recognized confirmation on the page.")
            else:
                self.svc.approvals.record_agent_submitted(job_id, "Recognized confirmation on the page.")
        return True

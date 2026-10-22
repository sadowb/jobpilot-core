"""Typed entry point shared by HTTP controllers, CLI tools and workers."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

from ..browser.tabs import TabBinder
from ..config import RuntimeSettings, Settings
from ..domain.answers import Known
from ..domain.next_step import JobFacts
from ..domain.state_machine import GateContext
from ..entity.profile import ProfileEntry
from ..projection.cvstore import CvStore
from ..repository.database import Database, Row
from ..repository.park_watch import ParkWatchRepository
from ._support import Dispatch, RunOutcome, transactional
from .agent_guidance import AgentGuidanceService
from .approvals import ApprovalsService
from .automation import AutomationService
from .browser import BrowserService
from .credentials import CredentialService
from .cv import CvService
from .forms import FormsService
from .jobs import JobsService
from .profile import ProfileService
from .projection import ProjectionService
from .reporting import ReportingService
from .submit_mode import SubmitModeService

log = logging.getLogger("jobpilot.service")


class ApplicationService:
    """One write path; feature services share this instance and its transaction connection."""

    _FEED_EVENTS = (
        "ingest", "transition", "wait", "approval", "submit_clicked", "confirmation", "applied_logged", "tracker_import",
        "tracking_updated", "answer_updated", "answer_deleted", "resume", "merged", "merged_into", "profile_updated",
        "fact_created", "fact_updated", "fact_deleted", "automation_changed",
        "details_refreshed", "transition_refused", "error", "agent_unavailable", "agent_timeout", "mode_changed",
    )  # fmt: skip

    def __init__(self, cfg: Settings, db: Database) -> None:
        self._close_backoff: dict[str, tuple[float, int]] = {}  # job -> (retry not before, consecutive failures)
        # Installed by the process that can reach the broker (API: worker.client). None in the CLI and in tests.
        self.dispatch_hook: Callable[[], None] | None = None
        self.cfg = cfg
        self.db = db
        self.store = CvStore(cfg.vault)
        self._lease_worker: ContextVar[str | None] = ContextVar("lease_worker", default=None)
        self.reporting = ReportingService(self)
        self.jobs = JobsService(self)
        self.forms = FormsService(self)
        self.browser = BrowserService(self)
        self.cv = CvService(self)
        self.credentials = CredentialService(self)
        self.approvals = ApprovalsService(self)
        self.submit_mode = SubmitModeService(self)
        self.automation = AutomationService(self)
        self.projection = ProjectionService(self)
        self.profile = ProfileService(self)
        self.agent_guidance = AgentGuidanceService(self)

    @transactional
    def guide_agent(self, job_id: str, message: str) -> None:
        self.agent_guidance.send(job_id, message)

    @transactional
    def agent_progress(self, job_id: str, message: str) -> None:
        self.agent_guidance.progress(job_id, message)

    def latest_agent_guidance(self, job_id: str) -> Row | None:
        return self.agent_guidance.latest(job_id)

    def cleanup_agent_session(self, job_id: str) -> None:
        from .agent_sessions import AgentSessions

        def callback() -> None:
            AgentSessions(self).cleanup(job_id)

        if not self.db.defer(callback):
            callback()

    @property
    def node(self) -> str:
        """Identity of this host in the cluster (JOBPILOT_NODE); stamped on browser sessions, leases and heartbeats."""
        return self.cfg.jobpilot_node

    def get_profile(self) -> list[ProfileEntry]:
        return self.profile.get_profile()

    def event(self, job_id: str | None, kind: str, actor: str, payload: dict[str, Any] | None = None) -> None:
        return self.reporting.event(job_id, kind, actor, payload)

    @transactional
    def cost(
        self,
        job_id: str | None,
        model: str,
        tokens_in: int,
        tokens_out: int,
        step: str,
        usd: float = 0.0,
        from_cache: bool = False,
    ) -> None:
        return self.reporting.cost(job_id, model, tokens_in, tokens_out, step, usd, from_cache)

    def upsert_company(self, name: str) -> int:
        return self.jobs.upsert_company(name)

    @transactional
    def ingest(
        self, url: str, cv_track: str | None = None, cv_mode: str = "base", company: str | None = None, role: str | None = None
    ) -> dict[str, Any]:
        return self.jobs.ingest(url, cv_track, cv_mode, company, role)

    @transactional
    def enqueue(self, job_id: str) -> None:
        return self.jobs.enqueue(job_id)

    def job(self, job_id: str) -> Row:
        return self.jobs.job(job_id)

    def _live(self, job_id: str) -> Row:
        return self.jobs._live(job_id)

    def posting(self, job_id: str) -> str:
        return self.jobs.posting(job_id)

    def form(self, job_id: str) -> Row | None:
        return self.jobs.form(job_id)

    def approval_for(self, job_id: str, draft_hash: str | None) -> str | None:
        return self.jobs.approval_for(job_id, draft_hash)

    def board_policy(self, board_id: str | None) -> str:
        return self.jobs.board_policy(board_id)

    @staticmethod
    def external_missing(job: Row) -> list[str]:
        """Required tracker fields still empty for a job the owner applied to outside the platform."""
        company = (job.get("company_name") or "").strip()
        missing: list[str] = []
        if not company or company.lower() in ("unknown company", "unknown") or re.fullmatch(r"[\d.:]+|localhost", company):
            missing.append("company")
        if not job.get("role") or job["role"] == "(role not captured yet)":
            missing.append("job title")
        if not (job.get("location") or "").strip():
            missing.append("location")
        if not job.get("meta_captured"):
            missing.append("job description")
        return missing

    def _order_state(self, job_id: str) -> tuple[Row, Row | None, JobFacts]:
        return self.jobs._order_state(job_id)

    def facts(self, job_id: str) -> JobFacts:
        return self.jobs.facts(job_id)

    def status(self, job_id: str) -> dict[str, Any]:
        return self.jobs.status(job_id)

    @transactional
    def done(self, job_id: str, worker: str) -> dict[str, bool]:
        return self.jobs.done(job_id, worker)

    def context(self, job_id: str) -> dict[str, Any]:
        return self.jobs.context(job_id)

    @staticmethod
    def _planned_choices(form: Row | None) -> list[dict[str, str]]:
        """Planned values for option-style fields only (dropdown/radio/checkbox): non-sensitive, and the agent needs
        them to drive custom widgets by hand. Free-text values (names, email, phone) are never shown here."""
        if not form or not form["inventory"] or not form["plan"]:
            return []
        kinds = {"custom-select", "select", "radio", "checkbox", "custom-checkbox"}
        by_key = {f["key"]: f for f in form["inventory"]}
        out: list[dict[str, str]] = []
        for item in form["plan"]:
            field = by_key.get(item["key"])
            if field and field.get("type") in kinds:
                out.append({"key": item["key"], "label": str(field.get("label", ""))[:120], "value": str(item["value"])[:120]})
        return out

    @transactional
    def set_meta(
        self,
        job_id: str,
        *,
        company: str | None = None,
        role: str | None = None,
        location: str | None = None,
        work_mode: str | None = None,
        pay: str | None = None,
        deadline: str | None = None,
        posting: str | None = None,
        job_level: str | None = None,
        employment_type: str | None = None,
        company_description: str | None = None,
    ) -> dict[str, Any]:
        return self.jobs.set_meta(
            job_id,
            company=company,
            role=role,
            location=location,
            work_mode=work_mode,
            pay=pay,
            deadline=deadline,
            posting=posting,
            job_level=job_level,
            employment_type=employment_type,
            company_description=company_description,
        )

    def suggest_track(self, posting: str, market: str = "us") -> tuple[str, int] | None:
        return self.jobs.suggest_track(posting, market)

    @transactional
    def set_inventory(
        self, job_id: str, fields: list[dict[str, Any]], *, page: int | None = None, complete: bool = False
    ) -> None:
        return self.forms.set_inventory(job_id, fields, page=page, complete=complete)

    def load_known(self) -> Known:
        return self.forms.load_known()

    @transactional
    def make_plan(self, job_id: str) -> dict[str, Any]:
        return self.forms.make_plan(job_id)

    def require_automation(self, job_id: str) -> None:
        return self.browser.require_automation(job_id)

    def recipe_path(self, board: str) -> Path:
        return self.browser.recipe_path(board)

    @transactional
    def review_recipe(self, suggestion_id: int, accept: bool, text: str | None) -> None:
        return self.browser.review_recipe(suggestion_id, accept, text)

    @transactional
    def answer_set(self, question: str, answer: str, scope: str, source: str, job_id: str | None = None) -> None:
        return self.forms.answer_set(question, answer, scope, source, job_id)

    @transactional
    def answer_suggest(self, question: str, answer: str, job_id: str, source: str) -> bool:
        return self.forms.answer_suggest(question, answer, job_id, source)

    @transactional
    def wait(self, job_id: str, who: str, reason: str) -> None:
        return self.jobs.wait(job_id, who, reason)

    def required_open(self, job_id: str) -> list[str]:
        return self.forms.required_open(job_id)

    @transactional
    def resume(self, job_id: str, *, try_anyway: bool = False) -> None:
        return self.jobs.resume(job_id, try_anyway=try_anyway)

    @transactional
    def acknowledge_unapproved_submit(self, job_id: str) -> None:
        return self.jobs.acknowledge_unapproved_submit(job_id)

    @transactional
    def transition(self, job_id: str, to: str, ctx: GateContext | None = None, actor: str = "system") -> None:
        return self.jobs.transition(job_id, to, ctx, actor)

    @transactional
    def cv_use_base(self, job_id: str) -> dict[str, str]:
        return self.cv.cv_use_base(job_id)

    def read_track_tex(self, track: str) -> str:
        return self.cv.read_track_tex(track)

    def submit_clicked(self, job_id: str) -> bool:
        return self.approvals.submit_clicked(job_id)

    @transactional
    def record_submitted(
        self, job_id: str, kind: Literal["live-verified", "user-confirmed"], text: str, *, user_session: bool = False
    ) -> None:
        return self.approvals.record_submitted(job_id, kind, text, user_session=user_session)

    def owner_submitted(self, job_id: str, *, user_session: bool) -> dict[str, Any]:
        """The owner submitted in the parked Neo tab (ADR 0026). The tab is read once or twice, read-only, for
        confirmation evidence BEFORE the transition closes it; the record is idempotent and user-session only."""
        from .evidence import SubmitEvidenceService  # evidence imports Services; keep the cycle lazy

        if not user_session:
            raise PermissionError("owner-submitted requires a user-session request")
        if self.approvals.repository.find_applications_for_record_submitted(job_id):
            return {"ok": True, "already": True, "confirmationSeen": None}
        if self.job(job_id)["status"] != "awaiting_approval":
            raise PermissionError("There is no prepared application waiting for you to submit.")
        seen = SubmitEvidenceService(self).verify(job_id)
        recorded = self._record_owner_submitted(job_id, seen)
        return {"ok": True, "already": not recorded, "confirmationSeen": seen if recorded else None}

    @transactional
    def set_submit_mode(self, job_id: str, mode: str | None) -> None:
        return self.jobs.set_submit_mode(job_id, mode)

    def submit_mode_info(self, job_id: str) -> dict[str, Any]:
        """What the next run of this job would do and why (the dashboard shows it; the runner decides again at start)."""
        decision = self.submit_mode.decide(job_id)
        return {
            "effective": decision.mode,
            "requested": decision.requested,
            "override": decision.override,
            "reason": decision.reason,
        }

    @transactional
    def _record_owner_submitted(self, job_id: str, confirmation_seen: bool) -> bool:
        return self.approvals.owner_submitted(job_id, user_session=True, confirmation_seen=confirmation_seen)

    def auto_detected_submit(self, job_id: str) -> bool:
        """The parked-tab watcher saw the owner's submit land on a confirmation page (ADR 0026, amended). Server-internal:
        no HTTP route reaches it. Same record as "I submitted it" with method `auto-detected`; the capture is taken
        before the transition closes the tab. Idempotent against the manual button (the loser sees `False`)."""
        from .evidence import SubmitEvidenceService  # evidence imports Services; keep the cycle lazy

        if self.approvals.repository.find_applications_for_record_submitted(job_id):
            return False
        if self.job(job_id)["status"] != "awaiting_approval":
            return False
        SubmitEvidenceService(self).capture(job_id, confirmation_seen=True)
        return self._record_auto_detected(job_id)

    @transactional
    def _record_auto_detected(self, job_id: str) -> bool:
        return self.approvals.record_owner_submit(job_id, True, "auto-detected")

    def dismiss_park_signal(self, job_id: str, *, user_session: bool) -> None:
        """The owner says a "looks submitted" signal was wrong: forget it and take a fresh baseline of the tab."""
        if not user_session:
            raise PermissionError("dismissing a signal requires a user-session request")
        if self.job(job_id)["status"] != "awaiting_approval":
            raise PermissionError("This application is not waiting for you to submit.")
        from .park_watch import ParkWatchService  # park_watch imports evidence, which imports Services

        self._clear_park_signal(job_id)
        ParkWatchService(self).store_baseline(job_id)

    @transactional
    def _clear_park_signal(self, job_id: str) -> None:
        self.event(job_id, "park_signal_dismissed", "user")
        ParkWatchRepository(self.db).clear_signal(job_id)

    @transactional
    def reopen_application(self, job_id: str, *, user_session: bool) -> None:
        """The owner closed the parked tab without submitting: send the job back for a fresh preparation run."""
        if not user_session:
            raise PermissionError("reopening requires a user-session request")
        self.jobs.reopen_parked(job_id)

    def refresh_details(self, job_id: str, *, user_session: bool) -> dict[str, Any]:
        from .details import DetailsService  # details imports Services; keep the cycle lazy

        return DetailsService(self).request(job_id, user_session=user_session)

    def refresh_missing_details(self, *, user_session: bool) -> dict[str, Any]:
        from .details import DetailsService

        return DetailsService(self).request_missing(user_session=user_session)

    def pump_details(self, publish: Callable[[str], object]) -> int:
        from .details import DetailsService

        return DetailsService(self).pump(publish)

    def run_details(self, job_id: str) -> str:
        from .details import DetailsService

        return DetailsService(self).run(job_id)

    def details_state(self, job_id: str) -> dict[str, Any] | None:
        from .details import DetailsService

        return DetailsService(self).state(job_id)

    def request_email_code(self, job_id: str, *, user_session: bool) -> dict[str, bool]:
        """The owner clicked Submit in the parked tab and the site wants an emailed code: start the verification run
        (ADR 0026, amended). User-session only; `started` is False when a run is already open for this challenge."""
        from .verification import VerificationService  # verification imports Services; keep the cycle lazy

        if not user_session:
            raise PermissionError("requesting the email code requires a user-session request")
        return {"ok": True, "started": VerificationService(self).request(job_id, "owner")}

    def claim(self, worker: str, lease_seconds: int = 600) -> str | None:
        return self.automation.claim(worker, lease_seconds)

    def heartbeat(self, job_id: str, worker: str, lease_seconds: int = 600) -> None:
        return self.automation.heartbeat(job_id, worker, lease_seconds)

    @transactional
    def release(self, job_id: str, worker: str, state: str = "queued", delay: int = 5, *, refund_attempt: bool = False) -> None:
        return self.automation.release(job_id, worker, state, delay, refund_attempt=refund_attempt)

    def project(self, job_id: str) -> None:
        return self.projection.project(job_id)

    def reproject(self, job_id: str) -> bool:
        return self.projection.reproject(job_id)

    def sweep_projections(self, limit: int = 100) -> int:
        return self.projection.sweep_projections(limit)

    def _write_projection(self, job_id: str) -> None:
        return self.projection._write_projection(job_id)

    @transactional
    def set_job_settings(self, job_id: str, track: str | None, mode: str | None) -> None:
        return self.jobs.set_job_settings(job_id, track, mode)

    @transactional
    def set_job_answers(self, job_id: str, answers: list[tuple[str, str, str]], *, resume: bool = True) -> None:
        return self.forms.set_job_answers(job_id, answers, resume=resume)

    @transactional
    def owner_completed(self, job_id: str, *, user_session: bool) -> dict[str, Any]:
        return self.forms.owner_completed(job_id, user_session=user_session)

    @transactional
    def invalidate_form(self, job_id: str, *, reset_plan: bool = False, agent_edit: bool = False) -> None:
        return self.forms.invalidate_form(job_id, reset_plan=reset_plan, agent_edit=agent_edit)

    @transactional
    def record_fill(self, job_id: str, failures: int) -> None:
        return self.forms.record_fill(job_id, failures)

    @transactional
    def record_verification(self, job_id: str, draft: str | None, payload: dict[str, Any]) -> None:
        return self.forms.record_verification(job_id, draft, payload)

    @transactional
    def profile_set(self, key: str, value: str, source: str, *, replan: bool = True) -> None:
        return self.profile.profile_set(key, value, source, replan=replan)

    @transactional
    def profile_section_set(self, section: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.profile.profile_section_set(section, entries)

    @transactional
    def profile_clear(self, key: str) -> None:
        return self.profile.profile_clear(key)

    def replan_blocked(self) -> int:
        return self.forms.replan_blocked()

    @transactional
    def fact_confirm(self, fact_id: str, confirmed: bool, claim: str | None) -> None:
        return self.profile.fact_confirm(fact_id, confirmed, claim)

    @transactional
    def board_update(
        self,
        board_id: str,
        automation: str | None,
    ) -> None:
        return self.profile.board_update(board_id, automation)

    def _defer_close(self, job_id: str) -> None:
        return self.browser._defer_close(job_id)

    def browser_session_present(self, job_id: str) -> bool:
        return self.browser.browser_session_present(job_id)

    def browser_is_open(self, job_id: str) -> bool:
        return self.browser.browser_is_open(job_id)

    def ensure_tab(self, job_id: str) -> None:
        return self.browser.ensure_tab(job_id)

    def browser_target(self, job_id: str) -> str | None:
        return self.browser.target_id(job_id)

    def drop_tab(self, job_id: str) -> None:
        return self.browser.drop_tab(job_id)

    def tab_binder(self) -> TabBinder:
        return self.browser.tabs()

    @contextmanager
    def browser_lock(self, job_id: str) -> Iterator[None]:
        yield from self.browser.browser_lock(job_id)

    def close_browser(self, job_id: str) -> None:
        return self.browser.close_browser(job_id)

    def _close_browser_locked(self, job_id: str) -> None:
        return self.browser._close_browser_locked(job_id)

    def sweep_browsers(self) -> None:
        return self.browser.sweep_browsers()

    def queue_cv_job(self, job_id: str, *, include_pdf: bool = False) -> None:
        return self.cv.queue_cv_job(job_id, include_pdf=include_pdf)

    def queue_cv_version(self, version: int, *, include_pdf: bool = False) -> None:
        return self.cv.queue_cv_version(version, include_pdf=include_pdf)

    @transactional
    def register_track(
        self, track: str, title: str, keywords: list[str], mode: str, obj_sha: str, tex_sha: str, market: str = "any"
    ) -> int:
        return self.cv.register_track(track, title, keywords, mode, obj_sha, tex_sha, market)

    @transactional
    def register_tailored(self, job_id: str, obj_sha: str, tex_sha: str, cache_key: str) -> int:
        return self.cv.register_tailored(job_id, obj_sha, tex_sha, cache_key)

    @transactional
    def use_tailored(self, job_id: str, obj_sha: str, version_id: int) -> None:
        return self.cv.use_tailored(job_id, obj_sha, version_id)

    def sweep_cv_commits(self, limit: int = 100) -> None:
        # Re-read queue rows under the lock: an API and worker cannot commit a stale snapshot.
        return self.cv.sweep_cv_commits(limit)

    def _sweep_cv_commits(self, limit: int) -> None:
        return self.cv._sweep_cv_commits(limit)

    def runtime_settings(self) -> RuntimeSettings:
        return self.automation.runtime_settings()

    def load_runtime_settings(self) -> RuntimeSettings:
        return self.automation.load_runtime_settings()

    @transactional
    def save_runtime_settings(self, value: RuntimeSettings) -> None:
        return self.automation.save_runtime_settings(value)

    def list(
        self, status: str | None = None, waiting_on: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Row]:
        return self.reporting.list(status, waiting_on, q, limit, offset)

    def stats(self) -> dict[str, Any]:
        return self.reporting.stats()

    def verification_gates(self, job_id: str) -> GateContext:
        return self.approvals.verification_gates(job_id)

    @contextmanager
    def lease_run(self, worker: str) -> Iterator[None]:
        yield from self.automation.lease_run(worker)

    def pace_browser(self, job_id: str, completion: bool = False, *, domain: str | None = None) -> None:
        return self.automation.pace_browser(job_id, completion, domain=domain)

    @transactional
    def set_automation(self, enabled: bool, *, actor: str = "user", reason: str | None = None) -> None:
        return self.automation.set_automation(enabled, actor=actor, reason=reason)

    @transactional
    def configure_runtime(self, value: RuntimeSettings) -> None:
        return self.automation.configure_runtime(value)

    def publish_worker(self, name: str, status: dict[str, Any], *, node: str | None = None) -> None:
        """Heartbeat of one worker process; `node` is the host it runs on (worker_processes.node, ADR 0012)."""
        return self.automation.publish_worker(name, status, node)

    def request_dispatch(self) -> None:
        """Ask the execution layer for a dispatch pass soon. Never raises: PostgreSQL is already authoritative and the
        5 s beat safety net picks the work up if the broker is down. Inside a transaction the call is deferred until the
        outermost commit, so a dispatcher never races an uncommitted claim."""
        if self.dispatch_hook is None:
            return
        if not self.db.defer(self._fire_dispatch_hook):
            self._fire_dispatch_hook()

    def _fire_dispatch_hook(self) -> None:
        hook = self.dispatch_hook
        if hook is None:
            return
        try:
            hook()
        except Exception as exc:  # broker outage, serialization, misconfiguration: never fail the caller's write
            log.warning("dispatch request failed (%s: %s); the next beat tick will pick the work up", type(exc).__name__, exc)

    def dispatch_claim(self, node: str, *, limit: int | None = None) -> Sequence[Dispatch]:
        """Admit queued jobs for the Celery producer in ONE transaction and return them to publish after commit.

        Takes the `automation` row lock (same as start/stop/configure) and applies, in order: automation flag,
        attempts < 5, per-domain pacing, `workerConcurrency` (live leases cluster-wide) and
        `maxOpenBrowsers` (preparation jobs only; approved submit runs and jobs that already hold a session are always
        admitted). At most `limit` rows (default: the free capacity). Each row becomes state='leased' with a fresh
        `leased_by` task id and a `DISPATCH_LEASE_SECONDS` lease. A job whose parked browser lives on an online node is
        returned with that node (queue node.<name>); one parked on a node that is offline but inside
        `NODE_TAKEOVER_SECONDS` is held back; past it, it goes to the shared queue. `node` is the dispatching host.
        `claim()` stays for `jp next` and obeys the same admission."""
        return self.automation.dispatch_claim(node, limit=limit)

    @transactional
    def dispatch_failed(self, job_id: str, task_id: str, reason: str) -> None:
        """Publishing `run_job` failed: return the lease to `queued` and refund the attempt, only while `leased_by` is
        still `task_id` and the run has not started. Records a `dispatch_failed` event with `reason`."""
        return self.automation.dispatch_failed(job_id, task_id, reason)

    def start_run(self, job_id: str, task_id: str, node: str) -> bool:
        """Atomic start of a delivered `run_job`: UPDATE tasks SET started_at=now(), leased_node=node, lease extended to
        `RUN_LEASE_SECONDS` WHERE job_id AND leased_by=task_id AND state='leased' AND started_at IS NULL.
        True: this delivery owns the run. False: duplicate or stale delivery, or this node is at `maxOpenBrowsers` for a
        preparation run (released `queued`, attempt refunded); the caller must exit without side effects."""
        return self.automation.start_run(job_id, task_id, node)

    @transactional
    def finish_run(
        self, job_id: str, task_id: str, outcome: RunOutcome, seconds: float, *, delay: int = 5, refund_attempt: bool = False
    ) -> None:
        """End a run owned by `task_id`: release the lease with `outcome` (done-gate as `release`: raises
        PermissionError while work remains), write `task_finished` (task id, node, outcome, seconds) and close or park
        the browser as `release` does; `queued` wakes the dispatcher. When the lease is no longer `task_id`'s (the agent
        ran `jp done`, or it expired and moved on) nothing is released but `task_finished` is still recorded."""
        return self.automation.finish_run(job_id, task_id, outcome, seconds, delay=delay, refund_attempt=refund_attempt)

    def updates(self, limit: int = 30, before: int | None = None) -> dict[str, Any]:
        return self.reporting.updates(limit, before)

    def automation_status(self) -> dict[str, Any]:
        return self.reporting.automation_status()


# Keep the established CLI/worker import compatible.
Services = ApplicationService

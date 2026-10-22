"""Automation operations behind the shared Services facade."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, Any, Literal, cast

from ..config import RuntimeSettings
from ..domain.canonical import pacing_hostname
from ..domain.next_step import next_step
from ..repository.automation import AutomationRepository
from ..repository.database import J, Row
from ..worker.names import DISPATCH_LEASE_SECONDS, NODE_TAKEOVER_SECONDS, RUN_LEASE_SECONDS
from ._support import LIVE_SETTINGS, Dispatch, RunOutcome, is_local_host

HEARTBEAT_FRESH_SECONDS = 20  # a node heartbeat older than this is offline (contract: worker/tasks.py node_ping, every 5 s)
DISPATCH_RETRY_SECONDS = 5  # a failed publish is retried on the next beat tick
CAP_RETRY_SECONDS = 15  # a run bounced by the node's browser cap waits this long before it is dispatched again

if TYPE_CHECKING:
    from .application import Services


class AutomationService:
    def __init__(self, application: Services) -> None:
        self.application = application
        self.repository = AutomationRepository(application.db)

    def open_browsers(self) -> int:
        """Tabs occupying the cluster now: this node's and every node healthy inside the takeover window."""
        _, recent = self._node_state()
        return self.repository.count_open_browsers(sorted(recent | {self.application.node}), self.application.node)

    def _admission(self) -> tuple[RuntimeSettings, int, int]:
        """(settings, free job slots, open browsers) as seen by the caller holding the `automation` row lock."""
        settings = self.application.runtime_settings()
        return settings, self.admission_cap(settings) - self.repository.count_active_leases(), self.open_browsers()

    def admission_cap(self, settings: RuntimeSettings) -> int:
        """Agents allowed at once: the owner's workerConcurrency, never above the pool slots that really exist. A lease
        without a free pool child would idle until it lapsed, so admission follows the measured capacity. With no job
        worker alive there is nothing to measure and the setting stands (jobs wait queued)."""
        capacity = self.repository.sum_job_slots(HEARTBEAT_FRESH_SECONDS)
        return min(settings.workerConcurrency, capacity) if capacity > 0 else settings.workerConcurrency

    def _claim_one(
        self,
        worker: str,
        lease_seconds: int,
        capture_only: bool,
        *,
        node: str | None,
        allow_new_browser: bool,
        skip_nodes: Sequence[str],
        session_nodes: Sequence[str],
        in_process: bool,
    ) -> Row | None:
        # Admission never waits for a quiet domain or adds pacing debt. The first browser action
        # reserves its turn through pace_browser, just like every later action.
        r = self.repository.claim_next_task(
            worker,
            lease_seconds,
            capture_only,
            current_node=self.application.node,
            node=node,
            allow_new_browser=allow_new_browser,
            skip_nodes=skip_nodes,
            session_nodes=session_nodes,
            charge_attempt=in_process,
        )
        return r

    def _begin_admission(self) -> bool | None:
        """Shared preamble of every claimer. Returns capture_only, or None when nothing may be admitted right now."""
        # Same lock as start/stop/configure: no claim can commit after Stop commits.
        control = self.repository.find_automation_for_configure_runtime()
        # Automation off: only read-only tracker capture for jobs the owner already applied to (nothing is submitted).
        capture_only = not control or not control["enabled"]
        exhausted = self.repository.block_exhausted_tasks()
        for task in exhausted:
            why = self.repository.find_last_requeue_reason(task["job_id"])
            self.application.wait(
                task["job_id"], "you", f"automation retry limit reached: {why}" if why else "automation retry limit reached"
            )
        return capture_only

    def _node_state(self) -> tuple[set[str], set[str]]:
        """(nodes whose job worker is alive, nodes healthy inside the takeover window)."""
        rows = self.repository.list_node_liveness(HEARTBEAT_FRESH_SECONDS, NODE_TAKEOVER_SECONDS)
        return {r["node"] for r in rows if r["alive"]}, {r["node"] for r in rows if r["recent"]}

    def _browser_ready(self, alive: set[str]) -> bool:
        """False only when job workers are up but none of those nodes can reach Neo: nothing can start
        a browser, so admitting would only churn leases. No live worker keeps the old behaviour (jobs wait queued)."""
        if not alive:
            return True
        return bool(alive & set(self.repository.list_browser_online_nodes(HEARTBEAT_FRESH_SECONDS)))

    def claim(self, worker: str, lease_seconds: int = 600) -> str | None:
        """Claimers outside Celery (`jp next`) obey the persisted switch, pacing, concurrency and browser cap."""
        with self.application.db.transaction():
            capture_only = self._begin_admission()
            if capture_only is None:
                return None
            settings, free, open_browsers = self._admission()
            if free <= 0:
                return None
            own = self.application.node
            if not self.application.tab_binder().online():
                return None  # in-process claimers run a browser right here: no Neo, no run
            alive, recent = self._node_state()
            # A job parked on another node cannot be run from here while that node can still take it back.
            skip = sorted((alive | recent) - {own})
            r = self._claim_one(
                worker,
                lease_seconds,
                capture_only,
                node=own,
                allow_new_browser=open_browsers < settings.maxOpenBrowsers,
                skip_nodes=skip,
                session_nodes=[own],
                in_process=True,
            )
            return r["job_id"] if r else None

    def dispatch_claim(self, node: str, *, limit: int | None = None) -> Sequence[Dispatch]:
        """Admit queued jobs for the Celery producer in one transaction; the caller publishes them after commit."""
        out: list[Dispatch] = []
        with self.application.db.transaction():
            capture_only = self._begin_admission()
            if capture_only is None:
                return out
            settings, free, open_browsers = self._admission()
            if limit is not None:
                free = min(free, limit)
            alive, recent = self._node_state()
            if not self._browser_ready(alive):
                return out  # backpressure (reported as "Neo offline"); no attempt is spent
            waiting = sorted(recent - alive)  # offline but inside the takeover window: hold their jobs, do not duplicate them
            while len(out) < free:
                task_id = str(uuid.uuid4())
                r = self._claim_one(
                    task_id,
                    DISPATCH_LEASE_SECONDS,
                    capture_only,
                    node=None,
                    allow_new_browser=open_browsers < settings.maxOpenBrowsers,
                    skip_nodes=waiting,
                    session_nodes=sorted(alive),
                    in_process=False,
                )
                if not r:
                    break
                if not r["has_session"]:
                    open_browsers += 1
                holder = r["session_node"]
                out.append(Dispatch(r["job_id"], task_id, holder if holder in alive else None))
        return out

    def dispatch_failed(self, job_id: str, task_id: str, reason: str) -> None:
        changed = self.repository.update_tasks_for_dispatch_failed(job_id, task_id, DISPATCH_RETRY_SECONDS)
        if changed:
            self.application.event(job_id, "dispatch_failed", "system", {"task": task_id, "reason": reason[:200]})

    def start_run(self, job_id: str, task_id: str, node: str) -> bool:
        with self.application.db.transaction():
            # Count-then-start must not interleave between prefork children of one node (the browser cap below).
            self.repository.lock_node_for_start(node)
            row = self.repository.update_tasks_for_start_run(job_id, task_id, node, RUN_LEASE_SECONDS)
            if not row:
                return False  # duplicate delivery, stale task id, or the lease moved on
            if not self.application.tab_binder().online():
                # Neo went away since dispatch (crash, restart): hand the job back, nothing is failed or charged.
                self.repository.release_unstarted_for_cap(job_id, task_id, CAP_RETRY_SECONDS)
                self.application.event(
                    job_id, "start_deferred", "system", {"task": task_id, "node": node, "reason": "Neo offline"}
                )
                return False
            if not row["has_session"]:
                limit = self.application.runtime_settings().maxOpenBrowsers
                if self.repository.count_node_browsers(node, job_id) >= limit:
                    # Parked forms filled this node up since dispatch: hand the job back, attempt refunded.
                    self.repository.release_unstarted_for_cap(job_id, task_id, CAP_RETRY_SECONDS)
                    self.application.event(job_id, "start_deferred", "system", {"task": task_id, "node": node, "limit": limit})
                    return False
        return True

    def finish_run(
        self, job_id: str, task_id: str, outcome: RunOutcome, seconds: float, *, delay: int = 5, refund_attempt: bool = False
    ) -> None:
        self.repository.lock_run_for_finish(task_id)
        if self.repository.has_finished_run(job_id, task_id):
            return
        lease = self.repository.find_task_lease(job_id, task_id)
        if lease and outcome == "done" and next_step(self.application.facts(job_id)) != "idle":
            raise PermissionError("done gate closed: work remains")
        if lease:
            self.repository.update_tasks_for_finish_run(outcome, refund_attempt, delay, job_id, task_id)
            self.application.event(job_id, "lease_released", "system", {"worker": task_id, "state": outcome})
            self.application._defer_close(job_id)
        # Recorded even when the agent already released the lease itself (`jp done`): the dashboard counters read this.
        self.application.event(
            job_id,
            "task_finished",
            "system",
            {
                "task": task_id,
                "node": lease["node"] if lease else None,
                "outcome": outcome,
                "seconds": round(seconds, 1),
                "refunded": bool(refund_attempt),
                "released": bool(lease),
            },
        )
        if lease and outcome == "queued":
            self.application.request_dispatch()

    def heartbeat(self, job_id: str, worker: str, lease_seconds: int = 600) -> None:
        updated = self.repository.update_tasks_for_heartbeat(lease_seconds, job_id, worker)
        if not updated:
            raise PermissionError("job lease lost")

    def release(self, job_id: str, worker: str, state: str = "queued", delay: int = 5, *, refund_attempt: bool = False) -> None:
        if state == "done" and next_step(self.application.facts(job_id)) != "idle":
            raise PermissionError("done gate closed: work remains")
        changed = self.repository.update_tasks_for_release(state, refund_attempt, state, delay, job_id, worker)
        if changed:
            self.application.event(job_id, "lease_released", "system", {"worker": worker, "state": state})
            self.application._defer_close(job_id)
            if state == "queued":
                self.application.request_dispatch()

    def runtime_settings(self) -> RuntimeSettings:
        defaults = RuntimeSettings(
            workerConcurrency=self.application.cfg.worker_concurrency,
            maxOpenBrowsers=self.application.cfg.max_open_browsers,
            cvModel=cast(Literal["claude-haiku-5-5"], self.application.cfg.agent_model_cv),
            effort=self.application.cfg.agent_effort,
            browserBudgetUsd=self.application.cfg.browser_budget_usd,
            browserRunTimeoutSeconds=self.application.cfg.browser_run_timeout_seconds,
            agentStallSeconds=self.application.cfg.agent_stall_seconds,
            cvBudgetUsd=self.application.cfg.cv_budget_usd,
            domainGapSeconds=self.application.cfg.domain_gap_seconds,
        )
        row = self.repository.find_runtime_settings_for_runtime_settings()
        stored = {k: v for k, v in (row["value"] if row else {}).items() if k in RuntimeSettings.model_fields}
        return RuntimeSettings.model_validate({**defaults.model_dump(), **stored})

    def load_runtime_settings(self) -> RuntimeSettings:
        value = self.application.runtime_settings()
        self.application.cfg.worker_concurrency = value.workerConcurrency
        self.application.cfg.max_open_browsers = value.maxOpenBrowsers
        self.application.cfg.agent_model_cv = value.cvModel
        self.application.cfg.agent_effort = value.effort
        self.application.cfg.browser_budget_usd = value.browserBudgetUsd
        self.application.cfg.browser_run_timeout_seconds = value.browserRunTimeoutSeconds
        self.application.cfg.agent_stall_seconds = value.agentStallSeconds
        self.application.cfg.cv_budget_usd = value.cvBudgetUsd
        self.application.cfg.domain_gap_seconds = value.domainGapSeconds
        return value

    def save_runtime_settings(self, value: RuntimeSettings) -> None:
        self.repository.insert_runtime_settings_for_save_runtime_settings(J(value.model_dump()))
        self.application.event(None, "settings_updated", "user", {"fields": list(value.model_fields_set)})

    def lease_run(self, worker: str) -> Iterator[None]:
        token = self.application._lease_worker.set(worker)
        try:
            yield
        finally:
            self.application._lease_worker.reset(token)

    def pace_browser(self, job_id: str, completion: bool = False, *, domain: str | None = None) -> None:
        worker = self.application._lease_worker.get()
        if os.environ.get("JP_ROLE") == "agent" and os.environ.get("JP_JOB") == job_id:
            worker = os.environ.get("JP_WORKER")
        if worker:
            self.application.heartbeat(job_id, worker)  # long page replays retain only their owned lease
        job_domain = pacing_hostname(self.application.job(job_id)["pacing_hostname"])
        domain = pacing_hostname(domain) if domain else job_domain
        # Native browser events supply a bare host. Reuse this job's board key only
        # on that same host; redirects to another host keep their own pacing queue.
        if domain.partition("/")[0] == job_domain.partition("/")[0]:
            domain = job_domain
        if is_local_host(domain):
            return  # politeness pacing protects other people's servers; the loopback fixture/dev hosts are ours
        gap = self.application.runtime_settings().domainGapSeconds
        # Independent commit even when submit holds a job transaction; never roll pacing back.
        row = self.repository.reserve_domain_hit(domain, gap)
        if row and row["delay"] > 0:
            time.sleep(row["delay"])

    def set_automation(self, enabled: bool, *, actor: str = "user", reason: str | None = None) -> None:
        if not self.repository.update_automation_for_set_automation(enabled):
            return
        payload: dict[str, Any] = {"enabled": enabled}
        if not enabled and reason in ("binary_missing", "not_logged_in", "usage_limit", "cli_error", "unapproved_submit"):
            payload["reason"] = reason
        self.application.event(None, "automation_changed", actor, payload)
        if enabled:
            self.application.request_dispatch()

    def configure_runtime(self, value: RuntimeSettings) -> None:
        row = self.repository.find_automation_for_configure_runtime()
        active = self.repository.find_tasks_for_configure_runtime()
        current = self.application.runtime_settings()
        # Older dashboards omit new fields; they must not reset configured limits.
        merged = RuntimeSettings.model_validate({**current.model_dump(), **value.model_dump(include=value.model_fields_set)})
        if merged.maxOpenBrowsers < merged.workerConcurrency:
            raise PermissionError(
                f"maxOpenBrowsers ({merged.maxOpenBrowsers}) must be at least workerConcurrency ({merged.workerConcurrency}): "
                "every running agent holds a browser"
            )
        changed = {k for k, v in merged.model_dump().items() if v != getattr(current, k)}
        if ((row and row["enabled"]) or active) and changed - LIVE_SETTINGS:
            raise PermissionError("stop automation and wait for active leases to drain before changing these settings")
        self.application.save_runtime_settings(merged)
        self.application.load_runtime_settings()
        if merged.submitMode != current.submitMode:  # new runs only: a run in flight keeps the mode it started with
            self.application.event(
                None, "mode_changed", "user", {"scope": "global", "from": current.submitMode, "to": merged.submitMode}
            )

    def publish_worker(self, name: str, status: dict[str, Any], node: str | None = None) -> None:
        self.repository.insert_worker_processes_for_publish_worker(name, J(status), node)

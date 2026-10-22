"""Reporting operations behind the shared Services facade."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..repository.database import J, Row
from ..repository.reporting import ReportingRepository
from .automation import HEARTBEAT_FRESH_SECONDS
from .tabs import TabService

if TYPE_CHECKING:
    from .application import Services


# Why a run was released for retry (`run_requeued` event reasons recorded by worker/runner.py), in owner words.
REQUEUE_REASONS = {
    "browser_offline": "Neo was unreachable",
    "browser_page_error": "a job page could not be read by the browser",
    "tab_lost": "the job's Neo tab was lost",
    "tab_missing": "the filled form's Neo tab is gone, so it will be filled again",
    "time_limit": "a run hit the worker time limit",
    "lease_lost": "a worker lost its claim on the job",
    "cv_selection_retry": "the CV could not be chosen yet",
    "agent_stalled": "the agent stopped making progress",
    "lease_check_failed": "the database could not be checked during a run",
    "guidance_updated": "you sent new guidance",
    "runner_error": "the worker hit an unexpected error",
}


class ReportingService:
    def __init__(self, application: Services) -> None:
        self.application = application
        self.repository = ReportingRepository(application.db)

    def event(self, job_id: str | None, kind: str, actor: str, payload: dict[str, Any] | None = None) -> None:
        self.repository.insert_job_events_for_event(job_id, kind, actor, J(payload or {}))

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
        self.repository.insert_cost_ledger_for_cost(job_id, model, tokens_in, tokens_out, usd, step, from_cache)

    def list(
        self, status: str | None = None, waiting_on: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Row]:
        return self.repository.list_jobs(status, waiting_on, q, limit, offset)

    def stats(self) -> dict[str, Any]:
        by = self.repository.list_jobs_for_stats()
        cost = self.repository.find_cost_ledger_for_stats()
        today = self.repository.find_applications_for_stats()
        incidents = self.repository.list_unapproved_submissions()
        return {
            "byStatus": by,
            "cost24h": cost,
            "appliedToday": today["n"] if today else 0,
            "submittedWithoutApproval": [row["id"] for row in incidents],
        }

    def updates(self, limit: int = 30, before: int | None = None) -> dict[str, Any]:
        """Newest-first feed across all jobs, keyset-paginated on the event id."""
        rows = self.repository.event_feed(list(self.application._FEED_EVENTS), limit, before)
        more = len(rows) > limit
        rows = rows[:limit]
        return {"items": rows, "next": rows[-1]["id"] if more and rows else None}

    def event_watermark(self) -> int:
        row = self.repository.find_job_events_for_event_watermark()
        return int(row["n"]) if row else 0

    def job_usage(self, job_id: str) -> Row:
        return self.repository.job_usage(job_id)

    def monitoring(self) -> dict[str, Any]:
        periods = {}
        for row in self.repository.usage_windows():
            periods[row["period"]] = {
                "usd": row["usd"],
                "tokens": row["tokens"],
                "runs": row["runs"],
                "jobs": row["jobs"],
                "averageCostPerJob": row["job_usd"] / row["jobs"] if row["jobs"] else 0.0,
            }
        return {**periods, "queueDepth": self.repository.queue_depth(), "recentFailures": self.repository.recent_failed_runs()}

    def automation_status(self) -> dict[str, Any]:
        row = self.repository.find_automation_for_automation_status()
        enabled = bool(row and row["enabled"])
        settings = self.application.runtime_settings()
        # Liveness contract (ADR 0012): the control worker's heartbeat writes "{node}:control" (only a process consuming
        # from Redis can) and "{node}:jobs" (slots; offline=true when the job worker missed its Celery ping).
        processes = self.repository.list_fresh_worker_processes(HEARTBEAT_FRESH_SECONDS)
        # offline=true: the control worker is alive but did not get its own ping back through Redis (broker down).
        queue_online = any(p["status"].get("role") == "control" and not p["status"].get("offline") for p in processes)
        job_workers = [p["status"] for p in processes if p["status"].get("role") == "jobs" and not p["status"].get("offline")]
        worker_online = bool(job_workers)
        # {node}:browser is written by the control worker's heartbeat: offline=true when Neo did not
        # answer on its debug port (ADR 0014).
        browser_online = any(p["status"].get("role") == "browser" and not p["status"].get("offline") for p in processes)
        capacity = sum(int(w.get("slots", 0)) for w in job_workers)
        active = self.repository.list_active_workers()
        TabService(self.application).annotate(active, key="job")
        browsers_open = self.application.automation.open_browsers()
        reason: str | None = None
        if not enabled:
            stop_reason = {
                "binary_missing": "agent CLI binary missing",
                "not_logged_in": "agent CLI not logged in",
                "usage_limit": "agent CLI usage limit reached",
                "cli_error": "agent CLI unavailable",
                "unapproved_submit": "an application was reported submitted without your approval",
            }.get(str((row or {}).get("stop_reason") or ""))
            reason = f"automation stopped: {stop_reason}" if stop_reason else "automation stopped"
        elif not queue_online:
            reason = "queue offline: the control worker is not heartbeating (docker compose ps: redis, control)"
        elif not worker_online:
            reason = "no job worker online (docker compose up -d worker)"
        elif not browser_online:
            reason = "Neo is not reachable: start BrowserOS neo; approved forms resume once it is back"
        elif browsers_open >= settings.maxOpenBrowsers and self.repository.count_cap_blocked_jobs(self.application.node):
            parked = self.repository.count_parked_forms(self.application.node)
            reason = (
                f"open-browser limit reached ({browsers_open}/{settings.maxOpenBrowsers}): {parked} filled form"
                f"{'' if parked == 1 else 's'} wait for your approval; approve or dismiss them to start more"
            )
        elif (
            not any(run.get("started") for run in active)
            and (requeue := self.repository.find_recent_requeue())
            and requeue["reason"]
        ):
            why = REQUEUE_REASONS.get(str(requeue["reason"]), "an unexpected interruption")
            reason = f"runs are being retried ({requeue['n']} in the last 10 minutes): {why}"
        elif not active:
            reason = "Nothing is waiting for the agent right now."
        runs = self.repository.find_task_outcomes_today() or {}
        finished, failed, running = (int(runs.get(k) or 0) for k in ("finished", "failed", "running"))
        return {
            "running": enabled,
            "concurrency": self.application.automation.admission_cap(settings),
            "active": active,
            "started": int(runs.get("finished_total") or 0) + running,
            "finished": finished,
            "failed": failed,
            "workerOnline": worker_online,
            "idleReason": reason,
            "monitoring": self.monitoring(),
            "workersOnline": len(job_workers),
            "activeRuns": sum(bool(run.get("started")) for run in active),
            "browsersOpen": browsers_open,
            "browserLimit": settings.maxOpenBrowsers,
            "capacity": capacity,
            "queueOnline": queue_online,
            "browserOnline": browser_online,
        }

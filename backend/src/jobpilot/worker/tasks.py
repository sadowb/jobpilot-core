"""Celery delivery adapters and node lifecycle; durable decisions stay in Services."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from celery import Task, signals

from ..config import get_settings
from . import client, maintenance, runtime
from .app import app
from .names import (
    QUEUE_CONTROL,
    QUEUE_JOBS,
    TASK_DETAILS_RUN,
    TASK_DISPATCH,
    TASK_NODE_PING,
    TASK_PARK_WATCH,
    TASK_PARTITIONS,
    TASK_PREFLIGHT,
    TASK_RUN_JOB,
    draining_flag,
    node_queue,
)
from .runner import RunEnd

log = logging.getLogger("jobpilot.worker.tasks")


@dataclass(frozen=True)
class DrainSignal:
    path: Path

    def is_set(self) -> bool:
        return self.path.exists()


@app.task(name=TASK_DISPATCH, queue=QUEUE_CONTROL, soft_time_limit=60, time_limit=60)
def dispatch() -> None:
    rt = runtime.get_runtime()
    from ..repository.preflight import PreflightRepository

    for job_id in PreflightRepository(rt.db).publishable():
        try:
            preflight.apply_async((job_id,), queue=QUEUE_CONTROL, retry=False)
        except Exception as exc:  # noqa: BLE001 - retry publication on a later dispatch tick
            log.warning("preflight publish failed (%s)", type(exc).__name__)
    try:  # "Refresh details" requests (ADR 0029): bounded, no job state involved; never blocks job dispatch
        rt.svc.pump_details(lambda job_id: details_run.apply_async((job_id,), queue=QUEUE_CONTROL, retry=False))
    except Exception as exc:  # noqa: BLE001 - the next tick retries
        log.warning("details pump failed (%s)", type(exc).__name__)
    for claim in rt.svc.dispatch_claim(rt.cfg.jobpilot_node):
        try:
            run_job.apply_async(
                (claim.job_id,), task_id=claim.task_id, queue=node_queue(claim.node) if claim.node else QUEUE_JOBS, retry=False
            )
        except Exception as exc:  # noqa: BLE001 - refund each failed publish and keep relaying
            try:
                rt.svc.dispatch_failed(claim.job_id, claim.task_id, type(exc).__name__)
            except Exception as cleanup:  # noqa: BLE001 - lease expiry is the fallback
                log.error("dispatch refund failed (%s)", type(cleanup).__name__)


@app.task(name=TASK_PREFLIGHT, queue=QUEUE_CONTROL, soft_time_limit=230, time_limit=240)
def preflight(job_id: str) -> None:
    from ..service.preflight import prepare_ingested

    prepare_ingested(runtime.get_runtime().svc, job_id)


@app.task(name=TASK_RUN_JOB, bind=True)
def run_job(self: Task[Any, Any], job_id: str) -> None:
    rt = runtime.get_runtime()
    task_id = self.request.id
    if task_id is None or not rt.svc.start_run(job_id, task_id, rt.cfg.jobpilot_node):
        return
    started = time.monotonic()
    try:
        drain = DrainSignal(draining_flag(rt.cfg.data_dir, rt.cfg.jobpilot_node))
        try:
            end = rt.runner.run(job_id, task_id, drain)
        except Exception as exc:  # noqa: BLE001 - unexpected runner failure still releases the slot
            log.error("runner failed (%s)", type(exc).__name__)
            end = RunEnd("queued", delay=30, detail="runner_error")
            try:
                rt.svc.event(job_id, "run_requeued", "system", {"reason": "runner_error", "retryAfter": 30})
            except Exception as event_exc:  # noqa: BLE001 - the release below matters more than the note
                log.error("requeue event failed (%s)", type(event_exc).__name__)
        seconds = time.monotonic() - started
        try:
            rt.svc.finish_run(job_id, task_id, end.outcome, seconds, delay=end.delay, refund_attempt=end.refund_attempt)
        except Exception as exc:  # noqa: BLE001 - e.g. the done-gate found remaining work
            log.warning("finish run failed (%s); retrying queued", type(exc).__name__)
            try:
                rt.svc.finish_run(job_id, task_id, "queued", seconds, delay=5, refund_attempt=end.refund_attempt)
            except Exception as retry_exc:  # noqa: BLE001 - lease expiry recovers a failed completion
                log.error("finish run retry failed (%s)", type(retry_exc).__name__)
    finally:
        client.request_dispatch()


@app.task(name=TASK_NODE_PING, queue=QUEUE_CONTROL, soft_time_limit=60, time_limit=60)
def node_ping() -> None:
    rt = runtime.get_runtime()
    node = rt.cfg.jobpilot_node
    try:
        # stats answers like ping (liveness) and carries each worker's real pool size, so capacity is measured, not configured.
        replies = app.control.inspect(timeout=1.0).stats() or {}
    except Exception as exc:  # noqa: BLE001 - broker outages must not stop PostgreSQL heartbeats
        log.warning("node ping failed (%s)", type(exc).__name__)
        replies = {}
    control_online = any(name.startswith(f"control-{node}@") for name in replies)
    job_replies = [stats for name, stats in replies.items() if name.startswith(f"jobs-{node}@")]
    jobs_online = bool(job_replies)
    slots = sum(int(((stats or {}).get("pool") or {}).get("max-concurrency") or 0) for stats in job_replies)
    rt.svc.publish_worker(f"{node}:control", {"role": "control", "node": node, "offline": not control_online}, node=node)
    rt.svc.publish_worker(
        f"{node}:jobs",
        {
            "role": "jobs",
            "node": node,
            "slots": slots or rt.cfg.node_slots,
            "workers": len(job_replies),
            "offline": not jobs_online,
        },
        node=node,
    )
    # Neo, the node's browser (ADR 0019): PostgreSQL carries the answer, so dispatch can apply backpressure.
    rt.svc.publish_worker(
        f"{node}:browser", {"role": "browser", "node": node, "offline": not rt.svc.tab_binder().online()}, node=node
    )


@app.task(name=TASK_PARTITIONS, queue=QUEUE_CONTROL, soft_time_limit=60, time_limit=60)
def ensure_partitions() -> None:
    runtime.get_runtime().db.one("SELECT ensure_job_event_partitions(3)")


@app.task(name=TASK_DETAILS_RUN, queue=QUEUE_CONTROL, soft_time_limit=330, time_limit=345)
def details_run(job_id: str) -> None:
    """Re-read one job's posting details (ADR 0029). Tool-less models only; the job's state is never touched."""
    state = runtime.get_runtime().svc.run_details(job_id)
    log.info("details refresh job=%s state=%s", job_id, state)


@app.task(name=TASK_PARK_WATCH, queue=QUEUE_CONTROL, soft_time_limit=45, time_limit=60)
def park_watch() -> None:
    """Read-only look at the tabs of applications parked for the owner (ADR 0026, amended): records a submit the owner
    made in Neo, or raises a Needs You question. No model, no clicks; bounded concurrency and deadline."""
    from ..service.park_watch import ParkWatchService

    rt = runtime.get_runtime()
    counts = ParkWatchService(rt.svc).run_once()
    if counts["detected"] or counts["looks_submitted"] or counts["tab_closed"]:
        log.info("park watch %s", counts)


_stop = threading.Event()
_threads: list[threading.Thread] = []


def _heartbeat_loop() -> None:
    while not _stop.is_set():
        try:
            node_ping()
        except Exception as exc:  # noqa: BLE001 - keep heartbeating after a transient outage
            log.warning("node heartbeat failed (%s)", type(exc).__name__)
        _stop.wait(5)


@signals.worker_ready.connect
def worker_ready(**kwargs: Any) -> None:
    cfg = get_settings()
    if runtime.worker_role() == "jobs":
        draining_flag(cfg.data_dir, cfg.jobpilot_node).unlink(missing_ok=True)
        return
    rt = runtime.get_runtime()
    _stop.clear()
    for target, args, name in (
        (maintenance.sweep_loop, (rt.svc, _stop), "control-maintenance"),
        (_heartbeat_loop, (), "control-heartbeat"),
    ):
        thread = threading.Thread(target=target, args=args, name=name, daemon=True)
        _threads.append(thread)
        thread.start()


@signals.worker_shutting_down.connect
def worker_shutting_down(**kwargs: Any) -> None:
    if runtime.worker_role() == "control":
        _stop.set()
        return
    cfg = get_settings()
    flag = draining_flag(cfg.data_dir, cfg.jobpilot_node)
    flag.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flag.parent.chmod(0o700)
    fd = os.open(flag, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


@signals.worker_shutdown.connect
def worker_shutdown(**kwargs: Any) -> None:
    try:
        if runtime.worker_role() == "control":
            _stop.set()
            for thread in _threads:
                thread.join()
            _threads.clear()
            rt = runtime.get_runtime()
            node = rt.cfg.jobpilot_node
            for role in ("control", "jobs"):
                try:
                    rt.svc.publish_worker(f"{node}:{role}", {"role": role, "node": node, "offline": True}, node=node)
                except Exception as exc:  # noqa: BLE001 - still close the pool after a failed final heartbeat
                    log.warning("offline heartbeat failed (%s)", type(exc).__name__)
    finally:
        runtime.close()


@signals.worker_process_shutdown.connect
def worker_process_shutdown(**kwargs: Any) -> None:
    runtime.close()

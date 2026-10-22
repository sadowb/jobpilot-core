"""Names and timings shared by the producer (API / dispatcher) and the Celery workers.

Frozen contract of docs/adr/0012-celery-redis-execution.md: change it in the ADR first.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config.settings import NODE_PATTERN

TASK_PREFLIGHT = "jobpilot.prepare_ingested"
TASK_DISPATCH = "jobpilot.dispatch"
TASK_RUN_JOB = "jobpilot.run_job"
TASK_NODE_PING = "jobpilot.node_ping"
TASK_PARTITIONS = "jobpilot.ensure_partitions"
TASK_PARK_WATCH = "jobpilot.park_watch"
TASK_DETAILS_RUN = "jobpilot.details_run"

QUEUE_JOBS = "jobs"  # any node may run a job prepared here
QUEUE_CONTROL = "control"  # dispatch, node heartbeat, sweeps

DISPATCH_LEASE_SECONDS = 90  # published but not started: a lost message is re-dispatched after this
RUN_LEASE_SECONDS = 600  # started: extended by heartbeats while the agent works
NODE_TAKEOVER_SECONDS = 300  # a parked approval browser's node offline this long: route its submit to the shared queue
SOFT_TIME_LIMIT = None  # long job runs are stopped by the monitor, not Celery
HARD_TIME_LIMIT = None  # control tasks explicitly retain their 60 s bounds

BEAT_DISPATCH_SECONDS = 5
BEAT_PARK_WATCH_SECONDS = 10  # parked-tab watcher (ADR 0026): read-only, no model

_NODE = re.compile(NODE_PATTERN)


def node_queue(node: str) -> str:
    """Queue consumed only by `node`; carries the submit run of a job whose approval browser is parked there."""
    if not _NODE.fullmatch(node):
        raise ValueError(f"invalid node name {node!r}")
    return f"node.{node}"


def draining_flag(data_dir: Path, node: str) -> Path:
    """Touched on SIGTERM; the runner releases its job `queued` while the subprocess is running when it exists."""
    if not _NODE.fullmatch(node):
        raise ValueError(f"invalid node name {node!r}")
    return data_dir / "run" / f"{node}.draining"

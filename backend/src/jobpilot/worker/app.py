"""Redis delivers job ids; PostgreSQL owns execution state and results."""

import logging
from socket import gethostname

from celery import Celery
from celery.app import trace
from celery.signals import worker_process_init

from ..config import get_settings
from .names import (
    BEAT_DISPATCH_SECONDS,
    BEAT_PARK_WATCH_SECONDS,
    HARD_TIME_LIMIT,
    QUEUE_CONTROL,
    QUEUE_JOBS,
    SOFT_TIME_LIMIT,
    TASK_DETAILS_RUN,
    TASK_DISPATCH,
    TASK_NODE_PING,
    TASK_PARK_WATCH,
    TASK_PARTITIONS,
    TASK_PREFLIGHT,
)

log = logging.getLogger("jobpilot.worker")

app = Celery("jobpilot", broker=get_settings().redis_url, include=["jobpilot.worker.tasks"])
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    task_acks_late=False,
    # run_job may fill an entire application; control tasks have explicit 60 s limits.
    task_soft_time_limit=SOFT_TIME_LIMIT,
    task_time_limit=HARD_TIME_LIMIT,
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=3,
    broker_transport_options={"socket_timeout": 3, "socket_connect_timeout": 3},
    worker_hijack_root_logger=False,
    worker_send_task_events=False,
    worker_enable_remote_control=True,
    task_default_queue=QUEUE_JOBS,
    task_routes={
        name: {"queue": QUEUE_CONTROL}
        for name in (TASK_DISPATCH, TASK_PARTITIONS, TASK_NODE_PING, TASK_PREFLIGHT, TASK_PARK_WATCH, TASK_DETAILS_RUN)
    },
    beat_schedule={
        "dispatch": {"task": TASK_DISPATCH, "schedule": BEAT_DISPATCH_SECONDS, "options": {"expires": 10}},
        "partitions": {"task": TASK_PARTITIONS, "schedule": 86400.0},
        "park_watch": {"task": TASK_PARK_WATCH, "schedule": BEAT_PARK_WATCH_SECONDS, "options": {"expires": 10}},
    },
)


@worker_process_init.connect(weak=False)
def _prime_fast_trace(**_: object) -> None:
    """Pool children started with `spawn` (billiard 4.3 default on macOS; Linux forks) do not inherit the parent's trace
    cache: Celery 5.6 only fills it in the child when FORKED_BY_MULTIPROCESSING is set (celery/concurrency/prefork.py:67),
    so `fast_trace_task` fails to unpack `_localized` (celery/app/trace.py:760). The child also holds a pickled copy of
    the app, not `jobpilot.worker.app.app`, whose tasks `tasks.py` registers; both are rebuilt here against the module
    app. Forked children arrive with the cache set and are left alone."""
    if trace._localized:  # type: ignore[attr-defined]
        return
    hostname = gethostname()
    trace.setup_worker_optimizations(app, hostname)
    for name, task in app.tasks.items():
        task.__trace__ = trace.build_tracer(name, task, app.loader, hostname, app=app)

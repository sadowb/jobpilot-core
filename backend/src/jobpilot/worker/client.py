"""Best-effort dispatch wakeups, bounded during broker outages."""

import logging
import threading
import time

from .app import app
from .names import QUEUE_CONTROL, TASK_DISPATCH

log = logging.getLogger("jobpilot.worker.client")
_lock = threading.Lock()
_blocked_until = 0.0


def request_dispatch() -> None:
    global _blocked_until
    # Concurrent HTTP callers skip an in-progress send rather than queue behind it.
    if not _lock.acquire(blocking=False):
        return
    try:
        if time.monotonic() < _blocked_until:
            return
        try:
            app.send_task(TASK_DISPATCH, queue=QUEUE_CONTROL, expires=10, retry=False)
        except Exception as exc:  # noqa: BLE001 - PostgreSQL writes must survive broker failure
            _blocked_until = time.monotonic() + 30
            log.warning("dispatch broker unavailable (%s); wakeups paused for 30 seconds", type(exc).__name__)
    finally:
        _lock.release()

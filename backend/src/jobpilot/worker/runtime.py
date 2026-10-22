"""Lazy composition, shared by threads and created separately in prefork children."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Literal

from billiard import current_process

from ..config import Settings, get_settings
from ..domain.ports import AgentDriver
from ..repository.database import Database
from ..service import Services
from . import client
from .claude import ClaudeDriver
from .runner import JobRunner


def worker_role() -> Literal["jobs", "control"]:
    role = os.environ.get("JOBPILOT_WORKER_ROLE", "jobs")
    if role == "jobs":
        return "jobs"
    if role == "control":
        return "control"
    raise ValueError("JOBPILOT_WORKER_ROLE must be jobs or control")


@dataclass(frozen=True)
class Runtime:
    cfg: Settings
    db: Database
    svc: Services
    driver: AgentDriver
    runner: JobRunner

    def close(self) -> None:
        self.db.close()


_runtime: Runtime | None = None
_pid: int | None = None
_lock = threading.Lock()


def get_runtime() -> Runtime:
    global _runtime, _pid
    pid = os.getpid()
    with _lock:
        if _pid == pid and _runtime is not None:
            return _runtime
        cfg = get_settings()
        role = worker_role()
        if role == "jobs" and cfg.jobpilot_pool == "prefork" and current_process().name == "MainProcess":
            raise RuntimeError("jobs prefork runtime must be initialized in a child")
        size = 8 if role == "control" else cfg.node_slots + 8 if cfg.jobpilot_pool == "threads" else 4
        db = Database(cfg, pool_size=size)
        svc = Services(cfg, db)
        svc.dispatch_hook = client.request_dispatch
        svc.load_runtime_settings()
        driver = ClaudeDriver(cfg, svc)
        _runtime = Runtime(cfg, db, svc, driver, JobRunner(cfg, svc, driver))
        _pid = pid
        return _runtime


def close() -> None:
    global _runtime, _pid
    with _lock:
        if _pid == os.getpid() and _runtime is not None:
            _runtime.close()
        _runtime, _pid = None, None

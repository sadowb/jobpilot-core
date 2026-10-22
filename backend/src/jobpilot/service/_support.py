"""Shared transaction boundary, constants and exceptions for the service facade."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import TYPE_CHECKING, Concatenate, Literal

from ..domain.canonical import pacing_hostname

if TYPE_CHECKING:
    from .application import Services

log = logging.getLogger("jobpilot.service")


def transactional[**P, T](fn: Callable[Concatenate[Services, P], T]) -> Callable[Concatenate[Services, P], T]:
    @wraps(fn)
    def wrapped(self: Services, /, *args: P.args, **kwargs: P.kwargs) -> T:
        with self.db.transaction():
            return fn(self, *args, **kwargs)

    return wrapped


RunOutcome = Literal["done", "blocked", "queued"]  # how a finished run_job releases its lease


@dataclass(frozen=True, slots=True)
class Dispatch:
    """One lease committed by `dispatch_claim`; the caller publishes `run_job` for it only after the transaction commits."""

    job_id: str
    task_id: str  # Celery task id, stored as tasks.leased_by; a duplicate delivery of another id cannot start the run
    node: str | None  # node holding this job's parked browser (route to queue node.<name>); None = shared `jobs`


LIVE_SETTINGS = frozenset({"workerConcurrency", "maxOpenBrowsers", "domainGapSeconds", "submitMode"})


def is_local_host(host: str | None) -> bool:
    name = pacing_hostname(host or "").partition("/")[0].rstrip(".")
    return name in {"127.0.0.1", "localhost", "::1", "[::1]"} or name.endswith((".localhost", ".test"))

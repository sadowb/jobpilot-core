"""Best-effort ingest preparation, without a browser or a job-worker lease."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from ..repository.preflight import PreflightRepository
from .posting_prefetch import PostingPrefetch
from .question_planner import QuestionPlanner

if TYPE_CHECKING:
    from .application import Services

log = logging.getLogger(__name__)


def prepare_ingested(svc: Services, job_id: str) -> None:
    repo = PreflightRepository(svc.db)
    if not repo.start(job_id):
        return  # duplicate/stale Redis delivery
    deadline = time.monotonic() + 210

    def check() -> None:
        if time.monotonic() >= deadline or not repo.active(job_id):
            raise TimeoutError("ingest preparation expired or job changed")

    success = False
    try:
        check()
        prefetch = PostingPrefetch(svc)
        form = prefetch.fetch(job_id, check, ingest=True)
        if form is None or prefetch.duplicate:
            return
        check()
        planner = QuestionPlanner(svc)
        outcome = planner.plan(job_id, form, check, choose_cv=True)
        with svc.db.transaction():
            planner.direct.lock_plan_write(job_id)
            check()
            planner.surface(job_id, form, outcome)
            repo.finish(job_id, True)
            success = True
    except Exception as exc:  # noqa: BLE001 - the browser path is the fallback, never block on a failed preflight
        log.warning("ingest preparation failed job=%s reason=%s", job_id, type(exc).__name__)
        svc.event(job_id, "ingest_preflight_failed", "system", {"reason": type(exc).__name__})
    finally:
        if not success:
            repo.finish(job_id, False)
        svc.request_dispatch()

"""Before the browser: read what the ATS publishes about the posting and record its metadata.

Best effort and idempotent. Failures and unsupported providers return None and leave the browser path untouched.
Metadata is applied only while the job has no captured posting yet, so an owner edit or an earlier agent capture is
never overwritten by a later fetch."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..domain.posting_form import PostingForm
from ..posting_fetch import fetch_posting_form

if TYPE_CHECKING:
    from .application import Services

log = logging.getLogger("jobpilot.posting_prefetch")


class PostingPrefetch:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.duplicate = False  # set_meta found a probable duplicate and put the job on the owner's list

    def fetch(self, job_id: str, check: Callable[[], None] = lambda: None, *, ingest: bool = False) -> PostingForm | None:
        job = self.svc.job(job_id)
        urls = [u for u in dict.fromkeys([job.get("apply_url"), job.get("url")]) if isinstance(u, str) and u]
        form = next((f for f in (fetch_posting_form(u) for u in urls) if f is not None), None)
        if form is None:
            return None
        meta = form.meta.as_set_meta()
        meta.pop("deadline", None)  # free-form provider text; the column is a date
        if meta and not job.get("meta_captured"):
            from ..repository.direct import DirectRepository

            with self.svc.db.transaction():
                if ingest:
                    DirectRepository(self.svc.db).lock_plan_write(job_id)
                check()
                if self.svc.job(job_id).get("meta_captured"):
                    return form
                result = self.svc.set_meta(job_id, **meta)
                self.duplicate = bool(result.get("duplicateOf"))
                self.svc.event(
                    job_id,
                    "posting_prefetched",
                    "system",
                    {"provider": form.provider, "questions": len(form.questions), "fields": sorted(meta)},
                )
        return form

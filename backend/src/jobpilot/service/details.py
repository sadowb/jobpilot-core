"""Refresh a job's captured details (role, company, location, work mode, pay, level, type) from its posting (ADR 0029).

Allowed for every job, submitted ones included, and it never moves the job: no status, form, approval, submit fence or
`waiting_on` is read-modified here, and no agent runs. The only job columns written are the detail columns, through the
field-by-field merge in `domain/details.py` (placeholders and empties are filled; real values and fields the owner edited
are never replaced). Cheapest source first, each stage only while a gap is still open:

  1. the ATS's public API (free, no model),
  2. the posting text already stored on the job -> one tool-less Haiku extraction,
  3. the public page/JSON-LD -> tool-less Haiku extraction,
  4. read the page in Neo (a server-opened tab: navigate, wait for the text, close that same tab) -> tool-less Haiku.

Stage 4 has no agent: the server drives the tab, so "no clicks, no forms, no account steps" is structural, not a prompt.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ..browser.neo import BrowserError, BrowserUnavailable, neo_browser
from ..browser.tabs import BrowserOffline
from ..clean import clean_posting, sanitize_fields
from ..domain.canonical import norm
from ..domain.details import FIELDS, host_of, missing_fields, plan_merge
from ..extract import extract_posting
from ..posting_fetch import fetch_embedded_greenhouse, fetch_posting_form
from ..repository.details import DetailsRepository

if TYPE_CHECKING:
    from .application import Services

log = logging.getLogger("jobpilot.details")

MAX_RUNNING = 2  # refreshes in flight (also capped by the worker concurrency); the control pool has 4 threads
STALE_SECONDS = 360  # a running row older than this lost its worker
RUN_DEADLINE_SECONDS = 240.0  # between stages; each Haiku attempt is itself bounded
BULK_MAX = 25  # jobs one "Refresh missing details" click may queue
BULK_RETRY_HOURS = 6  # a job whose last refresh finished is not queued again by the bulk button before this
MIN_STORED_CHARS = 100
Publish = Callable[[str], object]


class DetailsService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.repository = DetailsRepository(svc.db)

    # ---------------------------------------------------------------- owner actions
    def request(self, job_id: str, *, user_session: bool) -> dict[str, Any]:
        """Queue one refresh. Idempotent while one is queued or running. Any status is allowed except a deleted job."""
        if not user_session:
            raise PermissionError("refreshing details requires a user-session request")
        with self.svc.db.transaction():
            job = self.repository.lock_job(job_id)
            if not job:
                raise LookupError(f"unknown job {job_id}")
            if job["archived_at"] is not None:
                raise PermissionError("this application is deleted; restore it before refreshing")
            queued = self.repository.request(job_id)
            state = str((self.repository.find_request(job_id) or {}).get("state") or "queued")
        if queued:
            self.svc.request_dispatch()
        return {"ok": True, "already": not queued, "state": state}

    def request_missing(self, *, user_session: bool) -> dict[str, Any]:
        """Queue refreshes for live jobs whose role or company is still a placeholder. Bounded (`BULK_MAX`); the pump then
        runs them at most `MAX_RUNNING` at a time, each paced per employer domain."""
        if not user_session:
            raise PermissionError("refreshing details requires a user-session request")
        queued = 0
        for job_id in self.repository.list_missing(BULK_MAX, BULK_RETRY_HOURS):
            with self.svc.db.transaction():
                if self.repository.lock_job(job_id) and self.repository.request(job_id):
                    queued += 1
        if queued:
            self.svc.request_dispatch()
        return {"ok": True, "queued": queued, "limit": BULK_MAX}

    def state(self, job_id: str) -> dict[str, Any] | None:
        row = self.repository.find_request(job_id)
        if not row:
            return None
        return {"state": row["state"], "via": row["via"], "reason": row["reason"]}

    # ---------------------------------------------------------------- scheduling (beat tick)
    def pump(self, publish: Publish) -> int:
        """Expire lost runs, then start queued ones up to the cap. `publish(job_id)` hands one to the worker."""
        for job_id in self.repository.expire_stale(STALE_SECONDS):
            self.svc.event(job_id, "details_refresh_failed", "system", {"reason": "timeout"})
        capacity = max(1, min(MAX_RUNNING, self.svc.runtime_settings().workerConcurrency))
        with self.svc.db.transaction():
            claimed = self.repository.claim(capacity)
        for job_id in claimed:
            try:
                publish(job_id)
            except Exception as error:  # noqa: BLE001 - back to the queue; the next tick publishes again
                log.warning("details publish failed job=%s (%s)", job_id, type(error).__name__)
                self.repository.requeue(job_id)
        return len(claimed)

    # ---------------------------------------------------------------- the run
    def run(self, job_id: str) -> str:
        """Execute one claimed refresh. Returns the final state; never raises (a failure is recorded on the request)."""
        request = self.repository.find_request(job_id)
        if not request or request["state"] != "running":
            return "skipped"
        try:
            via, reason = self._refresh(job_id)
        except Exception as error:  # noqa: BLE001 - the request must always reach a terminal state
            log.warning("details refresh failed job=%s (%s)", job_id, type(error).__name__)
            via, reason = [], type(error).__name__
        if reason is None:
            return "done"
        with self.svc.db.transaction():
            if self.repository.finish(job_id, "failed", "+".join(via) or None, reason):
                self.svc.event(job_id, "details_refresh_failed", "system", {"reason": reason})
        return "failed"

    def _refresh(self, job_id: str) -> tuple[list[str], str | None]:
        """(stages that contributed, failure reason). A success writes its own terminal state inside `_apply`."""
        job = self.repository.find_job(job_id)
        if job is None or job["archived_at"] is not None:
            return [], "gone"
        hosts = [host_of(job["url"]), host_of(job["apply_url"])]
        owner = self.repository.owner_edited_fields(job_id)
        gaps = missing_fields(job, hosts, owner)
        need_posting = int(job["posting_len"] or 0) < MIN_STORED_CHARS
        if not gaps and not need_posting:
            self._finish_done(job_id, [], [])
            return [], None
        found: dict[str, str] = {}
        vias: list[str] = []
        reason = "nothing_found"
        deadline = time.monotonic() + RUN_DEADLINE_SECONDS
        for name, stage in (("api", self._api), ("stored", self._stored), ("page", self._page), ("browser", self._browser)):
            if time.monotonic() > deadline:
                reason = "timeout"
                break
            try:
                got = stage(job)
            except _Skip as skip:
                reason = skip.reason
                continue
            except Exception as error:  # noqa: BLE001 - a failed stage must not stop the cheaper or later ones
                log.warning("details stage %s failed job=%s (%s)", name, job_id, type(error).__name__)
                reason = f"{name}_failed"
                continue
            if not got:
                continue
            useful = plan_merge(job, {**got, **found}, hosts, owner)
            if any(key not in found for key in useful):
                vias.append(name)
            for key, value in got.items():
                found.setdefault(key, value)
            if set(plan_merge(job, found, hosts, owner)) >= gaps and (not need_posting or found.get("posting")):
                break
        changed = self._apply(job_id, found, vias, need_posting)
        if changed:
            return vias, None
        return vias, reason

    # ---------------------------------------------------------------- stages (each returns detail fields or None)
    def _links(self, job: Any) -> list[str]:
        links = [u for u in dict.fromkeys([job["apply_url"], job["url"]]) if isinstance(u, str) and u]
        return [u for u in links if urlsplit(u).scheme in ("http", "https")]

    def _pace(self, job_id: str) -> None:
        try:
            self.svc.pace_browser(job_id)
        except Exception as error:  # noqa: BLE001 - politeness is best effort here; never fail the refresh over it
            log.warning("details pacing skipped job=%s (%s)", job_id, type(error).__name__)

    def _api(self, job: Any) -> dict[str, str] | None:
        self._pace(job["id"])
        for url in self._links(job):
            form = fetch_posting_form(url)
            if form is not None:
                meta = form.meta.as_set_meta()
                return {k: v for k, v in meta.items() if k in (*FIELDS, "posting")}
        return None

    def _stored(self, job: Any) -> dict[str, str] | None:
        text = self.repository.find_posting_text(job["id"]).strip()
        if len(text) < MIN_STORED_CHARS:
            return None
        return self._tidy(job, {"page_text": text[:30_000]})

    def _page(self, job: Any) -> dict[str, str] | None:
        links = self._links(job)
        if not links:
            return None
        self._pace(job["id"])
        raw = extract_posting(links[0])
        return self._tidy(job, raw) if raw else None

    def _browser(self, job: Any) -> dict[str, str] | None:
        from .direct import MIN_POSTING_FOR_SELECTION, DirectService

        links = self._links(job)
        if not links:
            return None
        binder = self.svc.tab_binder()
        if not binder.online():
            raise _Skip("browser_offline")
        limit = self.svc.runtime_settings().maxOpenBrowsers
        from ..repository.automation import AutomationRepository

        if AutomationRepository(self.svc.db).count_node_browsers(self.svc.node, job["id"]) >= limit:
            raise _Skip("browser_busy")
        self._pace(job["id"])
        try:
            tab = binder.provision()
        except BrowserOffline as error:
            raise _Skip("browser_offline") from error
        embedded: dict[str, str] | None = None
        try:
            text = DirectService(self.svc)._read_page(job["id"], tab.target_id, links[0])  # noqa: SLF001
            embedded = self._embedded(tab.target_id)
        except (BrowserUnavailable, BrowserError) as error:
            raise _Skip("page_unreadable") from error
        finally:
            binder.release(tab.target_id)  # only the tab this run opened
        if embedded:
            return embedded
        if text is None or len(text.strip()) < MIN_POSTING_FOR_SELECTION:
            raise _Skip("page_unreadable")
        return self._tidy(job, {"page_text": text})

    def _embedded(self, target_id: str) -> dict[str, str] | None:
        """Details from a Greenhouse application embedded in the company's own page (read-only: lists iframe URLs)."""
        raw = neo_browser(self.svc.cfg).evaluate(
            target_id, "JSON.stringify([...document.querySelectorAll('iframe')].map(f => f.src).filter(Boolean))"
        )
        try:
            frames = json.loads(raw)
        except ValueError:
            return None
        form = fetch_embedded_greenhouse([f for f in frames if isinstance(f, str)]) if isinstance(frames, list) else None
        if form is None:
            return None
        return {k: v for k, v in form.meta.as_set_meta().items() if k in (*FIELDS, "posting")} or None

    def _tidy(self, job: Any, raw: dict[str, Any]) -> dict[str, str] | None:
        url = self._links(job)[0] if self._links(job) else str(job["url"])
        tidy = clean_posting(self.svc.cfg, url, raw)
        spent = tidy.pop("_cost", None)
        if spent:
            self.svc.cost(job["id"], self.svc.cfg.agent_model_cv, spent[0], spent[1], "details_refresh", spent[2])
        if not tidy.get("cleaned"):
            return None
        return {k: str(v) for k, v in tidy.items() if k in FIELDS and v}

    # ---------------------------------------------------------------- write
    def _apply(self, job_id: str, found: dict[str, str], vias: list[str], need_posting: bool = False) -> list[str]:
        """Merge under the job row lock (re-reading the job and the owner's edits), write the changed columns, record
        the event with field names only, mark the request done, and re-project. Returns the changed field names."""
        posting = (found.get("posting") or "").strip() if need_posting else ""
        clean = {k: v for k, v in sanitize_fields({k: v for k, v in found.items() if k != "posting"}).items() if v}
        if not clean and not posting:
            return []
        with self.svc.db.transaction():
            if not self.repository.lock_job(job_id):
                return []
            job = self.repository.find_job(job_id)
            if job is None or job["archived_at"] is not None:
                return []
            hosts = [host_of(job["url"]), host_of(job["apply_url"])]
            changes = plan_merge(job, clean, hosts, self.repository.owner_edited_fields(job_id))
            stored = bool(posting) and int(job["posting_len"] or 0) < MIN_STORED_CHARS
            if not changes and not stored:
                return []
            company_id = self.svc.upsert_company(changes["company"]) if "company" in changes else None
            norms = {
                column: norm(changes[key])
                for key, column in (("company", "company_norm"), ("role", "title_norm"), ("location", "location_norm"))
                if key in changes
            }
            if changes:
                self.repository.apply(job_id, changes, company_id, norms)
            if stored:
                self.repository.set_posting_text(job_id, posting)
            fields = sorted([*changes, *(["posting"] if stored else [])])
            self._finish_done(job_id, fields, vias)
        try:
            self.svc.project(job_id)  # the vault note follows PostgreSQL, through projection only
        except Exception as error:  # noqa: BLE001 - the sweep re-projects; the refresh itself is committed
            log.warning("details projection failed job=%s (%s)", job_id, type(error).__name__)
        return fields

    def _finish_done(self, job_id: str, fields: list[str], vias: list[str]) -> None:
        with self.svc.db.transaction():
            if self.repository.finish(job_id, "done", "+".join(vias) or None, None if fields else "nothing_missing"):
                if fields:
                    self.svc.event(job_id, "details_refreshed", "system", {"fields": fields, "via": vias})


class _Skip(Exception):  # noqa: N818 - control flow inside the stage loop
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

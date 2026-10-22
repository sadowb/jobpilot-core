"""Fast preparation from About me and the saved answer bank, before model interaction."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..browser.bulk import BULK_SCAN_JS, bulk_fill_js
from ..browser.neo import BrowserError, BrowserUnavailable, neo_browser
from ..domain.answers import JobContext, build_plan
from ..domain.state_machine import PRE_SUBMIT_ORDER
from ..dto.direct import SECRET_KEY, SECRET_VALUE
from ..repository.browser import BrowserRepository
from .application import Services


class BulkField(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    key: str = Field(min_length=1, max_length=2000)
    selector: str = Field(min_length=1, max_length=2000)
    label: str = Field(min_length=1, max_length=240)
    type: str = Field(min_length=1, max_length=40)
    required: bool = False
    occupied: bool = False
    ownerContext: bool = True
    options: list[str] = Field(default_factory=list, max_length=1000)
    multiple: bool = False


class BulkFillService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.chrome = neo_browser(svc.cfg)
        self.repository = BrowserRepository(svc.db)

    def run(self, job_id: str, check: Callable[[], None]) -> dict[str, Any]:
        check()
        job = self.svc.job(job_id)
        if job["status"] not in PRE_SUBMIT_ORDER or job["status"] == "awaiting_approval" or self.svc.submit_clicked(job_id):
            return {}
        row = self.repository.find_session_tab(job_id, self.svc.node)
        if not row or not row["target_id"]:
            return {}
        target = str(row["target_id"])
        started = time.monotonic()
        try:
            # ensure_track may have skipped navigation because the owner selected a CV.
            state = self.chrome.page_state(target, 0)
            link = job.get("apply_url") or job["url"]
            parts = urlsplit(link)
            segments = parts.path.strip("/").split("/")
            if (
                job.get("board_id") == "ashby"
                and parts.hostname == "jobs.ashbyhq.com"
                and len(segments) == 2
                and re.fullmatch(r"[0-9a-fA-F-]{32,36}", segments[1])
            ):
                link = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + "/application", parts.query, ""))
            if state.url in ("", "about:blank"):
                self.svc.pace_browser(job_id)
                self.chrome.navigate(target, link)
            deadline = time.monotonic() + 8
            fields: list[BulkField] = []
            while True:
                check()
                raw = self.chrome.evaluate(target, BULK_SCAN_JS)
                if not isinstance(raw, list) or len(raw) > 200:
                    return {}
                for value in raw:
                    try:
                        field = BulkField.model_validate(value)
                    except ValidationError:
                        continue
                    if (
                        field.type not in ("secret", "password")
                        and not SECRET_KEY.search(field.label)
                        and not SECRET_VALUE.search(field.model_dump_json())
                    ):
                        fields.append(field)
                if fields or time.monotonic() >= deadline:
                    break
                if state.url == job["url"] and link != job["url"]:
                    self.svc.pace_browser(job_id)
                    self.chrome.navigate(target, link)
                    state = self.chrome.page_state(target, 0)
                time.sleep(0.25)
            ordinary = [
                f
                for f in fields
                if f.ownerContext
                and f.type in ("text", "email", "tel", "url", "number", "date", "textarea", "select")
                and not f.multiple
            ]
            plan, _ = build_plan(
                [f.model_dump() for f in ordinary],
                self.svc.load_known(),
                job_id,
                job.get("company_slug") or "",
                JobContext(
                    job.get("company_name") or "",
                    job.get("role") or "",
                    job.get("apply_url") or job["url"],
                    job.get("location") or "",
                ),
            )
            by_key = {f.key: f for f in ordinary}
            items = [
                {
                    "selector": by_key[p.key].selector,
                    "label": by_key[p.key].label,
                    "value": p.value,
                    "kind": by_key[p.key].type,
                    "required": by_key[p.key].required,
                }
                for p in plan
                if not SECRET_VALUE.search(p.value)
            ]
            check()
            results: list[Any] = []
            if items:
                self.svc.pace_browser(job_id)
                results = self.chrome.evaluate(target, bulk_fill_js(items))
                if not isinstance(results, list):
                    return {}
            filled = {r.get("selector") for r in results if isinstance(r, dict) and r.get("status") == "filled"}
            summary = [
                {"label": i["label"], "kind": i["kind"], "required": i["required"], "value": i["value"]}
                for i in items
                if i["selector"] in filled
            ]
            timing = {
                "fieldsSeen": len(fields),
                "fieldsFilled": len(summary),
                "seconds": round(time.monotonic() - started, 3),
            }
            with self.svc.db.transaction():
                self.svc.event(job_id, "bulk_fill", "system", timing)
            return {
                "fields": summary,
                "remaining": [
                    {"label": f.label, "kind": f.type, "required": f.required, "options": f.options}
                    for f in fields
                    if f.selector not in filled
                ],
                "seconds": timing["seconds"],
            }
        except (BrowserError, BrowserUnavailable):
            # Unsupported pages remain the agent's responsibility; never log page content.
            return {}

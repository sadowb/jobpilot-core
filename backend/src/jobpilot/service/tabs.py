"""The owner's view of a job's live Neo tab: which jobs have one, and where to find it.

Both are read-only for the job: no tab is created, navigated or closed, and no lease, task or state is touched
(ADR 0014). `focus` expands the tab group that holds the page (a collapsed group hides it) and asks Neo to raise
the page; when Neo cannot, it reports the group, page id and title for the owner to pick in the Neo cockpit."""

from __future__ import annotations

import logging
from collections.abc import Iterable, MutableMapping
from typing import TYPE_CHECKING, Any

from ..repository.browser import BrowserRepository

if TYPE_CHECKING:
    from .application import Services

log = logging.getLogger(__name__)


class TabService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.repository = BrowserRepository(svc.db)

    def open_jobs(self, job_ids: Iterable[str]) -> set[str]:
        ids = sorted(set(job_ids))
        if not ids:
            return set()
        try:
            return self.repository.list_open_tab_job_ids(self.svc.node, ids)
        except Exception as exc:
            log.debug("could not list open tab jobs: %s", exc)
            return set()

    def annotate(self, rows: Iterable[MutableMapping[str, Any]], key: str = "id") -> None:
        """Set `tabOpen` on each row from one query. The flag follows the recorded session, which the control worker
        reconciles against Neo."""
        listed = list(rows)
        live = self.open_jobs(str(row[key]) for row in listed)
        for row in listed:
            row["tabOpen"] = str(row[key]) in live

    def focus(self, job_id: str) -> dict[str, Any]:
        """Show the job's tab: expand its tab group, then try to bring the page to the front. `focused` True means Neo
        raised it; otherwise `unsupported` carries where to look (page id, title, group name and colour)."""
        row = self.repository.find_session_tab(job_id, self.svc.node)
        if not row or not row["target_id"] or row["close_requested"]:
            return self._result(job_id, False, "no_tab")
        page = str(row["target_id"])
        binder = self.svc.tab_binder()
        group = binder.reveal(page)
        raised = binder.activate(page)
        result = self._result(job_id, raised, "focused" if raised else "unsupported")
        result["page"] = page
        title = binder.title(page)
        if title:
            result["title"] = title[:200]
        if group is not None:
            result["group"], result["color"] = group.title, group.color
        return result

    @staticmethod
    def _result(job_id: str, focused: bool, reason: str) -> dict[str, Any]:
        log.debug("focus tab job=%s focused=%s reason=%s", job_id, focused, reason)
        return {"focused": focused, "reason": reason}

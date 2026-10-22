"""Reconciles `browser_sessions` rows with the tabs Neo really has.

A row of this node that records a tab (`target_id`) is judged against Neo's own tab list: when the tab is gone (the owner
closed it, Neo restarted) the row is dropped with a `browser_lost` event, so `browser_is_open` tells the truth and a submit
run takes its guarded refill + draft-hash path instead of verifying a blank page. Rows without a node or tab (written before
tabs were recorded) are dropped as incompatible.

Neo is shared with the owner and other agents, so tabs that no row names are never closed here: JobPilot closes only
tabs it opened itself, through `Services.close_browser`. An unreachable Neo gives no evidence of loss and leaves every row.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from ..repository.browser import BrowserRepository
from .neo import BrowserUnavailable

if TYPE_CHECKING:
    from ..service import Services

log = logging.getLogger("jobpilot.browser.reaper")
TAB_LOST_GRACE_SECONDS = 30  # a recorded tab missing from Neo this long: the row is dropped (browser_lost)
INTERVAL_SECONDS = 60


class Reaper:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self._tab_missing_since: dict[str, float] = {}
        self._last = 0.0

    def sweep(self, *, now: float | None = None) -> list[str]:
        """Rate-limited reconcile. Returns the jobs whose row was dropped."""
        now = time.monotonic() if now is None else now
        if now - self._last < INTERVAL_SECONDS:
            return []
        self._last = now
        return self.reconcile_tabs(now)

    def reconcile_tabs(self, now: float) -> list[str]:
        node = self.svc.node
        repository = BrowserRepository(self.svc.db)
        rows = repository.list_node_session_tabs(node)
        lost: list[str] = []
        for row in rows:
            if row["node"] is None or row["target_id"] is None:
                with self.svc.browser_lock(row["job_id"]), self.svc.db.transaction():
                    if repository.delete_legacy_session(row["job_id"], node):
                        self.svc.event(row["job_id"], "browser_lost", "system", {"node": node, "reason": "legacy session"})
                        lost.append(str(row["job_id"]))
        try:
            present = {t.target_id for t in self.svc.tab_binder().browser.tabs()}
        except BrowserUnavailable:
            return lost
        recorded = {str(r["job_id"]): str(r["target_id"]) for r in rows if r["node"] is not None and r["target_id"]}
        for job_id, target in recorded.items():
            if target in present:
                self._tab_missing_since.pop(target, None)
                continue
            if now - self._tab_missing_since.setdefault(target, now) < TAB_LOST_GRACE_SECONDS:
                continue
            self._tab_missing_since.pop(target, None)
            with self.svc.browser_lock(job_id), self.svc.db.transaction():
                if repository.delete_tabless_session(job_id, node, target):
                    self.svc.event(job_id, "browser_lost", "system", {"node": node, "reason": "tab gone"})
                    log.warning("browser tab vanished job=%s node=%s; row dropped", job_id, node)
                    lost.append(job_id)
        for target in set(self._tab_missing_since) - set(recorded.values()):
            del self._tab_missing_since[target]
        return lost

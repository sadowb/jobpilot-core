"""Binds a job to one Neo page (tab). The binding lives in PostgreSQL (`browser_sessions.target_id`), so it survives
worker restarts and container recreation (ADR 0019); there is no per-process or on-disk pin.

JobPilot opens the tab itself (blank, then the first load happens server-side) before the agent starts, and the agent is
told the page id in its DATA. Neo is shared with the owner and other agents, so JobPilot only ever closes tabs it opened.
"""

from __future__ import annotations

import logging

from ..config import Settings
from .neo import BrowserError, BrowserUnavailable, NeoBrowser, Tab, TabGroup, neo_browser

log = logging.getLogger("jobpilot.browser.tabs")


class BrowserOffline(RuntimeError):
    """Neo is not reachable: backpressure, never a job failure."""


class TabBinder:
    def __init__(self, cfg: Settings, browser: NeoBrowser | None = None) -> None:
        self.cfg = cfg
        self.browser = browser or neo_browser(cfg)

    def online(self) -> bool:
        return self.browser.alive()

    def reveal(self, target_id: str) -> TabGroup | None:
        """Expand the tab group that holds this page so the tab is visible. Best effort: a group this connection cannot
        change, or an unreachable Neo, only means the tab may stay hidden. Returns the group when one was found."""
        try:
            group = next((g for g in self.browser.tab_groups() if target_id in g.pages), None)
            if group is not None:
                self.browser.expand_group(group.id)
            return group
        except (BrowserUnavailable, BrowserError) as exc:
            log.warning("could not expand the tab group for page %s: %s", target_id, type(exc).__name__)
            return None

    def activate(self, target_id: str) -> bool:
        try:
            return self.browser.activate(target_id)
        except (BrowserUnavailable, BrowserError):
            return False

    def title(self, target_id: str) -> str:
        try:
            return next((t.title for t in self.browser.tabs() if t.target_id == target_id), "")
        except (BrowserUnavailable, BrowserError):
            return ""

    def provision(self) -> Tab:
        """A fresh blank tab."""
        try:
            return self.browser.open_tab("about:blank")
        except (BrowserUnavailable, BrowserError) as exc:
            raise BrowserOffline("Neo is not available") from exc

    def release(self, target_id: str | None) -> bool:
        """Close the tab. A missing tab is success; an unreachable Neo is also success because there is nothing left for
        JobPilot to close (the reaper reconciles the rows when Neo returns). False means Neo refused: retry later."""
        if not target_id:
            return True
        try:
            self.browser.close_tab(target_id)
        except BrowserUnavailable:
            log.warning("Neo unreachable while closing a tab; the row is reconciled when it returns")
        except BrowserError as exc:
            log.warning("tab close refused by Neo: %s", str(exc)[:120])
            return False
        return True

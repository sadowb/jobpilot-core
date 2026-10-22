"""Durable maintenance shared by the API and standalone worker supervisor."""

from __future__ import annotations

import logging
import threading

from ..browser.reaper import Reaper
from ..service import Services
from ..service.agent_sessions import AgentSessions

log = logging.getLogger("jobpilot.maintenance")


def sweep_loop(svc: Services, stop: threading.Event) -> None:
    reaper = Reaper(svc)  # rate-limits itself; closes browser sessions nothing owns any more
    sessions = AgentSessions(svc)
    while not stop.is_set():
        for sweep in (svc.sweep_projections, svc.sweep_browsers, reaper.sweep, sessions.sweep):
            try:
                sweep()
            except Exception:  # noqa: BLE001 - durable queues retry independently
                log.exception("maintenance sweep failed: %s", getattr(sweep, "__qualname__", sweep))
        stop.wait(2)

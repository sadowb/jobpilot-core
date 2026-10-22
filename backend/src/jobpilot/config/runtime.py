"""Composition root: one shared service and PostgreSQL pool per API process."""

from __future__ import annotations

import logging
import secrets

from ..config import get_settings
from ..repository.database import Database
from ..repository.live_events import LiveEvents
from ..service import ApplicationService
from ..service.dashboard import DashboardService
from ..service.tracker import Tracker
from ..worker.client import request_dispatch

log = logging.getLogger("jobpilot.http")

cfg = get_settings()

db = Database(cfg, open_pool=False)
live_events = LiveEvents(cfg.dsn)

application_service = ApplicationService(cfg, db)
application_service.dispatch_hook = request_dispatch

tracker_service = Tracker(application_service)

PORT = cfg.backend_port

ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", "127.0.0.1:5173", "localhost:5173"}

ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}

API_TOKEN = secrets.token_urlsafe(32)

SESSION_TTL = 30 * 24 * 60 * 60


dashboard_service = DashboardService(db)

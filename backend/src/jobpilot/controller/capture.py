"""Browser-extension capture: send the link of the page you are on straight to the dashboard.

The extension is neither the dashboard (no session cookie, no CSRF token, a `chrome-extension://` origin) nor trusted
with the full API, so it gets its own narrow credential: a persistent random token that can do exactly one thing,
add a link for processing. Pairing is one visit to /api/v1/capture/pair in the owner's own browser.
"""

from __future__ import annotations

import hmac
import threading
import time
from collections import deque
from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..config.runtime import application_service, log
from ..dto import responses
from ..dto.requests import CaptureRequest
from ..middleware.security import _session_secret

router = APIRouter(tags=["Capture"])

CAPTURE_TOKEN = _session_secret(".capture-token")
_RATE = 60  # links per minute; a person clicking a button never gets near this
_stamps: deque[float] = deque()
_lock = threading.Lock()


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    return header[7:] if header.startswith("Bearer ") else ""


def _within_rate() -> bool:
    now = time.monotonic()
    with _lock:
        while _stamps and now - _stamps[0] > 60:
            _stamps.popleft()
        if len(_stamps) >= _RATE:
            return False
        _stamps.append(now)
        return True


@router.post("/capture", response_model=responses.Ingested)
def capture(body: CaptureRequest, request: Request) -> dict[str, Any]:
    if not hmac.compare_digest(_bearer(request).encode(), CAPTURE_TOKEN.encode()):
        raise HTTPException(401, "unauthorized")
    if not _within_rate():
        raise HTTPException(429, "too many links; wait a minute")
    try:
        return application_service.ingest(str(body.url))
    except (LookupError, PermissionError, ValueError) as error:
        raise HTTPException(422, str(error)[:200]) from error
    except Exception as error:  # noqa: BLE001 - never echo driver/database internals
        log.exception("capture failed for one link")
        raise HTTPException(500, "could not add this link") from error


@router.get("/capture/pair", response_class=HTMLResponse, include_in_schema=False)
def pair(request: Request) -> HTMLResponse:
    """Hands the capture token to the extension's content script, only to a top-level navigation the owner made
    (typed, bookmarked, or opened by the extension: Sec-Fetch-Site none/same-origin). A page on another site, a
    fetch() or an iframe cannot read it."""
    navigated = request.headers.get("sec-fetch-dest") == "document" and request.headers.get("sec-fetch-site") in {
        "none",
        "same-origin",
    }
    if not navigated:
        raise HTTPException(403, "open this page directly from the extension")
    page = (
        "<!doctype html><meta charset=utf-8><title>Pair JobPilot extension</title>"
        f'<meta name="jobpilot-capture-token" content="{escape(CAPTURE_TOKEN)}">'
        '<body style="font:16px system-ui;margin:3rem"><h1>JobPilot extension</h1>'
        "<p>If the extension is installed it is now paired. You can close this tab.</p></body>"
    )
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})

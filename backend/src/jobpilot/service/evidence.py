"""Server-side proof of what the owner's submission left on the job tab (ADR 0015, 0026). The agent never submits;
after the owner says they did, the server reads the parked tab, read-only, for confirmation evidence.

Everything captured is untrusted page data kept for the owner's eyes: the URL loses its query and fragment, text is
bounded and secret-shaped values are withheld, the screenshot is an owner-only file under `data/evidence/<job>/`.
Nothing here feeds a prompt, a state transition or a log line."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..boards import adapter_by_id
from ..browser.neo import BrowserError, BrowserUnavailable, PageCapture, neo_browser
from ..browser.verify import check_confirmation
from ..dto.direct import SECRET_VALUE
from ..repository.browser import BrowserRepository
from ..repository.database import J
from .application import Services

log = logging.getLogger(__name__)

EXCERPT_CHARS = 2_000
TITLE_CHARS = 160
URL_CHARS = 300
KEEP_SCREENSHOTS = 5
SCREENSHOT_NAME = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}\.png$")
JOB_ID = re.compile(r"^j_[a-z0-9]{1,40}$")


def safe_text(value: str, limit: int) -> str:
    """One line, printable characters only, secret-shaped values withheld, length-capped."""
    text = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    return SECRET_VALUE.sub("[withheld]", text)[:limit]


def safe_url(value: str) -> str:
    """Scheme, host and path only: query strings and fragments routinely carry tokens or one-time codes."""
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    port = f":{parts.port}" if parts.port else ""
    return safe_text(f"{parts.scheme}://{parts.hostname}{port}{parts.path}", URL_CHARS)


def evidence_dir(data_dir: Path, job_id: str) -> Path:
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("invalid job id")
    return data_dir / "evidence" / job_id


def screenshot_path(data_dir: Path, job_id: str, name: str) -> Path | None:
    """The stored file for `name`, or None. `name` comes from the database, but is validated anyway."""
    if not SCREENSHOT_NAME.fullmatch(name):
        return None
    base = evidence_dir(data_dir, job_id).resolve()
    path = (base / name).resolve()
    return path if path.parent == base and path.is_file() else None


def evidence_view(raw: object) -> dict[str, Any] | None:
    """The owner-facing shape of a stored `job_forms.submit_evidence` value; the file name is never exposed."""
    if not isinstance(raw, dict):
        return None
    return {
        "capturedAt": str(raw.get("capturedAt", "")),
        "url": str(raw.get("url", "")),
        "title": str(raw.get("title", "")),
        "excerpt": str(raw.get("excerpt", "")),
        "confirmation": raw.get("confirmation") if raw.get("confirmation") in ("seen", "not_seen") else None,
        "hasScreenshot": isinstance(raw.get("screenshot"), str) and bool(SCREENSHOT_NAME.fullmatch(raw["screenshot"])),
    }


class SubmitEvidenceService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc

    def verify(self, job_id: str, *, polls: int = 3, interval: float = 2.0, sleep: Callable[[float], None] = time.sleep) -> bool:
        """Read the parked tab (no clicks, no navigation) up to `polls` times for the board's confirmation text, then
        store one capture with the verdict. False means "not seen", including a gone tab or an unreachable Neo: the
        owner's word still stands and the dashboard says no confirmation page was seen."""
        seen = False
        try:
            row = BrowserRepository(self.svc.db).find_session_tab(job_id, self.svc.node)
            patterns = adapter_by_id(self.svc.job(job_id)["board_id"]).confirmation_patterns
            if row and row["target_id"]:
                for attempt in range(max(polls, 1)):
                    if attempt:
                        sleep(interval)
                    text = neo_browser(self.svc.cfg).page_text(row["target_id"])
                    if check_confirmation(text, patterns).ok:
                        seen = True
                        break
        except (BrowserUnavailable, BrowserError, ValueError, RuntimeError) as error:
            log.warning("submit verification unreadable job=%s %s", job_id, type(error).__name__)
        self.capture(job_id, confirmation_seen=seen)
        return seen

    def capture(self, job_id: str, *, confirmation_seen: bool | None = None) -> dict[str, Any] | None:
        """Read the job tab right now and record it. Best effort: failure never changes the caller's outcome."""
        try:
            row = BrowserRepository(self.svc.db).find_session_tab(job_id, self.svc.node)
            if not row or not row["target_id"]:
                self.svc.event(job_id, "submit_evidence_unavailable", "system", {"reason": "tab gone"})
                return None
            page = neo_browser(self.svc.cfg).capture(row["target_id"], EXCERPT_CHARS)
        except (BrowserUnavailable, BrowserError, ValueError) as error:
            log.warning("submit evidence unavailable job=%s %s", job_id, type(error).__name__)
            self._safe_event(job_id, {"reason": "page unreadable"})
            return None
        try:
            return self._record(job_id, page, confirmation_seen)
        except (OSError, ValueError) as error:
            log.warning("submit evidence not stored job=%s %s", job_id, type(error).__name__)
            self._safe_event(job_id, {"reason": "not stored"})
            return None

    def _safe_event(self, job_id: str, payload: dict[str, Any]) -> None:
        try:
            self.svc.event(job_id, "submit_evidence_unavailable", "system", payload)
        except Exception as error:  # noqa: BLE001 - evidence must never mask the run outcome
            log.warning("submit evidence event failed job=%s %s", job_id, type(error).__name__)

    def _record(self, job_id: str, page: PageCapture, confirmation_seen: bool | None = None) -> dict[str, Any]:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        text_sha = hashlib.sha256(page.text.encode()).hexdigest()
        name: str | None = None
        if page.png:
            directory = evidence_dir(self.svc.cfg.data_dir, job_id)
            directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            name = f"{stamp}-{text_sha[:8]}.png"
            descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(page.png)
            self._prune(directory)
        evidence: dict[str, Any] = {
            "capturedAt": datetime.now(UTC).isoformat(timespec="seconds"),
            "url": safe_url(page.url),
            "title": safe_text(page.title, TITLE_CHARS),
            "excerpt": safe_text(page.text, EXCERPT_CHARS),
            "textSha": text_sha,
            "screenshot": name,
        }
        if confirmation_seen is not None:
            evidence["confirmation"] = "seen" if confirmation_seen else "not_seen"
        with self.svc.db.transaction():
            self.svc.db.run("UPDATE job_forms SET submit_evidence=%s WHERE job_id=%s", (J(evidence), job_id))
            self.svc.event(
                job_id,
                "submit_evidence",
                "system",
                {"url": evidence["url"], "title": evidence["title"], "textSha": text_sha, "screenshot": name},
            )
        return evidence

    @staticmethod
    def _prune(directory: Path) -> None:
        files = sorted(p for p in directory.iterdir() if SCREENSHOT_NAME.fullmatch(p.name))
        for stale in files[:-KEEP_SCREENSHOTS]:
            stale.unlink(missing_ok=True)

"""Notices the owner's manual submit while an application is parked for them (ADR 0026, amended).

When a job parks at `awaiting_approval`, `store_baseline` records what its tab looks like. A beat task then reads each
parked tab read-only (`NeoBrowser.page_state`, no model, no clicks) and compares it with that baseline:

* the URL path moved or the form's field labels are gone, AND a confirmation text that the baseline did not already
  contain is now shown  -> the submit is recorded exactly like "I submitted it" (method `auto-detected`);
* the page moved or the form is gone but no confirmation text appeared -> `looks_submitted`, Needs You asks;
* the tab no longer exists -> `tab_closed`, Needs You asks "did you submit?".

A page whose baseline already says "thank you for applying" can never produce the first outcome. Every read is
untrusted page data: it is hashed or pattern-matched here and never stored beyond a capped, query-free URL and title.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from ..boards import adapter_by_id
from ..browser.neo import BrowserError, BrowserUnavailable, PageState, neo_browser
from ..browser.verify import check_confirmation
from ..domain.challenge import code_challenge
from ..repository.browser import BrowserRepository
from ..repository.park_watch import ParkWatchRepository
from .evidence import safe_text, safe_url
from .verification import VerificationService

if TYPE_CHECKING:
    from .application import Services

log = logging.getLogger("jobpilot.park_watch")

Kind = Literal["none", "detected", "looks_submitted", "tab_closed", "unreadable"]

PAGE_CHARS = 60_000
MAX_LABELS = 20
MIN_LABELS_FOR_FORM_GONE = 3
FORM_GONE_RATIO = 0.3
READ_TIMEOUT_SECONDS = 20.0
RECHECK_SECONDS = 2.0
_GONE = re.compile(r"unknown page|no such page|not found|closed", re.IGNORECASE)


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def _labels(inventory: object, normalized: str) -> list[str]:
    """Field labels the agent reported that are visible on the parked page: the signature of 'the form is still here'."""
    found: list[str] = []
    if not isinstance(inventory, list):
        return found
    for item in inventory:
        label = _norm(str(item.get("label", ""))) if isinstance(item, dict) else ""
        if 3 <= len(label) <= 80 and label in normalized and label not in found:
            found.append(label)
        if len(found) >= MAX_LABELS:
            break
    return found


def fingerprint(state: PageState, patterns: list[str], inventory: object) -> dict[str, Any]:
    normalized = _norm(state.text)
    return {
        "url": safe_url(state.url),
        "title": safe_text(state.title, 200),
        "textSha": hashlib.sha256(normalized.encode()).hexdigest(),
        "chars": len(normalized),
        "labels": _labels(inventory, normalized),
        "confirmed": check_confirmation(state.text, patterns).ok,
        "challenge": code_challenge(state.text) is not None,
    }


def judge(baseline: dict[str, Any], state: PageState, patterns: list[str]) -> Kind:
    """Pure decision on one read of a parked tab. `none` also covers a page that is still loading or blank."""
    if state.ready != "complete" or not state.text.strip():
        return "none"
    normalized = _norm(state.text)
    labels = [str(label) for label in baseline.get("labels") or []]
    gone = len(labels) >= MIN_LABELS_FOR_FORM_GONE and (
        sum(label in normalized for label in labels) / len(labels) < FORM_GONE_RATIO
    )
    moved = bool(baseline.get("url")) and safe_url(state.url) != baseline["url"]
    fresh_confirmation = check_confirmation(state.text, patterns).ok and not baseline.get("confirmed")
    if fresh_confirmation and (moved or gone):
        return "detected"
    if moved or gone or fresh_confirmation:
        return "looks_submitted"
    return "none"


@dataclass(frozen=True, slots=True)
class Reading:
    state: PageState | None  # None: Neo or the page could not be read this tick
    gone: bool = False


class ParkWatchService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.repository = ParkWatchRepository(svc.db)

    # -- baseline ------------------------------------------------------------------------------------------------
    def store_baseline(self, job_id: str, *, attempts: int = 3, sleep: Callable[[float], None] = time.sleep) -> bool:
        """`_store_baseline_once`, retried: Neo is shared and can drop a call right as the agent's run ends."""
        for attempt in range(attempts):
            if self._store_baseline_once(job_id):
                return True
            if attempt + 1 < attempts:
                sleep(2.0)
        return False

    def adopt_unbaselined(self, limit: int = 10) -> int:
        """A job that parked without a baseline would never be watched. Take the baseline now, unless the tab already
        shows a confirmation: then the owner submitted before we looked and Needs You asks (looks_submitted)."""
        taken = 0
        for row in self.repository.list_unbaselined(self.svc.node, limit):
            job_id = str(row["id"])
            try:
                state = neo_browser(self.svc.cfg).page_state(str(row["target_id"]), PAGE_CHARS)
                patterns = adapter_by_id(self.svc.job(job_id)["board_id"]).confirmation_patterns
                already = state.ready == "complete" and check_confirmation(state.text, patterns).ok
            except (BrowserUnavailable, BrowserError, ValueError):
                continue
            if already:
                self._signal(job_id, "looks_submitted", None)
            elif self._store_baseline_once(job_id):
                taken += 1
        return taken

    def _store_baseline_once(self, job_id: str) -> bool:
        """Record the parked tab as it is now. Best effort: a job without a baseline is simply not watched and the
        owner's buttons still work. Clears any earlier signal (a re-park or a dismissal starts fresh)."""
        try:
            row = BrowserRepository(self.svc.db).find_session_tab(job_id, self.svc.node)
            if not row or not row["target_id"]:
                return False
            state = neo_browser(self.svc.cfg).page_state(str(row["target_id"]), PAGE_CHARS)
            form = self.repository.find_form_for_baseline(job_id)
            patterns = adapter_by_id(self.svc.job(job_id)["board_id"]).confirmation_patterns
            baseline = fingerprint(state, patterns, form["inventory"] if form else None)
        except (BrowserUnavailable, BrowserError, ValueError) as error:
            log.warning("park baseline unavailable job=%s %s", job_id, type(error).__name__)
            return False
        with self.svc.db.transaction():
            self.repository.update_baseline(job_id, baseline)
        return True

    # -- watching ------------------------------------------------------------------------------------------------
    def run_once(
        self,
        *,
        workers: int = 4,
        limit: int = 40,
        timeout: float = READ_TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        reader: Callable[[str], Reading] | None = None,
    ) -> dict[str, int]:
        """One pass over the parked tabs on this node. Reads run in a small pool with a deadline; every database
        write happens here, on the caller's thread. Returns counts per outcome (for logs and tests)."""
        read = reader or self._read
        if reader is None:
            self.adopt_unbaselined()
        rows = self.repository.list_watchable(self.svc.node, limit)
        counts: dict[str, int] = {
            "checked": 0,
            "detected": 0,
            "looks_submitted": 0,
            "tab_closed": 0,
            "unreadable": 0,
            "challenge": 0,
        }
        if not rows:
            return counts
        pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="park-watch")
        try:
            futures = {pool.submit(read, str(row["target_id"])): row for row in rows}
            done, _ = wait(futures, timeout=timeout)
            for future, row in futures.items():
                counts["checked"] += 1
                if future not in done or future.exception() is not None:
                    counts["unreadable"] += 1
                    continue
                outcome = self._apply(row, future.result(), read, sleep)
                if outcome in counts:
                    counts[outcome] += 1
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return counts

    def _read(self, target_id: str) -> Reading:
        chrome = neo_browser(self.svc.cfg)
        try:
            return Reading(chrome.page_state(target_id, PAGE_CHARS))
        except BrowserError as error:
            if not _GONE.search(str(error)):
                return Reading(None)
        except BrowserUnavailable:
            return Reading(None)
        try:  # confirm against the tab list so a one-off page error is not read as "closed"
            return Reading(None, gone=all(tab.target_id != target_id for tab in chrome.tabs()))
        except (BrowserUnavailable, BrowserError):
            return Reading(None)

    def _apply(
        self, row: dict[str, Any], reading: Reading, read: Callable[[str], Reading], sleep: Callable[[float], None]
    ) -> str:
        job_id = str(row["id"])
        signal = row["park_signal"]
        if reading.gone:
            self._signal(job_id, "tab_closed", signal)
            return "tab_closed"
        if reading.state is None:
            return "unreadable"
        patterns = adapter_by_id(row["board_id"]).confirmation_patterns
        baseline = row["park_baseline"] if isinstance(row["park_baseline"], dict) else {}
        verdict = judge(baseline, reading.state, patterns)
        if verdict == "detected":
            sleep(RECHECK_SECONDS)  # the same verdict twice, a moment apart: a navigation in flight is not a submit
            again = read(str(row["target_id"]))
            if again.state is None or judge(baseline, again.state, patterns) != "detected":
                return "none"
            try:
                recorded = self.svc.auto_detected_submit(job_id)
            except PermissionError:
                return "none"  # the owner got there first, or the job moved on
            return "detected" if recorded else "none"
        asked = row.get("verify_requested_at") is not None  # one request per challenge: nothing more to do here
        label = code_challenge(reading.state.text) if not (baseline.get("challenge") or asked) else None
        if label:
            # A code prompt that the parked form did not have exists only after the owner's Submit click.
            sleep(RECHECK_SECONDS)
            again = read(str(row["target_id"]))
            if again.state is None or code_challenge(again.state.text) != label:
                return "none"
            try:
                started = VerificationService(self.svc).request(job_id, "watcher", label)
            except PermissionError:
                return "none"  # the owner got there first, or the job moved on
            return "challenge" if started else "none"
        if verdict == "looks_submitted":
            self._signal(job_id, "looks_submitted", signal)
            return "looks_submitted"
        return "none"

    def _signal(self, job_id: str, signal: str, current: str | None) -> None:
        if current == signal:
            return
        with self.svc.db.transaction():
            if self.repository.update_signal(job_id, signal, current):
                self.svc.event(job_id, "park_signal", "system", {"signal": signal})

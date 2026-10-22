"""Browser operations behind the shared Services facade."""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from ..boards import adapter_by_id
from ..browser.neo import BrowserUnavailable
from ..browser.tabs import BrowserOffline, TabBinder
from ..repository.browser import BrowserRepository
from ._support import log

if TYPE_CHECKING:
    from .application import Services


class BrowserService:
    def __init__(self, application: Services) -> None:
        self.application = application
        self.repository = BrowserRepository(application.db)
        self._tabs: TabBinder | None = None

    def require_automation(self, job_id: str) -> None:
        """Applying an agent result needs automation to be on; a tracker row the owner applied to is exempt."""
        control = self.repository.find_automation_for_require_browser_worker()
        if not control or not control["enabled"]:
            row = self.repository.find_jobs_for_require_browser_worker(job_id)
            if not row or row["source"] == "platform":
                raise PermissionError("automation stopped")

    def recipe_path(self, board: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", board):
            raise ValueError("invalid board id")
        if adapter_by_id(board).id != board:
            raise ValueError("unknown board id")
        root = (self.application.cfg.home / "agent" / "boards").resolve()
        path = root / f"{board}.md"
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise PermissionError("recipe path outside boards directory")
        return path

    def recipe(self, board: str) -> str:
        path = self.application.recipe_path(board)
        return path.read_text()[:12000] if path.exists() else "no recipe; use generic flow"

    def review_recipe(self, suggestion_id: int, accept: bool, text: str | None) -> None:
        """Called only by the human-session HTTP route; suggestions never become instructions automatically."""
        control = self.repository.find_automation_for_review_recipe()
        if (control and control["enabled"]) or self.repository.find_tasks_for_review_recipe():
            raise PermissionError("stop automation and drain leases before reviewing recipes")
        row = self.repository.find_recipe_suggestions_for_review_recipe(suggestion_id)
        if not row or row["status"] != "pending":
            raise ValueError("pending recipe suggestion not found")
        if accept:
            if text is None or not text.strip() or len(text) > 12000:
                raise ValueError("accepting requires the human-reviewed full recipe text (1..12000 characters)")
            path = self.application.recipe_path(row["board_id"])
            # Atomic file replacement; failed DB commit is repairable by reviewing again.
            temporary = path.with_suffix(".md.review")
            if temporary.is_symlink():
                raise PermissionError("recipe temporary path is a symlink")
            temporary.write_text(text)
            temporary.replace(path)
        self.repository.update_recipe_suggestions_for_review_recipe("accepted" if accept else "rejected", suggestion_id)
        self.application.event(row["job_id"], "recipe_reviewed", "user", {"id": suggestion_id, "accepted": accept})

    def _defer_close(self, job_id: str) -> None:
        # Keep the verified form while its owner reviews it: browser-only widget
        # and authentication state cannot always be rebuilt from the saved plan.
        if self.application.job(job_id)["status"] == "awaiting_approval":
            self._reveal_after_commit(job_id)
            return
        if self.repository.find_parked_for_owner(job_id):
            self._reveal_after_commit(job_id)
            return  # the owner is being waited on: the tab stays until they act (see PARKED_FOR_OWNER)
        self.repository.update_browser_sessions_for_defer_close(job_id)
        if not self.application.db.defer(lambda: self.application.close_browser(job_id)):
            self.application.close_browser(job_id)

    def _reveal_after_commit(self, job_id: str) -> None:
        if not self.application.db.defer(lambda: self.reveal_job_tab(job_id)):
            self.reveal_job_tab(job_id)

    def tabs(self) -> TabBinder:
        if self._tabs is None:
            self._tabs = TabBinder(self.application.cfg)
        return self._tabs

    def target_id(self, job_id: str) -> str | None:
        """The Neo page this node recorded for the job. PostgreSQL is the only source: it survives restarts."""
        row = self.repository.find_session_tab(job_id, self.application.node)
        return str(row["target_id"]) if row and row["target_id"] and not row["close_requested"] else None

    def ensure_tab(self, job_id: str) -> None:
        """Give `job_id` a recorded tab in Neo unless it already has one. The row is written right after the tab is
        opened and before anything uses it. Raises BrowserOffline when Neo is unreachable (the caller backs off;
        nothing is failed)."""
        existing = self.repository.find_session_tab(job_id, self.application.node)
        if existing and self.browser_session_present(job_id):
            return
        tab = self.tabs().provision()
        try:
            self.repository.insert_browser_sessions_for_browser_opened(job_id, self.application.node, tab.target_id)
        except BaseException:
            self.tabs().release(tab.target_id)
            raise
        self.tabs().reveal(tab.target_id)  # a collapsed tab group would hide the agent's tab from the owner
        self.application.event(job_id, "tab_opened", "system", {"node": self.application.node})

    def reveal_job_tab(self, job_id: str) -> None:
        """Best effort: expand the tab group of a tab that now waits for the owner. Never raises, never collapses."""
        try:
            target = self.target_id(job_id)
            if target:
                self.tabs().reveal(target)
        except Exception as exc:  # noqa: BLE001 - visibility must never fail a job transition
            log.warning("could not reveal the tab for job %s: %s", job_id, type(exc).__name__)

    def drop_tab(self, job_id: str) -> None:
        """Forget a session whose tab cannot be used (open failed, tab gone): close it and delete the row."""
        row = self.repository.find_session_tab(job_id, self.application.node)
        if row:
            if row["node"] is None or row["target_id"] is None:
                self._drop_legacy_session(job_id)
                return
            self.tabs().release(row["target_id"])
            self.repository.delete_browser_sessions_for_close_browser_locked(job_id, self.application.node)

    def _drop_legacy_session(self, job_id: str) -> None:
        with self.application.db.transaction():
            if self.repository.delete_legacy_session(job_id, self.application.node):
                self.application.event(
                    job_id, "browser_lost", "system", {"node": self.application.node, "reason": "legacy session"}
                )

    def browser_session_present(self, job_id: str) -> bool:
        """Reconcile before use, including sessions pending close (post-run safety must still inspect those).

        A missing tab is recoverable; an unreachable Neo gives no evidence of loss and leaves the row intact.
        The lock prevents a replacement tab from being provisioned between the probe and deletion.
        """
        with self.application.browser_lock(job_id):
            row = self.repository.find_session_tab(job_id, self.application.node)
            if not row:
                return False
            if row["node"] is None or row["target_id"] is None:
                self._drop_legacy_session(job_id)
                return False
            try:
                if row["target_id"] in {tab.target_id for tab in self.tabs().browser.tabs()}:
                    return True
            except BrowserUnavailable as exc:
                raise BrowserOffline("Neo is not available") from exc
            with self.application.db.transaction():
                deleted = self.repository.delete_tabless_session(job_id, self.application.node, row["target_id"])
                if deleted:
                    self.application.event(
                        job_id,
                        "browser_lost",
                        "system",
                        {"node": self.application.node, "reason": "tab gone"},
                    )
            return False

    def browser_is_open(self, job_id: str) -> bool:
        with self.application.browser_lock(job_id):
            if not self.browser_session_present(job_id):
                return False
            row = self.repository.find_session_tab(job_id, self.application.node)
            return bool(row and not row["close_requested"])

    def browser_lock(self, job_id: str) -> Iterator[None]:
        with self.application.db.session_lock(job_id, 2):
            yield

    def close_browser(self, job_id: str) -> None:
        try:
            with self.repository.cleanup_lock(job_id) as acquired:
                if acquired:
                    self.application._close_browser_locked(job_id)
        except Exception:  # noqa: BLE001 - cleanup cannot fail a committed job write
            log.exception("browser cleanup failed job=%s", job_id)

    def _close_browser_locked(self, job_id: str) -> None:
        with self.application.browser_lock(job_id):
            row = self.repository.find_browser_sessions_for_close_browser_locked(job_id, self.application.node)
            if not row:
                return
            if row["node"] is None or row["target_id"] is None:
                self._drop_legacy_session(job_id)
                self.application._close_backoff.pop(job_id, None)
                return
            until, failures = self.application._close_backoff.get(job_id, (0.0, 0))
            if time.monotonic() < until:
                return
            if not self.tabs().release(row["target_id"]):
                failures += 1
                self.application._close_backoff[job_id] = (time.monotonic() + min(5 * 2**failures, 900), failures)
                if failures == 1:  # one safe diagnostic, then quiet retries (5s, 10s, ... capped at 15 min)
                    log.warning("browser close failed job=%s", job_id)
                return
            self.application._close_backoff.pop(job_id, None)
            with self.application.db.transaction():
                self.repository.delete_browser_sessions_for_close_browser_locked(job_id, self.application.node)
                self.application.event(job_id, "browser_closed", "system")

    def sweep_browsers(self) -> None:
        rows = self.repository.list_browser_sessions_for_sweep_browsers(self.application.node)
        for row in rows:
            self.application.close_browser(row["job_id"])

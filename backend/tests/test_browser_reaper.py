"""Reconciliation of recorded browser sessions against Neo's own tab list (ADR 0019); no browser or database starts."""

import unittest
from contextlib import nullcontext
from typing import Any
from unittest.mock import MagicMock, patch

from jobpilot.browser.neo import BrowserUnavailable, Tab
from jobpilot.browser.reaper import INTERVAL_SECONDS, TAB_LOST_GRACE_SECONDS, Reaper
from jobpilot.repository.browser import BrowserRepository
from jobpilot.service import Services


class ReaperTabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app: Any = MagicMock(spec=Services)
        self.app.node = "mac"
        self.app.db = MagicMock()
        self.app.db.transaction.return_value = nullcontext()
        self.app.browser_lock.return_value = nullcontext()
        self.neo: Any = MagicMock()
        self.neo.tabs.return_value = []
        self.app.tab_binder.return_value.browser = self.neo
        self.repository: Any = MagicMock(spec=BrowserRepository)
        self.repository.list_node_session_tabs.return_value = []
        self.repository.delete_tabless_session.return_value = 1
        patcher = patch("jobpilot.browser.reaper.BrowserRepository", return_value=self.repository)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.reaper = Reaper(self.app)

    def row(self, job_id: str, target: str | None) -> dict[str, Any]:
        return {"job_id": job_id, "node": "mac", "target_id": target, "opened_at": None}

    def test_legacy_rows_are_lost(self) -> None:
        self.repository.list_node_session_tabs.return_value = [
            {"job_id": "j_old", "node": None, "target_id": None, "opened_at": None},
            self.row("j_tabless", None),
        ]
        self.repository.delete_legacy_session.return_value = 1
        self.reaper.sweep(now=1000.0)
        self.assertEqual(self.repository.delete_legacy_session.call_count, 2)
        self.app.event.assert_any_call("j_old", "browser_lost", "system", {"node": "mac", "reason": "legacy session"})

    def test_a_recorded_tab_missing_from_neo_drops_its_row_after_the_grace(self) -> None:
        self.repository.list_node_session_tabs.return_value = [self.row("j_1", "7")]
        self.reaper.reconcile_tabs(100.0)
        self.repository.delete_tabless_session.assert_not_called()
        lost = self.reaper.reconcile_tabs(100.0 + TAB_LOST_GRACE_SECONDS)
        self.assertEqual(lost, ["j_1"])
        self.repository.delete_tabless_session.assert_called_once_with("j_1", "mac", "7")
        self.app.event.assert_called_once_with("j_1", "browser_lost", "system", {"node": "mac", "reason": "tab gone"})

    def test_a_tab_that_is_present_is_never_judged_lost(self) -> None:
        self.repository.list_node_session_tabs.return_value = [self.row("j_1", "7")]
        self.neo.tabs.return_value = [Tab("7", "https://boards.example.com/j/1")]
        for step in range(4):
            self.reaper.reconcile_tabs(100.0 + step * 1000)
        self.repository.delete_tabless_session.assert_not_called()

    def test_unreachable_neo_judges_nothing(self) -> None:
        self.repository.list_node_session_tabs.return_value = [self.row("j_1", "7")]
        self.neo.tabs.side_effect = BrowserUnavailable("down")
        self.assertEqual(self.reaper.reconcile_tabs(100.0 + 10 * TAB_LOST_GRACE_SECONDS), [])
        self.repository.delete_tabless_session.assert_not_called()

    def test_tabs_no_row_names_belong_to_the_owner_and_are_never_closed(self) -> None:
        self.neo.tabs.return_value = [Tab("1", "chrome://newtab/"), Tab("4", "https://example.com/"), Tab("7", "https://x/")]
        self.repository.list_node_session_tabs.return_value = [self.row("j_1", "7")]
        self.reaper.sweep(now=1000.0)
        self.reaper.sweep(now=1000.0 + INTERVAL_SECONDS + 10_000)
        self.neo.close_tab.assert_not_called()

    def test_the_sweep_is_rate_limited(self) -> None:
        self.repository.list_node_session_tabs.return_value = [self.row("j_1", "7")]
        self.reaper.sweep(now=1000.0)
        self.reaper.sweep(now=1000.0 + INTERVAL_SECONDS - 1)
        self.assertEqual(self.repository.list_node_session_tabs.call_count, 1)


if __name__ == "__main__":
    unittest.main()

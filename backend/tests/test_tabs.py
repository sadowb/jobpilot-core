"""Owner-facing tab view: which jobs have a live tab, and the where the tab is. No browser or database starts."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from jobpilot.browser.neo import TabGroup
from jobpilot.config import Settings
from jobpilot.service import Services
from jobpilot.service.tabs import TabService


class TabServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc: Any = MagicMock(spec=Services)
        self.svc.cfg, self.svc.node = Settings(_env_file=None, jobpilot_home=Path("/nonexistent")), "mac"
        self.svc.db = MagicMock()
        repository = patch("jobpilot.service.tabs.BrowserRepository")
        self.repository: Any = repository.start().return_value
        self.addCleanup(repository.stop)
        self.service = TabService(self.svc)

    def test_annotate_sets_tab_open_from_one_query_for_every_row(self) -> None:
        self.repository.list_open_tab_job_ids.return_value = {"j_a"}
        rows: list[dict[str, Any]] = [{"id": "j_a"}, {"id": "j_b"}, {"id": "j_a"}]
        self.service.annotate(rows)
        self.assertEqual([row["tabOpen"] for row in rows], [True, False, True])
        self.repository.list_open_tab_job_ids.assert_called_once_with("mac", ["j_a", "j_b"])

    def test_annotate_honours_a_different_key_and_empty_input(self) -> None:
        self.repository.list_open_tab_job_ids.return_value = {"j_w"}
        workers: list[dict[str, Any]] = [{"job": "j_w"}]
        self.service.annotate(workers, key="job")
        self.assertTrue(workers[0]["tabOpen"])
        self.service.annotate([])

    def test_focus_without_a_recorded_tab_reports_no_tab(self) -> None:
        for row in (None, {"target_id": None, "close_requested": False}, {"target_id": "t", "close_requested": True}):
            with self.subTest(row=row):
                self.repository.find_session_tab.return_value = row
                self.assertEqual(self.service.focus("j_a"), {"focused": False, "reason": "no_tab"})

    def _binder(self, *, raised: bool, group: Any = None, title: str = "") -> Any:
        binder: Any = MagicMock()
        binder.reveal.return_value = group
        binder.activate.return_value = raised
        binder.title.return_value = title
        self.svc.tab_binder.return_value = binder
        self.repository.find_session_tab.return_value = {"target_id": "7", "close_requested": False}
        return binder

    def test_focus_expands_the_group_and_reports_focused_when_neo_raises_the_page(self) -> None:
        group = TabGroup("g1", "jobpilot/job-applications", "yellow", ("7",))
        binder = self._binder(raised=True, group=group, title="Apply - Acme")
        self.assertEqual(
            self.service.focus("j_a"),
            {
                "focused": True,
                "reason": "focused",
                "page": "7",
                "title": "Apply - Acme",
                "group": "jobpilot/job-applications",
                "color": "yellow",
            },
        )
        binder.reveal.assert_called_once_with("7")
        binder.activate.assert_called_once_with("7")

    def test_focus_falls_back_to_naming_group_page_and_title(self) -> None:
        group = TabGroup("g1", "jobpilot/job-applications", "yellow", ("7",))
        self._binder(raised=False, group=group, title="Apply - Acme")
        result = self.service.focus("j_a")
        self.assertEqual((result["focused"], result["reason"]), (False, "unsupported"))
        self.assertEqual((result["page"], result["group"], result["title"]), ("7", "jobpilot/job-applications", "Apply - Acme"))

    def test_focus_without_group_or_title_still_reports_the_page(self) -> None:
        self._binder(raised=False)
        self.assertEqual(self.service.focus("j_a"), {"focused": False, "reason": "unsupported", "page": "7"})

    def test_focus_is_read_only_for_job_state(self) -> None:
        self._binder(raised=True)
        self.service.focus("j_a")
        self.svc.event.assert_not_called()
        self.svc.db.run.assert_not_called()
        self.svc.db.transaction.assert_not_called()


if __name__ == "__main__":
    unittest.main()

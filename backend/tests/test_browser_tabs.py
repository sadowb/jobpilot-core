"""Tab binding of jobs to Neo pages (ADR 0019); no Neo, browser or database starts."""

import unittest
from typing import Any
from unittest.mock import MagicMock

from jobpilot.browser.neo import BrowserError, BrowserUnavailable, Tab, TabGroup
from jobpilot.browser.tabs import BrowserOffline, TabBinder
from jobpilot.config import Settings
from jobpilot.repository.browser import BrowserRepository
from jobpilot.service import Services
from jobpilot.service.browser import BrowserService


class TabBinderRevealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.neo: Any = MagicMock()
        self.binder = TabBinder(Settings(_env_file=None), self.neo)

    def test_reveal_expands_only_the_group_holding_the_page(self) -> None:
        mine = TabGroup("1", "jobpilot/job-applications", "yellow", ("7",))
        self.neo.tab_groups.return_value = [TabGroup("2", "x", "blue", ("9",)), mine]
        self.assertEqual(self.binder.reveal("7"), mine)
        self.neo.expand_group.assert_called_once_with("1")

    def test_reveal_swallows_neo_errors(self) -> None:
        self.neo.tab_groups.side_effect = BrowserUnavailable("down")
        self.assertIsNone(self.binder.reveal("7"))
        self.neo.tab_groups.side_effect = None
        self.neo.tab_groups.return_value = [TabGroup("1", "g", "red", ("7",))]
        self.neo.expand_group.side_effect = BrowserError("no")
        self.assertIsNone(self.binder.reveal("7"))

    def test_activate_errors_are_false(self) -> None:
        self.neo.activate.side_effect = BrowserUnavailable("down")
        self.assertFalse(self.binder.activate("7"))


class TabBinderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.neo: Any = MagicMock()
        self.neo.open_tab.return_value = Tab("7", "about:blank")
        self.binder = TabBinder(Settings(_env_file=None), self.neo)

    def test_provision_opens_one_blank_tab(self) -> None:
        tab = self.binder.provision()
        self.neo.open_tab.assert_called_once_with("about:blank")
        self.assertEqual(tab.target_id, "7")

    def test_unreachable_neo_is_offline_not_a_failure(self) -> None:
        self.neo.open_tab.side_effect = BrowserUnavailable("down")
        with self.assertRaises(BrowserOffline):
            self.binder.provision()

    def test_neo_refusing_to_open_is_also_offline_backpressure(self) -> None:
        self.neo.open_tab.side_effect = BrowserError("no")
        with self.assertRaises(BrowserOffline):
            self.binder.provision()

    def test_release_closes_the_tab(self) -> None:
        self.assertTrue(self.binder.release("7"))
        self.neo.close_tab.assert_called_once_with("7")

    def test_release_without_a_target_is_a_no_op_success(self) -> None:
        self.assertTrue(self.binder.release(None))
        self.neo.close_tab.assert_not_called()

    def test_release_with_neo_down_succeeds_because_nothing_is_left_to_close(self) -> None:
        self.neo.close_tab.side_effect = BrowserUnavailable("down")
        self.assertTrue(self.binder.release("7"))

    def test_release_refused_by_neo_reports_failure_for_a_retry(self) -> None:
        self.neo.close_tab.side_effect = BrowserError("Not allowed")
        self.assertFalse(self.binder.release("7"))


class EnsureTabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app: Any = MagicMock(spec=Services)
        self.app.db = MagicMock()
        self.app.node = "mac"
        self.app.cfg = Settings(_env_file=None)
        self.service = BrowserService(self.app)
        self.repository: Any = MagicMock(spec=BrowserRepository)
        self.service.repository = self.repository
        self.binder: Any = MagicMock(spec=TabBinder)
        self.binder.browser = MagicMock()
        self.service._tabs = self.binder
        self.binder.provision.return_value = Tab("7", "about:blank")
        self.repository.find_session_tab.return_value = None

    def test_new_session_records_the_target_in_postgres(self) -> None:
        self.service.ensure_tab("j_1")
        self.binder.provision.assert_called_once_with()
        self.repository.insert_browser_sessions_for_browser_opened.assert_called_once_with("j_1", "mac", "7")

    def test_a_fresh_tab_is_revealed_before_it_is_announced(self) -> None:
        self.service.ensure_tab("j_1")
        self.binder.reveal.assert_called_once_with("7")

    def test_a_reveal_failure_never_fails_the_binding(self) -> None:
        self.binder.reveal.return_value = None
        self.service.ensure_tab("j_1")
        self.repository.insert_browser_sessions_for_browser_opened.assert_called_once()

    def test_an_existing_tab_is_reused(self) -> None:
        self.repository.find_session_tab.return_value = {"node": "mac", "target_id": "3", "close_requested": False}
        self.binder.browser.tabs.return_value = [Tab("3", "about:blank")]
        self.service.ensure_tab("j_1")
        self.binder.provision.assert_not_called()

    def test_a_legacy_row_without_a_target_gets_a_tab(self) -> None:
        self.repository.find_session_tab.return_value = {"node": None, "target_id": None, "close_requested": False}
        self.service.ensure_tab("j_1")
        self.binder.provision.assert_called_once_with()

    def test_failure_to_record_closes_the_fresh_tab(self) -> None:
        self.repository.insert_browser_sessions_for_browser_opened.side_effect = RuntimeError("db")
        with self.assertRaises(RuntimeError):
            self.service.ensure_tab("j_1")
        self.binder.release.assert_called_once_with("7")

    def test_drop_tab_closes_and_deletes_the_recorded_session(self) -> None:
        self.repository.find_session_tab.return_value = {"node": "mac", "target_id": "7"}
        self.service.drop_tab("j_1")
        self.binder.release.assert_called_once_with("7")
        self.repository.delete_browser_sessions_for_close_browser_locked.assert_called_once_with("j_1", "mac")

    def test_the_agents_tab_comes_from_postgres_not_from_process_state(self) -> None:
        self.repository.find_session_tab.return_value = {"node": "mac", "target_id": "7", "close_requested": False}
        self.assertEqual(self.service.target_id("j_1"), "7")
        self.repository.find_session_tab.return_value = {"node": "mac", "target_id": "7", "close_requested": True}
        self.assertIsNone(self.service.target_id("j_1"))
        self.repository.find_session_tab.return_value = None
        self.assertIsNone(self.service.target_id("j_1"))


if __name__ == "__main__":
    unittest.main()

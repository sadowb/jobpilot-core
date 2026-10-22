"""Parked-tab watcher (ADR 0026, amended): pure judgement, the pass over parked tabs, and the idempotent record."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

from jobpilot.browser.neo import PageState
from jobpilot.repository.approvals import ApprovalsRepository
from jobpilot.service import Services
from jobpilot.service.approvals import ApprovalsService
from jobpilot.service.park_watch import ParkWatchService, Reading, fingerprint, judge

PATTERNS = [r"thank you for applying", r"application (has been )?(received|submitted)"]
FORM = "Apply to Acme\nFirst name\nLast name\nEmail\nResume/CV\nWhy do you want to work here?\nSubmit application"
INVENTORY = [{"label": label} for label in ("First name", "Last name", "Email", "Resume/CV", "Why do you want to work here?")]
URL = "https://boards.greenhouse.io/acme/jobs/1?gh_src=abc"


def state(text: str, url: str = URL, ready: str = "complete") -> PageState:
    return PageState(url, ready, text, "Acme careers")


class JudgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = fingerprint(state(FORM), PATTERNS, INVENTORY)

    def test_baseline_is_hashed_query_free_and_records_the_visible_labels(self) -> None:
        self.assertEqual(self.baseline["url"], "https://boards.greenhouse.io/acme/jobs/1")
        self.assertEqual(len(self.baseline["textSha"]), 64)
        self.assertEqual(len(self.baseline["labels"]), 5)
        self.assertFalse(self.baseline["confirmed"])
        self.assertNotIn("gh_src", str(self.baseline))

    def test_new_route_plus_confirmation_text_is_a_detected_submit(self) -> None:
        confirmation = state("Thank you for applying to Acme.", "https://boards.greenhouse.io/acme/jobs/1/confirmation")
        self.assertEqual(judge(self.baseline, confirmation, PATTERNS), "detected")

    def test_form_replaced_in_place_by_a_confirmation_is_detected(self) -> None:
        self.assertEqual(judge(self.baseline, state("Your application has been received."), PATTERNS), "detected")

    def test_confirmation_text_while_the_form_is_still_there_only_asks(self) -> None:
        mixed = state(FORM + "\nThank you for applying (see our other roles)")
        self.assertEqual(judge(self.baseline, mixed, PATTERNS), "looks_submitted")

    def test_page_moved_without_confirmation_is_ambiguous(self) -> None:
        moved = state("Welcome to Acme careers. Open roles", "https://acme.com/careers")
        self.assertEqual(judge(self.baseline, moved, PATTERNS), "looks_submitted")

    def test_an_unchanged_or_edited_form_triggers_nothing(self) -> None:
        self.assertEqual(judge(self.baseline, state(FORM), PATTERNS), "none")
        self.assertEqual(judge(self.baseline, state(FORM + "\nThis field is required"), PATTERNS), "none")

    def test_a_posting_that_already_says_thank_you_never_triggers_a_detection(self) -> None:
        thanking = state(FORM + "\nThank you for applying! We read every application.")
        baseline = fingerprint(thanking, PATTERNS, INVENTORY)
        self.assertTrue(baseline["confirmed"])
        self.assertEqual(judge(baseline, thanking, PATTERNS), "none")
        moved = state("Thank you for applying! We read every application.", "https://boards.greenhouse.io/acme/jobs/1/done")
        self.assertEqual(judge(baseline, moved, PATTERNS), "looks_submitted")  # asks; never records on its own

    def test_a_loading_or_blank_page_is_not_judged(self) -> None:
        self.assertEqual(judge(self.baseline, state("Thank you for applying", ready="loading"), PATTERNS), "none")
        self.assertEqual(judge(self.baseline, state("  "), PATTERNS), "none")

    def test_a_small_form_cannot_be_called_gone(self) -> None:
        tiny = fingerprint(state("Name\nSubmit"), PATTERNS, [{"label": "Name"}])
        self.assertEqual(judge(tiny, state("Something else entirely"), PATTERNS), "none")


class WatchPassTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc: Any = MagicMock(spec=Services)
        self.svc.db = MagicMock()
        self.svc.node = "mac"
        self.svc.auto_detected_submit.return_value = True
        self.watch = ParkWatchService(self.svc)
        self.repo: Any = MagicMock()
        self.watch.repository = self.repo
        self.repo.update_signal.return_value = 1
        baseline = fingerprint(state(FORM), PATTERNS, INVENTORY)
        self.row: dict[str, Any] = {
            "id": "j1",
            "board_id": "greenhouse",
            "target_id": "7",
            "park_baseline": baseline,
            "park_signal": None,
            "inventory": INVENTORY,
        }
        self.repo.list_watchable.return_value = [self.row]
        patcher = patch("jobpilot.service.park_watch.adapter_by_id")
        adapter = patcher.start()
        adapter.return_value.confirmation_patterns = PATTERNS
        self.addCleanup(patcher.stop)
        self.slept: list[float] = []

    def run_pass(self, *readings: Reading) -> dict[str, int]:
        queue = list(readings)
        return self.watch.run_once(reader=lambda _: queue.pop(0), sleep=self.slept.append)

    def test_detected_twice_in_a_row_records_the_submit_once(self) -> None:
        done = Reading(state("Thank you for applying.", "https://boards.greenhouse.io/acme/jobs/1/done"))
        counts = self.run_pass(done, done)
        self.assertEqual(counts["detected"], 1)
        self.svc.auto_detected_submit.assert_called_once_with("j1")
        self.assertEqual(self.slept, [2.0])

    def test_a_verdict_that_does_not_hold_on_the_recheck_records_nothing(self) -> None:
        done = Reading(state("Thank you for applying.", "https://boards.greenhouse.io/acme/jobs/1/done"))
        counts = self.run_pass(done, Reading(state(FORM)))
        self.assertEqual(counts["detected"], 0)
        self.svc.auto_detected_submit.assert_not_called()

    def test_the_manual_button_winning_the_race_is_not_an_error(self) -> None:
        self.svc.auto_detected_submit.return_value = False
        done = Reading(state("Thank you for applying."))
        self.assertEqual(self.run_pass(done, done)["detected"], 0)
        self.svc.auto_detected_submit.side_effect = PermissionError("not waiting")
        self.assertEqual(self.run_pass(done, done)["detected"], 0)

    def test_ambiguous_change_sets_the_signal_once_and_writes_one_event(self) -> None:
        moved = Reading(state("Open roles at Acme", "https://acme.com/careers"))
        self.assertEqual(self.run_pass(moved)["looks_submitted"], 1)
        self.repo.update_signal.assert_called_once_with("j1", "looks_submitted", None)
        self.assertEqual(self.svc.event.call_args.args[1:], ("park_signal", "system", {"signal": "looks_submitted"}))
        self.svc.event.reset_mock()
        self.row["park_signal"] = "looks_submitted"
        self.run_pass(moved)
        self.svc.event.assert_not_called()  # already flagged

    def test_a_closed_tab_asks_did_you_submit(self) -> None:
        self.assertEqual(self.run_pass(Reading(None, gone=True))["tab_closed"], 1)
        self.repo.update_signal.assert_called_once_with("j1", "tab_closed", None)

    def test_an_unreadable_tick_is_not_a_signal(self) -> None:
        self.assertEqual(self.run_pass(Reading(None))["unreadable"], 1)
        self.repo.update_signal.assert_not_called()
        self.svc.auto_detected_submit.assert_not_called()

    def test_a_reader_that_raises_is_unreadable_and_does_not_stop_the_pass(self) -> None:
        def broken(_: str) -> Reading:
            raise RuntimeError("neo down")

        self.assertEqual(self.watch.run_once(reader=broken)["unreadable"], 1)

    def test_nothing_parked_means_no_neo_calls(self) -> None:
        self.repo.list_watchable.return_value = []
        reader = MagicMock()
        self.assertEqual(self.watch.run_once(reader=reader)["checked"], 0)
        reader.assert_not_called()

    def test_only_this_nodes_watchable_jobs_are_listed(self) -> None:
        self.watch.run_once(reader=lambda _: Reading(None))
        self.repo.list_watchable.assert_called_once_with("mac", 40)


class BaselineRecoveryTests(unittest.TestCase):
    """A job whose baseline failed to store when it parked (Neo dropped one call) must not stay invisible forever."""

    def setUp(self) -> None:
        self.svc: Any = MagicMock(spec=Services)
        self.svc.db = MagicMock()
        self.svc.node = "mac"
        self.svc.cfg = MagicMock()
        self.svc.job.return_value = {"board_id": "greenhouse"}
        self.watch = ParkWatchService(self.svc)
        self.repo: Any = MagicMock()
        self.watch.repository = self.repo
        self.repo.update_signal.return_value = 1
        self.repo.list_unbaselined.return_value = [{"id": "j1", "target_id": "7"}]
        for target, value in (
            ("jobpilot.service.park_watch.adapter_by_id", MagicMock(**{"return_value.confirmation_patterns": PATTERNS})),
            ("jobpilot.service.park_watch.neo_browser", MagicMock()),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def neo(self) -> Any:
        from jobpilot.service import park_watch

        return park_watch.neo_browser.return_value

    def test_baseline_storing_is_retried_after_a_dropped_call(self) -> None:
        from jobpilot.browser.neo import BrowserUnavailable

        self.neo().page_state.side_effect = [BrowserUnavailable("x"), state(FORM)]
        self.repo.find_form_for_baseline.return_value = {"inventory": INVENTORY}
        self.assertTrue(self.watch.store_baseline("j1", sleep=lambda _: None))
        self.repo.update_baseline.assert_called_once()

    def test_an_unbaselined_parked_job_gets_its_baseline_on_a_later_tick(self) -> None:
        self.neo().page_state.return_value = state(FORM)
        self.repo.find_form_for_baseline.return_value = {"inventory": INVENTORY}
        self.assertEqual(self.watch.adopt_unbaselined(), 1)
        self.repo.update_baseline.assert_called_once()

    def test_a_tab_that_already_shows_the_confirmation_asks_instead_of_becoming_the_baseline(self) -> None:
        self.neo().page_state.return_value = state("Thank you for applying to Acme", url=URL + "/done")
        self.assertEqual(self.watch.adopt_unbaselined(), 0)
        self.repo.update_baseline.assert_not_called()
        self.repo.update_signal.assert_called_once_with("j1", "looks_submitted", None)


class AutoDetectedRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app: Any = MagicMock(spec=Services)
        self.app.db = MagicMock()
        self.app.form.return_value = {"draft_hash": "draft-one", "verified_at": datetime.now(UTC), "pages_complete": True}
        self.app.job.return_value = {"status": "awaiting_approval", "cv_version_id": 4}
        self.app.submit_clicked.return_value = False
        self.service = ApprovalsService(self.app)
        self.repository: Any = MagicMock(spec=ApprovalsRepository)
        self.service.repository = self.repository
        self.repository.find_unconsumed_submit_approval.return_value = None
        self.repository.find_applications_for_record_submitted.return_value = None

    def test_auto_detected_is_the_same_record_as_the_button_with_the_method_noted(self) -> None:
        self.assertTrue(self.service.record_owner_submit("j1", True, "auto-detected"))
        events = {c.args[1]: c.args for c in self.app.event.call_args_list}
        self.assertEqual(events["approval"][3]["method"], "auto-detected")
        self.assertEqual(events["approval"][2], "user")
        self.assertEqual(events["confirmation"][3]["method"], "auto-detected")
        args = self.repository.insert_applications_for_record_submitted.call_args.args
        self.assertEqual(args[2], "live-verified")
        self.assertIn("saw the confirmation", args[3])

    def test_the_manual_button_after_a_detection_is_a_noop_and_vice_versa(self) -> None:
        self.assertTrue(self.service.owner_submitted("j1", user_session=True, confirmation_seen=True))
        self.repository.find_applications_for_record_submitted.return_value = {"x": 1}
        self.assertFalse(self.service.record_owner_submit("j1", True, "auto-detected"))
        self.assertEqual(self.repository.insert_applications_for_record_submitted.call_count, 1)

    def test_the_http_entry_point_still_requires_a_user_session(self) -> None:
        with self.assertRaises(PermissionError):
            self.service.owner_submitted("j1", user_session=False)


class ServiceGuardTests(unittest.TestCase):
    def test_auto_detected_submit_does_nothing_when_already_recorded_or_not_parked(self) -> None:
        app: Any = MagicMock(spec=Services)
        app.approvals = MagicMock()
        for recorded, status in (({"x": 1}, "awaiting_approval"), (None, "submitted")):
            app.approvals.repository.find_applications_for_record_submitted.return_value = recorded
            app.job.return_value = {"status": status}
            with patch.object(Services, "_record_auto_detected") as record:
                self.assertFalse(Services.auto_detected_submit(app, "j1"))
            record.assert_not_called()


if __name__ == "__main__":
    unittest.main()

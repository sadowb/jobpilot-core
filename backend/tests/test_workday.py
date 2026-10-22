"""Workday (ADR 0032): review-only ceiling instead of a forced-manual hardcode, and the public CXS prefetch."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from jobpilot import posting_fetch
from jobpilot.boards import (
    ADAPTERS,
    CUSTOM,
    BoardAdapter,
    BoardMatch,
    adapter_by_id,
    cap_policy,
    entry_url,
    exceeds_ceiling,
    recipe_text,
    resolve_board,
)
from jobpilot.domain.posting_form import parse_workday
from jobpilot.service.jobs import JobsService
from jobpilot.service.profile import ProfileService
from jobpilot.service.submit_mode import SubmitModeService

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "posting_forms" / "workday_job.json").read_text())
URL = "https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/Remote-Canada/Senior-Software-Engineer_R-12345"


class CeilingTests(unittest.TestCase):
    def test_workday_may_be_set_to_any_policy_and_the_ceiling_mechanism_still_clamps(self) -> None:
        self.assertEqual(adapter_by_id("workday").max_automation, "auto")
        self.assertEqual([cap_policy("workday", p) for p in ("manual", "assisted", "auto")], ["manual", "assisted", "auto"])
        self.assertFalse(exceeds_ceiling("workday", "auto"))
        capped = BoardAdapter("x", "X", "manual", lambda _u: False, lambda u: BoardMatch(u), max_automation="assisted")
        with patch("jobpilot.boards.adapter_by_id", return_value=capped):
            self.assertEqual(cap_policy("x", "auto"), "assisted")
            self.assertTrue(exceeds_ceiling("x", "auto"))

    def board_policy(self, stored: str | None) -> str:
        repo = MagicMock()
        repo.find_boards_for_board_policy.return_value = {"automation": stored} if stored else None
        return JobsService.board_policy(SimpleNamespace(repository=repo), "workday")  # type: ignore[arg-type]

    def test_effective_policy_is_what_the_owner_set_and_manual_by_default(self) -> None:
        self.assertEqual(self.board_policy("manual"), "manual")  # off by default: the seeded value
        self.assertEqual(self.board_policy("assisted"), "assisted")
        self.assertEqual(self.board_policy("auto"), "auto")
        self.assertEqual(self.board_policy(None), "manual")

    def test_auto_still_needs_every_existing_safeguard(self) -> None:
        svc: Any = MagicMock()
        svc.board_policy.return_value = "auto"
        svc.runtime_settings.return_value.submitMode = "auto"
        svc.jobs.repository.find_job_events_for_kind.return_value = []
        svc.job.return_value = {"board_id": "workday", "submit_mode": None, "try_questions_anyway": True}
        self.assertEqual(SubmitModeService(svc).decide("j").mode, "review")  # unanswered required questions force review
        svc.job.return_value = {"board_id": "workday", "submit_mode": None}
        self.assertEqual(SubmitModeService(svc).decide("j").mode, "auto")
        svc.job.return_value = {"board_id": "workday", "submit_mode": "review"}
        self.assertEqual(SubmitModeService(svc).decide("j").mode, "review")  # a per-job override wins


class EntryTests(unittest.TestCase):
    def test_workday_starts_in_the_manual_wizard_once(self) -> None:
        manual = URL + "/apply/applyManually"
        self.assertEqual(entry_url("workday", URL), manual)
        self.assertEqual(entry_url("workday", URL + "/"), manual)
        self.assertEqual(entry_url("workday", URL + "?source=x"), manual)
        self.assertEqual(entry_url("workday", manual), manual)
        self.assertEqual(entry_url("greenhouse", URL), URL)
        self.assertEqual(entry_url(None, URL), URL)

    def test_the_workday_recipe_names_the_manual_wizard_and_the_review_stop(self) -> None:
        notes = recipe_text(Path(__file__).resolve().parents[2] / "agent", "workday")
        self.assertIn('Never use "Autofill with Resume"', notes)
        self.assertIn("On the step named Review the normal submit rule applies", notes)
        self.assertIn("Shared recipe", notes)  # _common first

    def test_every_board_has_a_recipe_and_unknown_ids_cannot_name_a_path(self) -> None:
        agent = Path(__file__).resolve().parents[2] / "agent"
        for adapter in (*ADAPTERS, CUSTOM):
            self.assertIn(adapter.display_name.split()[-1].lower(), recipe_text(agent, adapter.id).lower(), adapter.id)
        self.assertEqual(recipe_text(agent, "../NEO"), recipe_text(agent, None))
        self.assertLessEqual(len(recipe_text(agent, "workday")), 2000)


class DefaultPolicyTests(unittest.TestCase):
    def test_account_heavy_unverified_boards_default_to_review(self) -> None:
        for board in ("icims", "taleo", "successfactors", "handshake", "custom"):
            self.assertEqual(adapter_by_id(board).automation, "assisted", board)
        for board in ("greenhouse", "lever", "ashby", "workable", "bamboohr", "smartrecruiters", "eightfold"):
            self.assertEqual(adapter_by_id(board).automation, "auto", board)
        for board in ("workday", "linkedin", "indeed"):
            self.assertEqual(adapter_by_id(board).automation, "manual", board)


class BoardUpdateTests(unittest.TestCase):
    def update(self, board: str, automation: str | None) -> None:
        repo = MagicMock()
        repo.find_automation_for_board_update.return_value = {"enabled": False}
        repo.find_tasks_for_board_update.return_value = []
        ProfileService.board_update(SimpleNamespace(repository=repo, application=MagicMock()), board, automation)  # type: ignore[arg-type]

    def test_owner_may_choose_any_policy_for_workday_when_automation_is_stopped(self) -> None:
        for choice in ("manual", "assisted", "auto", None):
            self.update("workday", choice)

    def test_a_board_with_a_lower_ceiling_is_refused(self) -> None:
        capped = BoardAdapter("x", "X", "manual", lambda _u: False, lambda u: BoardMatch(u), max_automation="assisted")
        with (
            patch("jobpilot.service.profile.adapter_by_id", return_value=capped),
            patch("jobpilot.boards.adapter_by_id", return_value=capped),
            self.assertRaises(PermissionError) as raised,
        ):
            self.update("x", "auto")
        self.assertIn("cannot be set above assisted", str(raised.exception))

    def test_changes_require_stopped_automation(self) -> None:
        repo = MagicMock()
        repo.find_automation_for_board_update.return_value = {"enabled": True}
        with self.assertRaises(PermissionError):
            ProfileService.board_update(SimpleNamespace(repository=repo, application=MagicMock()), "workday", "auto")  # type: ignore[arg-type]


class PrefetchTests(unittest.TestCase):
    def test_parser_reads_the_public_cxs_document(self) -> None:
        form = parse_workday(FIXTURE)
        self.assertEqual((form.provider, form.questions), ("workday", ()))
        meta = form.meta
        self.assertEqual(
            (meta.role, meta.company, meta.work_mode, meta.employment_type),
            ("Senior Software Engineer", "Acme Corp", "Remote", "Full-time"),
        )
        self.assertIn("Toronto, Canada", meta.location or "")
        self.assertEqual(meta.description, "Build things . Python SQL")
        self.assertEqual(parse_workday({}).meta.role, None)

    def test_url_is_mapped_to_the_cxs_endpoint_without_a_browser(self) -> None:
        with patch.object(posting_fetch, "_get", return_value=json.dumps(FIXTURE)) as get:
            form = posting_fetch.fetch_posting_form(URL + "/apply/applyManually")
        assert form is not None
        self.assertEqual(form.meta.role, "Senior Software Engineer")
        self.assertEqual(
            get.call_args.args[0],
            "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Careers/job/Remote-Canada/Senior-Software-Engineer_R-12345",
        )

    def test_other_hosts_and_failures_fall_back_to_the_browser(self) -> None:
        with patch.object(posting_fetch, "_get", side_effect=OSError("down")):
            self.assertIsNone(posting_fetch.fetch_posting_form(URL))
        with patch.object(posting_fetch, "_get") as get:
            self.assertIsNone(posting_fetch._workday("https://evil.example/Careers/job/x_1"))
            self.assertIsNone(posting_fetch._workday("https://acme.wd5.myworkdayjobs.com/Careers/not-a-job"))
            get.assert_not_called()

    def test_the_board_still_resolves_to_workday(self) -> None:
        self.assertEqual(resolve_board(URL)[0].id, "workday")


if __name__ == "__main__":
    unittest.main()

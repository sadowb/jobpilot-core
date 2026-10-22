"""Refresh details (ADR 0029): placeholder detection, field-by-field merge, and a service that never moves the job."""

from __future__ import annotations

import re
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from jobpilot.domain.details import (
    ats_host_regex,
    is_ats_host,
    is_placeholder_company,
    is_placeholder_role,
    missing_fields,
    plan_merge,
)
from jobpilot.service.application import Services
from jobpilot.service.details import DetailsService

HOSTS = ["ats.rippling.com"]
BLANK = {"role": "(role not captured yet)", "company": "ats.rippling.com", "location": None, "work_mode": "", "pay": None,
         "job_level": None, "employment_type": None}  # fmt: skip


class DomainTests(unittest.TestCase):
    def test_role_placeholders(self) -> None:
        for value in (None, "", "  ", "(role not captured yet)", "(Role not captured)"):
            self.assertTrue(is_placeholder_role(value), value)
        self.assertFalse(is_placeholder_role("Backend Engineer"))

    def test_company_placeholders(self) -> None:
        for value in (None, "", "ats.rippling.com", "jobs.ashbyhq.com", "boards.greenhouse.io", "Greenhouse", "careers.acme.io"):
            self.assertTrue(is_placeholder_company(value, ["careers.acme.io"]), value)
        for value in ("Acme", "Booking.com", "Rippling", "Stripe, Inc."):
            self.assertFalse(is_placeholder_company(value, ["careers.acme.io"]), value)

    def test_the_sql_host_regex_matches_the_python_rule(self) -> None:
        regex = re.compile(ats_host_regex())
        for host in ("ats.rippling.com", "jobs.ashbyhq.com", "boards.greenhouse.io", "x.myworkdayjobs.com", "booking.com",
                     "rippling.com", "notgreenhouse.io", "acme.io"):  # fmt: skip
            self.assertEqual(bool(regex.search(host)), is_ats_host(host), host)

    def test_placeholders_and_empties_are_filled(self) -> None:
        found = {"role": "Backend Engineer", "company": "Acme", "location": "Remote", "work_mode": "Remote", "pay": "$1"}
        self.assertEqual(plan_merge(BLANK, found, HOSTS), found)

    def test_real_values_are_never_replaced(self) -> None:
        current = {**BLANK, "role": "Staff Engineer", "company": "Acme", "location": "Paris"}
        changes = plan_merge(current, {"role": "Other", "company": "Other", "location": "Rome", "pay": "$1"}, HOSTS)
        self.assertEqual(changes, {"pay": "$1"})

    def test_owner_edited_fields_are_never_overwritten_even_if_placeholder_like(self) -> None:
        found = {"role": "Backend Engineer", "location": "Remote"}
        changes = plan_merge(BLANK, found, HOSTS, owner_edited={"role", "location"})
        self.assertEqual(changes, {})
        self.assertNotIn("role", missing_fields(BLANK, HOSTS, {"role"}))

    def test_a_placeholder_is_not_an_improvement(self) -> None:
        found = {"role": "(role not captured yet)", "company": "ats.rippling.com", "location": "   "}
        self.assertEqual(plan_merge(BLANK, found, HOSTS), {})


def job_row(**over: Any) -> dict[str, Any]:
    return {"id": "j1", "status": "submitted", "url": "https://ats.rippling.com/acme/jobs/1", "apply_url": None, **BLANK,
            "archived_at": None, "posting_len": 500, **over}  # fmt: skip


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc: Any = MagicMock(spec=Services)
        self.svc.db = MagicMock()
        self.svc.cfg = MagicMock()
        self.svc.cfg.agent_model_cv = "claude-haiku-5-5"
        self.svc.upsert_company.return_value = 7
        self.svc.approvals = MagicMock()
        self.svc.tab_binder.return_value.online.return_value = False
        self.svc.runtime_settings.return_value.workerConcurrency = 8
        self.svc.runtime_settings.return_value.maxOpenBrowsers = 6
        self.service = DetailsService(self.svc)
        self.repo = MagicMock()
        self.service.repository = self.repo
        self.repo.lock_job.return_value = {"id": "j1", "archived_at": None}
        self.repo.find_request.return_value = {"state": "running"}
        self.repo.find_job.return_value = job_row()
        self.repo.owner_edited_fields.return_value = set()
        self.repo.find_posting_text.return_value = ""
        self.repo.finish.return_value = 1

    # ---- request
    def test_request_needs_a_user_session_and_a_live_job(self) -> None:
        with self.assertRaises(PermissionError):
            self.service.request("j1", user_session=False)
        self.repo.lock_job.return_value = None
        with self.assertRaises(LookupError):
            self.service.request("j1", user_session=True)
        self.repo.lock_job.return_value = {"id": "j1", "archived_at": "2026-01-01"}
        with self.assertRaises(PermissionError):
            self.service.request("j1", user_session=True)

    def test_request_is_idempotent_while_one_is_open(self) -> None:
        self.repo.request.side_effect = [True, False]
        self.repo.find_request.return_value = {"state": "queued"}
        self.assertEqual(self.service.request("j1", user_session=True), {"ok": True, "already": False, "state": "queued"})
        self.assertEqual(self.service.request("j1", user_session=True)["already"], True)
        self.svc.request_dispatch.assert_called_once()

    def test_a_submitted_job_is_accepted_and_nothing_about_it_moves(self) -> None:
        self.repo.request.return_value = True
        self.service.request("j1", user_session=True)
        for name in ("wait", "resume", "transition", "enqueue", "invalidate_form"):
            getattr(self.svc, name).assert_not_called()

    def test_bulk_is_bounded_and_user_only(self) -> None:
        with self.assertRaises(PermissionError):
            self.service.request_missing(user_session=False)
        self.repo.list_missing.return_value = ["a", "b", "c"]
        self.repo.request.side_effect = [True, False, True]
        self.assertEqual(self.service.request_missing(user_session=True), {"ok": True, "queued": 2, "limit": 25})
        self.repo.list_missing.assert_called_once_with(25, 6)

    # ---- pump
    def test_pump_caps_by_concurrency_and_requeues_a_failed_publish(self) -> None:
        self.svc.runtime_settings.return_value.workerConcurrency = 1
        self.repo.expire_stale.return_value = ["old"]
        self.repo.claim.return_value = ["a", "b"]
        sent: list[str] = []

        def publish(job_id: str) -> None:
            if job_id == "b":
                raise RuntimeError("broker down")
            sent.append(job_id)

        self.assertEqual(self.service.pump(publish), 2)
        self.repo.claim.assert_called_once_with(1)
        self.assertEqual(sent, ["a"])
        self.repo.requeue.assert_called_once_with("b")
        self.assertEqual(self.svc.event.call_args_list[0].args[:2], ("old", "details_refresh_failed"))

    # ---- run
    def run_with(self, **stages: Any) -> str:
        patches = {
            "jobpilot.service.details.fetch_posting_form": stages.get("api", MagicMock(return_value=None)),
            "jobpilot.service.details.extract_posting": stages.get("extract", MagicMock(return_value={})),
            "jobpilot.service.details.clean_posting": stages.get("clean", MagicMock(return_value={"cleaned": False})),
        }
        with patch.multiple("jobpilot.service.details", **{k.rsplit(".", 1)[1]: v for k, v in patches.items()}):
            return self.service.run("j1")

    def form(self, **meta: str) -> Any:
        form = MagicMock()
        form.meta.as_set_meta.return_value = meta
        return form

    def written(self) -> dict[str, str]:
        return self.repo.apply.call_args.args[1] if self.repo.apply.called else {}

    def test_the_api_fills_everything_without_a_model_or_browser(self) -> None:
        clean = MagicMock()
        api = MagicMock(
            return_value=self.form(
                role="Backend Engineer",
                company="Acme",
                location="Remote",
                work_mode="Remote",
                pay="$1",
                job_level="Senior",
                employment_type="Full-time",
            )  # fmt: skip
        )
        self.assertEqual(self.run_with(api=api, clean=clean), "done")
        clean.assert_not_called()
        self.svc.tab_binder.return_value.provision.assert_not_called()
        self.assertEqual(set(self.written()), {"role", "company", "location", "work_mode", "pay", "job_level", "employment_type"})
        self.repo.apply.assert_called_once()
        self.assertEqual(self.repo.apply.call_args.args[2], 7)  # company_id
        self.assertEqual(set(self.repo.apply.call_args.args[3]), {"company_norm", "title_norm", "location_norm"})
        event = [c for c in self.svc.event.call_args_list if c.args[1] == "details_refreshed"][0]
        self.assertEqual(event.args[3]["fields"], sorted(self.written()))
        self.assertNotIn("Acme", str(event))  # field names only, never values
        self.svc.project.assert_called_once_with("j1")

    def test_the_job_is_never_moved(self) -> None:
        api = MagicMock(return_value=self.form(role="Backend Engineer", company="Acme"))
        self.run_with(api=api)
        for name in ("wait", "resume", "transition", "enqueue", "invalidate_form"):
            getattr(self.svc, name).assert_not_called()
        self.assertEqual(self.svc.approvals.method_calls, [])
        self.assertTrue(all(c.args[1].startswith("details_") for c in self.svc.event.call_args_list))

    def test_stored_posting_text_goes_to_the_tool_less_model(self) -> None:
        self.repo.find_posting_text.return_value = "x" * 500
        clean = MagicMock(return_value={"cleaned": True, "role": "Backend Engineer", "company": "Acme", "location": "Paris",
                                        "work_mode": "Hybrid", "pay": "$1", "job_level": "Mid", "employment_type": "Full-time"})  # fmt: skip
        extract = MagicMock()
        self.assertEqual(self.run_with(clean=clean, extract=extract), "done")
        extract.assert_not_called()
        self.assertEqual(clean.call_args.args[2], {"page_text": "x" * 500})

    def test_the_browser_stage_opens_one_tab_reads_and_always_closes_it(self) -> None:
        binder = self.svc.tab_binder.return_value
        binder.online.return_value = True
        binder.provision.return_value.target_id = "tab-9"
        clean = MagicMock(return_value={"cleaned": True, "role": "Backend Engineer", "company": "Acme"})
        with (
            patch("jobpilot.repository.automation.AutomationRepository") as automation,
            patch("jobpilot.service.direct.DirectService._read_page", return_value="y" * 400) as read,
            patch("jobpilot.service.details.DetailsService._embedded", return_value=None),
        ):
            automation.return_value.count_node_browsers.return_value = 0
            self.assertEqual(self.run_with(clean=clean), "done")
        read.assert_called_once()
        binder.release.assert_called_once_with("tab-9")
        self.assertEqual(clean.call_args.args[2], {"page_text": "y" * 400})

    def test_the_tab_is_closed_even_when_reading_fails(self) -> None:
        from jobpilot.browser.neo import BrowserError

        binder = self.svc.tab_binder.return_value
        binder.online.return_value = True
        binder.provision.return_value.target_id = "tab-9"
        with (
            patch("jobpilot.repository.automation.AutomationRepository") as automation,
            patch("jobpilot.service.direct.DirectService._read_page", side_effect=BrowserError("x")),
        ):
            automation.return_value.count_node_browsers.return_value = 0
            self.assertEqual(self.run_with(), "failed")
        binder.release.assert_called_once_with("tab-9")
        self.repo.finish.assert_called_with("j1", "failed", None, "page_unreadable")

    def test_an_offline_browser_fails_with_a_reason_and_changes_nothing(self) -> None:
        self.svc.tab_binder.return_value.online.return_value = False
        self.assertEqual(self.run_with(), "failed")
        self.repo.apply.assert_not_called()
        self.repo.finish.assert_called_with("j1", "failed", None, "browser_offline")

    def test_nothing_missing_is_done_without_work(self) -> None:
        self.repo.find_job.return_value = job_row(role="Staff Engineer", company="Acme", location="Paris", work_mode="Remote",
                                                  pay="$1", job_level="Senior", employment_type="Full-time")  # fmt: skip
        api = MagicMock()
        self.assertEqual(self.run_with(api=api), "done")
        api.assert_not_called()
        self.repo.finish.assert_called_with("j1", "done", None, "nothing_missing")

    def test_an_empty_posting_text_is_filled_from_the_api(self) -> None:
        self.repo.find_job.return_value = job_row(
            role="Staff Engineer", company="Acme", location="Paris", work_mode="Remote", pay="$1", job_level="Senior",
            employment_type="Full-time", posting_len=0,
        )  # fmt: skip
        api = MagicMock(return_value=self.form(posting="x" * 300))
        self.assertEqual(self.run_with(api=api), "done")
        self.repo.set_posting_text.assert_called_once_with("j1", "x" * 300)
        self.repo.apply.assert_not_called()

    def test_the_owner_edit_is_re_read_at_write_time(self) -> None:
        self.repo.owner_edited_fields.side_effect = [set(), {"location"}]  # edited between the read and the write
        api = MagicMock(return_value=self.form(role="Backend Engineer", location="Remote"))
        self.run_with(api=api)
        self.assertEqual(self.written(), {"role": "Backend Engineer"})

    def test_a_request_that_is_not_running_is_skipped(self) -> None:
        self.repo.find_request.return_value = {"state": "done"}
        self.assertEqual(self.service.run("j1"), "skipped")
        self.repo.apply.assert_not_called()

    def test_an_unexpected_error_still_ends_the_request(self) -> None:
        self.repo.find_job.side_effect = RuntimeError("db")
        self.assertEqual(self.service.run("j1"), "failed")
        self.repo.finish.assert_called_with("j1", "failed", None, "RuntimeError")


if __name__ == "__main__":
    unittest.main()

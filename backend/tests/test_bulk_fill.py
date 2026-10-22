"""About me batch preparation, resume preservation and numeric-only timing."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import MagicMock

from jobpilot.browser.bulk import BULK_SCAN_JS
from jobpilot.browser.neo import BrowserUnavailable, PageState
from jobpilot.config import Settings
from jobpilot.domain.answers import Known
from jobpilot.domain.canonical import norm
from jobpilot.service.application import Services
from jobpilot.service.bulk_fill import BulkFillService
from jobpilot.worker.claude import report_agent_timing


def field(label: str, kind: str = "text", **extra: Any) -> dict[str, Any]:
    return {"key": label, "selector": "#" + label.replace(" ", "-"), "label": label, "type": kind, "required": True, **extra}


class BulkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc: Any = MagicMock(spec=Services)
        self.svc.cfg = Settings(_env_file=None)
        self.svc.db = MagicMock()
        self.svc.node = "mac"
        self.svc.job.return_value = {
            "status": "discovered",
            "url": "https://fixture.test/application",
            "company_name": "Fixture",
            "company_slug": "fixture",
            "role": "Engineer",
        }
        self.svc.submit_clicked.return_value = False
        self.svc.load_known.return_value = Known(
            profile={"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.test", "phone": "12345"}
        )
        self.service = BulkFillService(self.svc)
        self.service.repository = MagicMock()
        self.service.repository.find_session_tab.return_value = {"target_id": "assigned"}
        self.service.chrome = MagicMock()
        self.service.chrome.page_state.return_value = PageState("https://fixture.test/application", "complete", "")
        self.check = MagicMock()

    def test_about_me_and_job_scoped_answers_are_mapped_without_model_calls(self) -> None:
        fields = [
            field("Full name"),
            field("Email", "email"),
            field("Phone", "tel"),
            field("Why this role", "textarea"),
            field("School", "custom-select"),
            field("Consent", "checkbox"),
            field("Password", "secret"),
        ]
        self.svc.load_known.return_value.answers = {norm("Why this role"): {"global": "generic", "job:j": "my saved job answer"}}
        self.service.chrome.evaluate.side_effect = [fields, [{"selector": f["selector"], "status": "filled"} for f in fields[:4]]]
        result = self.service.run("j", self.check)
        self.assertEqual(
            [f["value"] for f in result["fields"]], ["Ada Lovelace", "ada@example.test", "12345", "my saved job answer"]
        )
        self.assertEqual([f["kind"] for f in result["remaining"]], ["custom-select", "checkbox"])
        self.assertEqual(self.service.chrome.evaluate.call_count, 2)
        self.assertEqual(self.service.chrome.evaluate.call_args_list[0].args, ("assigned", BULK_SCAN_JS))
        payload = self.svc.event.call_args.args[3]
        self.assertNotIn("ada@example.test", json.dumps(payload))
        self.assertNotIn("my saved job answer", json.dumps(payload))

    def test_already_filled_fields_are_verified_or_preserved_for_agent_review(self) -> None:
        fields = [field("Email", "email", occupied=True), field("Phone", "tel", occupied=True)]
        self.service.chrome.evaluate.side_effect = [
            fields,
            [{"selector": fields[0]["selector"], "status": "filled"}, {"selector": fields[1]["selector"], "status": "preserved"}],
        ]
        result = self.service.run("j", self.check)
        self.assertEqual([f["label"] for f in result["fields"]], ["Email"])
        self.assertEqual([f["label"] for f in result["remaining"]], ["Phone"])

    def test_cv_upload_is_left_to_the_agent(self) -> None:
        fields = [field("Resume", "file"), field("Email", "email")]
        self.service.chrome.evaluate.side_effect = [fields, [{"selector": fields[1]["selector"], "status": "filled"}]]
        result = self.service.run("j", self.check)
        self.assertEqual([f["label"] for f in result["fields"]], ["Email"])
        self.assertEqual([f["label"] for f in result["remaining"]], ["Resume"])
        self.assertFalse(hasattr(self.service.chrome, "attach_file") and self.service.chrome.attach_file.called)

    def test_submit_runs_and_terminal_jobs_never_touch_the_page(self) -> None:
        for status in ("awaiting_approval", "submitted", "closed"):
            self.svc.job.return_value["status"] = status
            self.assertEqual(self.service.run("j", self.check), {})
        self.service.chrome.evaluate.assert_not_called()

    def test_resume_does_not_navigate_existing_application_and_chrome_failure_falls_back(self) -> None:
        self.service.chrome.evaluate.side_effect = BrowserUnavailable("offline")
        self.assertEqual(self.service.run("j", self.check), {})
        self.service.chrome.navigate.assert_not_called()

    def test_selected_cv_first_load_navigates_straight_to_ashby_application(self) -> None:
        url = "https://jobs.ashbyhq.com/fixture/12345678-1234-1234-1234-123456789abc"
        self.svc.job.return_value.update(url=url, board_id="ashby")
        self.service.chrome.page_state.return_value = PageState("about:blank", "complete", "")
        self.service.chrome.evaluate.side_effect = [[field("Email", "email")], [{"selector": "#Email", "status": "filled"}]]
        self.service.run("j", self.check)
        self.service.chrome.navigate.assert_called_once_with("assigned", url + "/application")


class TimingTests(unittest.TestCase):
    def test_timing_is_numbers_only(self) -> None:
        svc = MagicMock()
        report_agent_timing(svc, "j", {"streamSeconds": 30.0, "firstOutputSeconds": 2.0})
        svc.event.assert_called_once_with("j", "agent_timing", "system", {"streamSeconds": 30.0, "firstOutputSeconds": 2.0})

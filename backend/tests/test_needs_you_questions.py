"""Owner-question context on Needs You: field type, options, required and board; no database or worker starts."""

import asyncio
import json
import os
import tempfile
import unittest
from typing import Any, ClassVar
from unittest.mock import patch

from fastapi import FastAPI
from starlette.types import Message, Scope

from jobpilot.domain.answers import Known
from jobpilot.domain.question_view import (
    FALLBACK_LABEL,
    MAX_LABEL,
    MAX_OPTION,
    MAX_OPTIONS,
    missing_field_views,
)

Field = dict[str, Any]

NAME: Field = {"key": "#name", "label": "Name", "type": "text", "required": True, "selector": "#name"}
SELECT: Field = {
    "key": "#visa",
    "label": "Which team are you applying to?",
    "type": "select",
    "required": True,
    "options": ["Platform", "Research"],
}


class MissingFieldViewTests(unittest.TestCase):
    def test_text_field_carries_label_type_and_required(self) -> None:
        (view,) = missing_field_views([NAME], ["#name"])
        self.assertEqual(
            view,
            {
                "key": "#name",
                "label": "Name",
                "type": "text",
                "options": [],
                "optionsTotal": 0,
                "required": True,
                "multiple": None,
                "searchable": None,
                "unmatchedValue": None,
                "generic": True,  # "Name" is a profile alias: stable across jobs
            },
        )

    def test_choice_field_lists_its_options_and_follows_missing_order(self) -> None:
        views = missing_field_views([NAME, SELECT], ["#visa", "#name"])
        self.assertEqual([v["key"] for v in views], ["#visa", "#name"])
        self.assertEqual(views[0]["options"], ["Platform", "Research"])
        self.assertEqual(views[0]["optionsTotal"], 2)

    def test_multiple_flag_is_kept(self) -> None:
        field: Field = {**SELECT, "multiple": True}
        self.assertIs(missing_field_views([field], ["#visa"])[0]["multiple"], True)

    def test_legacy_inventory_without_required_or_options_still_serialises(self) -> None:
        (view,) = missing_field_views([{"key": "#q", "label": "Anything else?"}], ["#q"])
        self.assertEqual(view["type"], "text")
        self.assertEqual(view["options"], [])
        self.assertIsNone(view["required"])
        self.assertIsNone(view["multiple"])

    def test_unknown_keys_and_missing_inventory_yield_no_view(self) -> None:
        self.assertEqual(missing_field_views([NAME], ["#gone"]), [])
        self.assertEqual(missing_field_views(None, ["#name"]), [])
        self.assertEqual(missing_field_views([NAME], None), [])

    def test_blank_label_gets_a_neutral_fallback(self) -> None:
        (view,) = missing_field_views([{"key": "#q", "label": "  \n ", "type": "text"}], ["#q"])
        self.assertEqual(view["label"], FALLBACK_LABEL)

    def test_options_are_capped_deduplicated_and_clipped(self) -> None:
        options = ["", "  ", "Same", "Same", "x" * 5000] + [f"Option {n}" for n in range(200)]
        field: Field = {"key": "#c", "label": "Country", "type": "select", "required": True, "options": options}
        (view,) = missing_field_views([field], ["#c"])
        self.assertEqual(len(view["options"]), MAX_OPTIONS)
        self.assertEqual(view["optionsTotal"], 202)  # distinct, non-blank: Same, the long one, 200 numbered
        self.assertEqual(view["options"].count("Same"), 1)
        self.assertTrue(all(len(o) <= MAX_OPTION for o in view["options"]))

    def test_label_and_type_are_bounded(self) -> None:
        field: Field = {"key": "#q", "label": "a  b\n" + "z" * 5000, "type": "t" * 500}
        (view,) = missing_field_views([field], ["#q"])
        self.assertLessEqual(len(view["label"]), MAX_LABEL)
        self.assertTrue(view["label"].startswith("a b "))
        self.assertLessEqual(len(view["type"]), 40)

    def test_non_list_options_are_ignored(self) -> None:
        for bad in ("Yes,No", {"a": 1}, 7):
            with self.subTest(bad=bad):
                field: Field = {"key": "#q", "label": "Anything else?", "type": "text", "options": bad}
                self.assertEqual(missing_field_views([field], ["#q"])[0]["options"], [])

    def test_known_question_without_page_options_offers_its_fixed_answers(self) -> None:
        field: Field = {"key": "#g", "label": "Gender", "type": "text", "required": True}
        (view,) = missing_field_views([field], ["#g"])
        self.assertIn("Decline to answer", view["options"])
        self.assertEqual(view["optionsTotal"], len(view["options"]))

    def test_file_and_secret_fields_are_never_exposed(self) -> None:
        inventory: list[Field] = [
            {"key": "#cv", "label": "Resume", "type": "file", "required": True},
            {"key": "#pw", "label": "Password", "type": "secret", "required": True, "selector": "#pw"},
            NAME,
        ]
        views = missing_field_views(inventory, ["#cv", "#pw", "#name"])
        self.assertEqual([v["key"] for v in views], ["#name"])

    def test_view_never_carries_selectors_or_page_internals(self) -> None:
        field: Field = {**SELECT, "selector": "#secret-selector", "optionSelectors": {"Platform": "#p"}}
        (view,) = missing_field_views([field], ["#visa"])
        self.assertNotIn("secret-selector", json.dumps(view))
        self.assertNotIn("optionSelectors", view)


class FakeDashboard:
    def __init__(self, blocked: list[dict[str, Any]]) -> None:
        self._blocked = blocked

    def blocked_jobs(self, limit: int, offset: int) -> list[dict[str, Any]]:
        return self._blocked

    def blocked_jobs_ready(self, limit: int, offset: int) -> list[dict[str, Any]]:
        return []

    def answer_bank(self) -> Known:
        return Known()

    def unsettled(self, job_id: str, slug: str, views: list[dict[str, Any]], bank: Known) -> list[dict[str, Any]]:
        return views


class NeedsYouResponseTests(unittest.IsolatedAsyncioTestCase):
    home: ClassVar[tempfile.TemporaryDirectory[str]]
    environment: ClassVar[Any]
    app: ClassVar[FastAPI]
    token: ClassVar[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.home = tempfile.TemporaryDirectory(prefix="jp-needs-you-test-")
        cls.environment = patch.dict(
            os.environ, {"JOBPILOT_HOME": cls.home.name, "JOBPILOT_VAULT": cls.home.name + "/vault", "BACKEND_PORT": "4100"}
        )
        cls.environment.start()
        from jobpilot.config import get_settings

        get_settings.cache_clear()
        from jobpilot.config.runtime import API_TOKEN
        from jobpilot.main import app

        cls.app, cls.token = app, API_TOKEN

    @classmethod
    def tearDownClass(cls) -> None:
        cls.environment.stop()
        cls.home.cleanup()

    async def needs_you(self, blocked: list[dict[str, Any]]) -> tuple[int, bytes]:
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/needs-you",
            "raw_path": b"/api/v1/needs-you",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"127.0.0.1:4100"), (b"authorization", b"Bearer " + self.token.encode())],
            "server": ("127.0.0.1", 4100),
            "client": ("127.0.0.1", 60000),
        }
        messages: list[Message] = []
        sent, finished = False, asyncio.Event()

        async def receive() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await finished.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            messages.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                finished.set()

        with patch("jobpilot.controller.jobs.dashboard_service", FakeDashboard(blocked)):
            await asyncio.wait_for(self.app(scope, receive, send), timeout=5)
        start = next(m for m in messages if m["type"] == "http.response.start")
        return start["status"], b"".join(m.get("body", b"") for m in messages)

    async def test_blocked_job_exposes_context_for_each_question(self) -> None:
        status, body = await self.needs_you(
            [
                {
                    "id": "j_tw",
                    "company": "tensorwave",
                    "role": "Software Engineer",
                    "board": "Ashby",
                    "reason": "missing answers: 2",
                    "missing": ["#name", "#visa"],
                    "inventory": [NAME, SELECT, {"key": "#pw", "label": "Password", "type": "secret", "selector": "#pw"}],
                    "status": "preparing",
                }
            ]
        )
        self.assertEqual(status, 200)
        (job,) = json.loads(body)["blocked"]
        self.assertEqual((job["company"], job["role"], job["board"]), ("tensorwave", "Software Engineer", "Ashby"))
        name, team = job["missingFields"]
        self.assertEqual((name["label"], name["type"], name["required"], name["options"]), ("Name", "text", True, []))
        self.assertEqual((team["type"], team["required"], team["options"]), ("select", True, ["Platform", "Research"]))
        self.assertNotIn("inventory", job)
        self.assertNotIn(b"selector", body)
        self.assertNotIn(b"Password", body)

    async def test_job_past_preparation_never_carries_questions(self) -> None:
        for state in ("awaiting_approval", "submitted", "closed"):
            with self.subTest(status=state):
                status, body = await self.needs_you(
                    [
                        {
                            "id": "j_tmvqizck",
                            "company": "Acme",
                            "role": "Engineer",
                            "reason": "SUBMITTED AS REPORTED WITHOUT APPROVAL: check this application",
                            "missing": ["#pass"],
                            "inventory": [{"key": "#pass", "label": "Passphrase", "type": "text", "required": True}],
                            "status": state,
                        }
                    ]
                )
                self.assertEqual(status, 200)
                (job,) = json.loads(body)["blocked"]
                self.assertEqual((job["missing"], job["missingFields"]), ([], []))
                self.assertNotIn(b"Passphrase", body)

    async def test_custom_choice_response_carries_searchability_and_rejected_answer(self) -> None:
        field = {
            "key": "school",
            "label": "School",
            "type": "custom-select",
            "required": True,
            "searchable": True,
            "unmatchedValue": "Example State University",
            "suggestions": ["Al Azhar University", "Other"],
        }
        status, body = await self.needs_you(
            [
                {
                    "id": "j",
                    "company": "Co",
                    "role": "Intern",
                    "reason": None,
                    "missing": ["school"],
                    "inventory": [field],
                    "status": "preparing",
                }
            ]
        )
        self.assertEqual(status, 200)
        (view,) = json.loads(body)["blocked"][0]["missingFields"]
        self.assertTrue(view["searchable"])
        self.assertEqual(view["unmatchedValue"], "Example State University")
        self.assertEqual(view["options"], ["Al Azhar University", "Other"])

    async def test_legacy_job_without_inventory_or_board_still_serialises(self) -> None:
        status, body = await self.needs_you(
            [
                {
                    "id": "j_old",
                    "company": None,
                    "role": "Engineer",
                    "reason": "missing answers: 1",
                    "missing": ["#legacy"],
                    "inventory": None,
                    "status": "preparing",
                }
            ]
        )
        self.assertEqual(status, 200)
        (job,) = json.loads(body)["blocked"]
        self.assertIsNone(job["board"])
        self.assertEqual(job["missing"], [])
        self.assertEqual(job["missingFields"], [])

    async def test_legacy_field_without_required_serialises_as_null(self) -> None:
        status, body = await self.needs_you(
            [
                {
                    "id": "j_old",
                    "company": "Acme",
                    "role": "Engineer",
                    "board": None,
                    "reason": None,
                    "missing": ["#q"],
                    "inventory": [{"key": "#q", "label": "Anything else?", "type": "text"}],
                    "status": "preparing",
                }
            ]
        )
        self.assertEqual(status, 200)
        job = json.loads(body)["blocked"][0]
        self.assertEqual(job["missing"], [])
        self.assertEqual(job["missingFields"], [])

    async def test_question_batch_excludes_optional_fields(self) -> None:
        for required in ([], [NAME]):
            with self.subTest(required=bool(required)):
                optional = {"key": "optional", "label": "Nickname", "type": "text", "required": False}
                status, body = await self.needs_you(
                    [
                        {
                            "id": "j",
                            "company": "Acme",
                            "role": "R",
                            "status": "preparing",
                            "reason": None,
                            "missing": ["optional", *[f["key"] for f in required]],
                            "inventory": [optional, *required],
                        }
                    ]
                )
                self.assertEqual(status, 200)
                job = json.loads(body)["blocked"][0]
                self.assertEqual(job["missing"], [f["key"] for f in required])
                self.assertTrue(all(f["required"] for f in job["missingFields"]))
                self.assertNotIn("Nickname", body.decode())

    async def test_response_options_are_capped(self) -> None:
        huge: Field = {**SELECT, "options": [f"Team {n}" for n in range(1000)]}
        status, body = await self.needs_you(
            [
                {
                    "id": "j",
                    "company": "Acme",
                    "role": "R",
                    "reason": None,
                    "missing": ["#visa"],
                    "inventory": [huge],
                    "status": "preparing",
                }
            ]
        )
        self.assertEqual(status, 200)
        (field,) = json.loads(body)["blocked"][0]["missingFields"]
        self.assertEqual(len(field["options"]), MAX_OPTIONS)
        self.assertEqual(field["optionsTotal"], 1000)


if __name__ == "__main__":
    unittest.main()

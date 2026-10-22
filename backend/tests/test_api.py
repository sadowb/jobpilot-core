"""HTTP versioning and authentication regressions; no database or worker starts."""

import asyncio
import json
import os
import tempfile
import unittest
from typing import Any, ClassVar
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.types import Message, Scope

from jobpilot.repository.database import Database


class ApiTests(unittest.IsolatedAsyncioTestCase):
    home: ClassVar[tempfile.TemporaryDirectory[str]]
    environment: ClassVar[Any]
    app: ClassVar[FastAPI]
    token: ClassVar[str]
    db: ClassVar[Database]

    @classmethod
    def setUpClass(cls) -> None:
        cls.home = tempfile.TemporaryDirectory(prefix="jp-api-test-")
        cls.environment = patch.dict(
            os.environ, {"JOBPILOT_HOME": cls.home.name, "JOBPILOT_VAULT": cls.home.name + "/vault", "BACKEND_PORT": "4100"}
        )
        cls.environment.start()
        from jobpilot.config import get_settings

        get_settings.cache_clear()
        from jobpilot.config.runtime import API_TOKEN, db
        from jobpilot.main import app

        cls.app, cls.token, cls.db = app, API_TOKEN, db

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.environment.stop()
        cls.home.cleanup()

    async def test_automation_monitoring_contract_on_both_route_prefixes(self) -> None:
        usage = {"usd": 0.37, "tokens": 2_223_167, "jobs": 2, "runs": 3, "averageCostPerJob": 0.185}
        body = {
            "running": True,
            "concurrency": 4,
            "active": [],
            "started": 3,
            "finished": 3,
            "failed": 0,
            "workerOnline": True,
            "workersOnline": 1,
            "activeRuns": 0,
            "idleReason": None,
            "browsersOpen": 1,
            "browserLimit": 6,
            "capacity": 4,
            "queueOnline": True,
            "browserOnline": True,
            "monitoring": {"today": usage, "last7Days": usage, "allTime": usage, "queueDepth": 9, "recentFailures": []},
        }
        with patch("jobpilot.controller.automation.application_service.automation_status", return_value=body):
            for prefix in ("/api", "/api/v1"):
                status, _, raw = await self.request(f"{prefix}/automation", headers={"authorization": f"Bearer {self.token}"})
                self.assertEqual(status, 200)
                result = json.loads(raw)
                self.assertEqual(result["monitoring"]["today"]["tokens"], 2_223_167)
                self.assertEqual(result["monitoring"]["queueDepth"], 9)
                self.assertNotIn("budget", result)

    async def request(
        self, path: str, method: str = "GET", headers: dict[str, str] | None = None, body: object = None
    ) -> tuple[int, dict[bytes, bytes], bytes]:
        payload = b"" if body is None else json.dumps(body).encode()
        headers = {"host": "127.0.0.1:4100", **(headers or {})}
        if body is not None:
            headers["content-type"] = "application/json"
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
            "server": ("127.0.0.1", 4100),
            "client": ("127.0.0.1", 60000),
        }
        messages: list[Message] = []
        sent, finished = False, asyncio.Event()

        async def receive() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": payload, "more_body": False}
            await finished.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            messages.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                finished.set()

        await asyncio.wait_for(self.app(scope, receive, send), timeout=5)
        start = next(message for message in messages if message["type"] == "http.response.start")
        return start["status"], dict(start["headers"]), b"".join(message.get("body", b"") for message in messages)

    async def session(self) -> tuple[str, str]:
        status, headers, _ = await self.request("/docs", headers={"sec-fetch-site": "none", "sec-fetch-dest": "document"})
        self.assertEqual(status, 200)
        cookie = headers[b"set-cookie"].decode().split(";")[0]
        status, _, body = await self.request("/api/v1/session", headers={"cookie": cookie})
        self.assertEqual(status, 200)
        csrf = json.loads(body)["csrf"]
        if not isinstance(csrf, str):
            raise AssertionError("expected a session CSRF value")
        return cookie, csrf

    def test_retired_account_contracts_are_absent(self) -> None:
        schema = self.app.openapi()
        self.assertFalse(any("account" in path for path in schema["paths"]))
        self.assertFalse(any(name.startswith("Account") for name in schema["components"]["schemas"]))
        resume = schema["paths"]["/api/v1/jobs/{job_id}/resume"]["post"]
        self.assertIn("try_anyway", [parameter["name"] for parameter in resume["parameters"]])

    def test_legacy_routes_use_same_controllers_and_contracts(self) -> None:
        routes = [route for route in self.app.routes if isinstance(route, APIRoute)]
        versioned = {
            (route.path.removeprefix("/api/v1"), method): route
            for route in routes
            if route.path.startswith("/api/v1/")
            for method in (route.methods or set())
        }
        legacy = {
            (route.path.removeprefix("/api"), method): route
            for route in routes
            if route.path.startswith("/api/") and not route.path.startswith("/api/v1/")
            for method in (route.methods or set())
        }
        self.assertEqual(versioned.keys(), legacy.keys())
        for key, route in versioned.items():
            self.assertIs(route.endpoint, legacy[key].endpoint)
            self.assertEqual(route.response_model, legacy[key].response_model)
            self.assertFalse(legacy[key].include_in_schema)

    def test_schema_lists_only_versioned_feature_groups(self) -> None:
        schema = self.app.openapi()
        self.assertTrue(all(path.startswith("/api/v1/") for path in schema["paths"]))
        tags = {tag for operations in schema["paths"].values() for operation in operations.values() for tag in operation["tags"]}
        self.assertEqual(tags, {"Session", "Jobs", "Tracker", "CV tracks", "Profile", "Automation", "Capture", "Credentials"})

    async def test_event_stream_requires_owner_session_and_safe_origin(self) -> None:
        from jobpilot.config.runtime import live_events

        cookie, _ = await self.session()
        for prefix in ("/api", "/api/v1"):
            path = prefix + "/events"
            self.assertEqual((await self.request(path))[0], 401)
            self.assertEqual((await self.request(path, headers={"authorization": "Bearer " + self.token}))[0], 403)
            for extra in ({"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"}):
                self.assertEqual((await self.request(path, headers={"cookie": cookie, **extra}))[0], 403)
            with patch.object(live_events, "connected", False):
                status, headers, body = await self.request(path, headers={"cookie": cookie})
                self.assertEqual(status, 200)
                self.assertIn(b"text/event-stream", headers[b"content-type"])
                self.assertIn(b"retry: 5000", body)
                self.assertIn(b"live updates unavailable", body)
            with patch.object(live_events, "subscribe", side_effect=OverflowError):
                status, _, body = await self.request(path, headers={"cookie": cookie})
                self.assertEqual(status, 200)
                self.assertIn(b"retry: 5000", body)
                self.assertEqual(len(live_events._clients), 0)

    async def test_event_stream_sends_named_id_events_and_releases_subscription(self) -> None:
        from sse_starlette import ServerSentEvent

        from jobpilot.config.runtime import live_events

        async def finite_stream(_: Any) -> Any:
            yield ServerSentEvent(event="job", id="42", data='{"job":"j_abcdefgh","kind":"run_requeued"}')

        cookie, _ = await self.session()
        for prefix in ("/api", "/api/v1"):
            with patch.object(live_events, "connected", True), patch.object(live_events, "stream", finite_stream):
                status, headers, body = await self.request(
                    prefix + "/events", headers={"cookie": cookie, "last-event-id": "41", "accept-encoding": "gzip"}
                )
            self.assertEqual(status, 200)
            self.assertIn(b"text/event-stream", headers[b"content-type"])
            self.assertEqual(headers[b"cache-control"], b"no-store")
            self.assertEqual(headers[b"x-accel-buffering"], b"no")
            self.assertNotIn(b"content-encoding", headers)
            self.assertIn(b"id: 42", body)
            self.assertIn(b"event: job", body)
            self.assertEqual(len(live_events._clients), 0)

    async def test_anonymous_reads_and_schema_are_rejected(self) -> None:
        for path in ("/api/jobs", "/api/v1/jobs", "/openapi.json", "/docs"):
            with self.subTest(path=path):
                self.assertEqual((await self.request(path))[0], 401)

    async def test_capture_needs_its_own_token_and_only_adds_a_link(self) -> None:
        from jobpilot.controller import capture

        link = {"url": "https://boards.example/jobs/42", "title": "Engineer"}
        for headers in ({}, {"authorization": "Bearer wrong"}, {"authorization": "Bearer " + self.token}):
            with self.subTest(headers=headers):  # no token, a wrong one, and the full-API bearer are all refused
                self.assertEqual((await self.request("/api/v1/capture", "POST", headers, link))[0], 401)
        good = {"authorization": "Bearer " + capture.CAPTURE_TOKEN}
        ingested = {"id": "j_x", "created": True, "board": "generic", "automation": "assisted"}
        with patch.object(capture.application_service, "ingest", return_value=ingested) as ingest:
            status, _, body = await self.request("/api/v1/capture", "POST", good, link)
        self.assertEqual((status, json.loads(body)["id"]), (200, "j_x"))
        ingest.assert_called_once_with("https://boards.example/jobs/42")
        for bad in ({"url": "javascript:alert(1)"}, {"url": "file:///etc/passwd"}, {"url": "not a url"}, {}):
            with self.subTest(bad=bad):
                self.assertEqual((await self.request("/api/v1/capture", "POST", good, bad))[0], 422)

    async def test_capture_is_rate_limited(self) -> None:
        from jobpilot.controller import capture

        good = {"authorization": "Bearer " + capture.CAPTURE_TOKEN}
        ingested = {"id": "j_x", "created": False, "board": "generic", "automation": "assisted"}
        capture._stamps.clear()
        with patch.object(capture, "_RATE", 2), patch.object(capture.application_service, "ingest", return_value=ingested):
            codes = [(await self.request("/api/v1/capture", "POST", good, {"url": "https://a.example/1"}))[0] for _ in range(3)]
        capture._stamps.clear()
        self.assertEqual(codes, [200, 200, 429])

    async def test_pairing_page_is_only_for_the_owners_own_navigation(self) -> None:
        from jobpilot.controller import capture

        own = {"sec-fetch-dest": "document", "sec-fetch-site": "none"}
        status, _, body = await self.request("/api/v1/capture/pair", headers=own)
        self.assertEqual(status, 200)
        self.assertIn(capture.CAPTURE_TOKEN.encode(), body)
        for headers in (
            {},  # a script or curl
            {"sec-fetch-dest": "empty", "sec-fetch-site": "same-origin"},  # fetch()
            {"sec-fetch-dest": "document", "sec-fetch-site": "cross-site"},  # another website linking here
            {"sec-fetch-dest": "iframe", "sec-fetch-site": "same-origin"},
        ):
            with self.subTest(headers=headers):
                status, _, body = await self.request("/api/v1/capture/pair", headers=headers)
                self.assertEqual(status, 403)
                self.assertNotIn(capture.CAPTURE_TOKEN.encode(), body)

    def test_capture_token_file_is_private(self) -> None:
        import stat

        from jobpilot.config.runtime import cfg

        path = cfg.data_dir / ".capture-token"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertGreaterEqual(len(path.read_text().strip()), 32)

    async def test_bearer_cannot_record_an_owner_submission(self) -> None:
        for prefix in ("/api", "/api/v1"):
            result = await self.request(
                prefix + "/jobs/j_abc123/owner-submitted",
                "POST",
                {"authorization": "Bearer " + self.token},
                {},
            )
            self.assertEqual(result[0], 403)

    async def test_guidance_and_manual_submission_require_an_owner_session(self) -> None:
        for suffix, body in (("guidance", {"message": "Continue"}), ("user-confirmed", {}), ("owner-submitted", {})):
            for prefix in ("/api", "/api/v1"):
                path = prefix + "/jobs/synthetic-job/" + suffix
                self.assertEqual((await self.request(path, "POST", body=body))[0], 401)
                result = await self.request(path, "POST", {"authorization": "Bearer " + self.token}, body)
                self.assertEqual(result[0], 403)

    async def test_refresh_details_routes_require_an_owner_session(self) -> None:
        for suffix in ("/jobs/synthetic-job/refresh-details", "/jobs/refresh-missing-details"):
            for prefix in ("/api", "/api/v1"):
                path = prefix + suffix
                self.assertEqual((await self.request(path, "POST", body={}))[0], 401)
                result = await self.request(path, "POST", {"authorization": "Bearer " + self.token}, {})
                self.assertEqual(result[0], 403)

    async def test_docs_bootstrap_a_session_and_keep_scripts_local(self) -> None:
        cookie, _ = await self.session()
        status, headers, body = await self.request("/docs", headers={"cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn(b"/docs/assets/swagger-ui-bundle.js", body)
        self.assertNotIn(b"<script>", body)
        self.assertIn(b"script-src 'self'", headers[b"content-security-policy"])
        self.assertEqual((await self.request("/openapi.json", headers={"cookie": cookie}))[0], 200)

    async def test_csrf_and_validation_apply_to_both_api_versions(self) -> None:
        cookie, csrf = await self.session()
        for prefix in ("/api", "/api/v1"):
            valid = {"cookie": cookie, "origin": "http://127.0.0.1:4100", "x-csrf-token": csrf}
            invalid = {**valid, "x-csrf-token": "incorrect"}
            self.assertEqual(
                (await self.request(prefix + "/profile", "POST", invalid, {"key": "fixture", "value": "fixture"}))[0], 403
            )
            self.assertEqual(
                (await self.request(prefix + "/profile/sections/education", "PUT", valid, {"entries": "invalid"}))[0], 422
            )

    async def test_submit_evidence_screenshot_is_served_only_from_the_jobs_own_record(self) -> None:
        from pathlib import Path

        from jobpilot.config.runtime import application_service, cfg

        path = "/api/v1/jobs/j_abc123/submit-evidence/screenshot"
        self.assertEqual((await self.request(path))[0], 401)
        cookie, _ = await self.session()
        directory = Path(cfg.data_dir) / "evidence" / "j_abc123"
        directory.mkdir(parents=True, exist_ok=True)
        name = "20260101T000000Z-abcd1234.png"
        (directory / name).write_bytes(b"\x89PNG-fixture")
        (directory.parent / "secret.png").write_bytes(b"not for this job")
        for stored, expected in (
            ({"screenshot": name}, 200),
            ({"screenshot": "../secret.png"}, 404),  # a stored name outside the fixed pattern is never opened
            ({"screenshot": None}, 404),
            (None, 404),
        ):
            with (
                self.subTest(stored=stored),
                patch.object(
                    application_service, "form", return_value={"submit_evidence": stored} if stored is not None else None
                ),
            ):
                status, headers, body = await self.request(path, headers={"cookie": cookie})
                self.assertEqual(status, expected)
                if expected == 200:
                    self.assertEqual(body, b"\x89PNG-fixture")
                    self.assertEqual(headers[b"content-type"], b"image/png")
                    self.assertEqual(headers[b"x-content-type-options"], b"nosniff")
                    self.assertIn(b"no-store", headers[b"cache-control"])
        with patch.object(application_service, "form", return_value={"submit_evidence": {"screenshot": name}}):
            status, _, _ = await self.request("/api/v1/jobs/..%2F..%2Fx/submit-evidence/screenshot", headers={"cookie": cookie})
            self.assertEqual(status, 404)

    async def test_tab_focus_is_a_user_session_action_with_both_outcomes(self) -> None:
        from jobpilot.config.runtime import application_service
        from jobpilot.controller import jobs as controller

        path = "/api/v1/jobs/j_abc123/tab/focus"
        self.assertEqual((await self.request(path, "POST"))[0], 401)
        self.assertEqual((await self.request(path, "POST", {"authorization": "Bearer " + self.token}))[0], 403)
        cookie, csrf = await self.session()
        valid = {"cookie": cookie, "origin": "http://127.0.0.1:4100", "x-csrf-token": csrf}
        self.assertEqual((await self.request(path, "POST", {**valid, "x-csrf-token": "incorrect"}))[0], 403)
        with patch.object(application_service, "job", return_value={"id": "j_abc123"}):
            for result, focused in (
                ({"focused": False, "reason": "unsupported", "page": "7", "group": "jobpilot/job-applications"}, False),
                ({"focused": True, "reason": "focused", "page": "7", "title": "Apply", "color": "yellow"}, True),
                ({"focused": False, "reason": "no_tab", "page": None}, False),
            ):
                with self.subTest(result=result), patch.object(controller.tabs, "focus", return_value=result) as focus:
                    status, _, body = await self.request(path, "POST", valid)
                    self.assertEqual(status, 200)
                    self.assertEqual(
                        {k: v for k, v in json.loads(body).items() if v is not None},
                        {k: v for k, v in result.items() if v is not None},
                    )
                    self.assertIs(json.loads(body)["focused"], focused)
                    focus.assert_called_once_with("j_abc123")
        with patch.object(application_service, "job", side_effect=LookupError("unknown job")):
            self.assertEqual((await self.request(path, "POST", valid))[0], 404)

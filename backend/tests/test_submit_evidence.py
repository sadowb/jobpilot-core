"""Server-side submit evidence: bounded, secret-filtered, owner-only files; no browser or database starts."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from jobpilot.browser.neo import BrowserUnavailable, PageCapture
from jobpilot.config import Settings
from jobpilot.service import Services
from jobpilot.service.evidence import (
    EXCERPT_CHARS,
    KEEP_SCREENSHOTS,
    SubmitEvidenceService,
    evidence_dir,
    evidence_view,
    safe_text,
    safe_url,
    screenshot_path,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class SanitisingTests(unittest.TestCase):
    def test_urls_lose_query_fragment_and_credentials(self) -> None:
        self.assertEqual(
            safe_url("https://user:pw@job-boards.greenhouse.io/acme/jobs/1/confirmation?token=abc&code=123#x"),
            "https://job-boards.greenhouse.io/acme/jobs/1/confirmation",
        )
        self.assertEqual(safe_url("javascript:alert(1)"), "")
        self.assertEqual(safe_url("not a url"), "")

    def test_text_is_one_bounded_printable_line_without_secret_values(self) -> None:
        text = safe_text("Thank\x00 you\n\nfor applying. Your verification code: 481516 token=abc", 200)
        self.assertEqual(text, "Thank you for applying. Your [withheld] [withheld]")
        self.assertEqual(len(safe_text("x" * 5000, EXCERPT_CHARS)), EXCERPT_CHARS)

    def test_view_hides_the_file_name_and_flags_a_valid_screenshot(self) -> None:
        stored = {
            "capturedAt": "t",
            "url": "u",
            "title": "T",
            "excerpt": "e",
            "textSha": "s",
            "screenshot": "20260101T000000Z-abcd1234.png",
        }
        view = evidence_view(stored)
        self.assertEqual(
            view, {"capturedAt": "t", "url": "u", "title": "T", "excerpt": "e", "confirmation": None, "hasScreenshot": True}
        )
        self.assertFalse(evidence_view({**stored, "screenshot": "../../etc/passwd"})["hasScreenshot"])  # type: ignore[index]
        self.assertIsNone(evidence_view(None))

    def test_screenshot_lookup_rejects_traversal_and_foreign_names(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            data = Path(raw)
            directory = evidence_dir(data, "j_abc123")
            directory.mkdir(parents=True)
            good = directory / "20260101T000000Z-abcd1234.png"
            good.write_bytes(PNG)
            self.assertEqual(screenshot_path(data, "j_abc123", good.name), good.resolve())
            for name in ("../20260101T000000Z-abcd1234.png", "x.png", "20260101T000000Z-abcd1234.png/../x", ""):
                self.assertIsNone(screenshot_path(data, "j_abc123", name))
            self.assertIsNone(screenshot_path(data, "j_abc123", "20260101T000000Z-ffffffff.png"))  # not on disk
            with self.assertRaises(ValueError):
                evidence_dir(data, "../etc")


class CaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Settings(_env_file=None, jobpilot_home=Path(self.tmp.name))
        self.svc: Any = MagicMock(spec=Services)
        self.svc.cfg, self.svc.node = self.cfg, "mac"
        self.svc.db = MagicMock()
        self.svc.db.transaction.side_effect = lambda: nullcontext()
        self.service = SubmitEvidenceService(self.svc)
        self.chrome: Any = MagicMock()
        self.chrome.capture.return_value = PageCapture(
            "https://job-boards.greenhouse.io/acme/jobs/1?token=zzz", "Thank you", "Thank you for applying.", PNG
        )
        patches = [
            patch("jobpilot.service.evidence.neo_browser", return_value=self.chrome),
            patch("jobpilot.service.evidence.BrowserRepository"),
        ]
        tabs = patches[1].start()
        tabs.return_value.find_session_tab.return_value = {"target_id": "tab-1"}
        patches[0].start()
        for item in patches:
            self.addCleanup(item.stop)

    def events(self) -> list[tuple[Any, ...]]:
        return [call.args for call in self.svc.event.call_args_list]

    def test_records_the_tab_state_in_the_form_row_and_an_event_without_the_text(self) -> None:
        evidence = self.service.capture("j_abc123")
        assert evidence is not None
        self.chrome.capture.assert_called_once_with("tab-1", EXCERPT_CHARS)
        self.assertEqual(evidence["url"], "https://job-boards.greenhouse.io/acme/jobs/1")
        self.assertEqual(evidence["excerpt"], "Thank you for applying.")
        kind, actor, payload = self.events()[0][1:]
        self.assertEqual((kind, actor), ("submit_evidence", "system"))
        self.assertEqual(set(payload), {"url", "title", "textSha", "screenshot"})  # never the excerpt
        update = self.svc.db.run.call_args.args
        self.assertIn("UPDATE job_forms SET submit_evidence", update[0])
        self.assertEqual(update[1][1], "j_abc123")

    def test_screenshot_is_an_owner_only_file_in_an_owner_only_directory(self) -> None:
        evidence = self.service.capture("j_abc123")
        assert evidence is not None
        directory = evidence_dir(self.cfg.data_dir, "j_abc123")
        path = directory / evidence["screenshot"]
        self.assertEqual(path.read_bytes(), PNG)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)

    def test_only_the_latest_screenshots_are_kept(self) -> None:
        directory = evidence_dir(self.cfg.data_dir, "j_abc123")
        directory.mkdir(parents=True, mode=0o700)
        for index in range(KEEP_SCREENSHOTS + 3):
            (directory / f"2026010{index}T000000Z-aaaaaaaa.png").write_bytes(PNG)
        self.service.capture("j_abc123")
        kept = sorted(os.listdir(directory))
        self.assertEqual(len(kept), KEEP_SCREENSHOTS)
        self.assertNotIn("20260100T000000Z-aaaaaaaa.png", kept)

    def test_a_page_without_a_screenshot_still_records_text(self) -> None:
        self.chrome.capture.return_value = PageCapture("https://x.test/a", "T", "Body", None)
        evidence = self.service.capture("j_abc123")
        assert evidence is not None
        self.assertIsNone(evidence["screenshot"])
        self.assertFalse(evidence_dir(self.cfg.data_dir, "j_abc123").exists())

    def test_missing_tab_or_unreachable_chrome_returns_none_and_never_raises(self) -> None:
        self.chrome.capture.side_effect = BrowserUnavailable("down")
        self.assertIsNone(self.service.capture("j_abc123"))
        self.assertEqual(self.events()[-1][1], "submit_evidence_unavailable")
        self.svc.event.reset_mock()
        with patch("jobpilot.service.evidence.BrowserRepository") as tabs:
            tabs.return_value.find_session_tab.return_value = None
            self.assertIsNone(self.service.capture("j_abc123"))
        self.assertEqual(self.events()[-1][1:], ("submit_evidence_unavailable", "system", {"reason": "tab gone"}))


class VerifyTests(CaptureTests):
    """The read-only look at the parked tab after the owner says they submitted (ADR 0026)."""

    def setUp(self) -> None:
        super().setUp()
        self.svc.job.return_value = {"board_id": "custom"}
        self.chrome.page_text.return_value = "Application form"
        self.sleeps: list[float] = []

    def verify(self, **kwargs: Any) -> bool:
        return self.service.verify("j_abc123", sleep=self.sleeps.append, **kwargs)

    def test_a_confirmation_page_is_seen_and_stored_with_the_verdict(self) -> None:
        self.chrome.page_text.return_value = "Thank you for applying. We have received your application."
        self.assertTrue(self.verify())
        stored = self.svc.db.run.call_args.args[1][0]
        self.assertIn("'confirmation': 'seen'", str(stored.obj if hasattr(stored, "obj") else stored))

    def test_no_confirmation_after_every_poll_is_not_seen_and_still_stored(self) -> None:
        self.assertFalse(self.verify(polls=3))
        self.assertEqual(self.chrome.page_text.call_count, 3)
        self.assertEqual(len(self.sleeps), 2)
        stored = self.svc.db.run.call_args.args[1][0]
        self.assertIn("'confirmation': 'not_seen'", str(stored.obj if hasattr(stored, "obj") else stored))

    def test_stops_polling_at_the_first_confirmation(self) -> None:
        self.chrome.page_text.side_effect = ["form", "Thank you for applying.", "unused"]
        self.assertTrue(self.verify(polls=3))
        self.assertEqual(self.chrome.page_text.call_count, 2)

    def test_only_reads_the_tab_never_clicks_or_navigates(self) -> None:
        self.verify()
        called = {name for name, *_ in self.chrome.method_calls}
        self.assertLessEqual(called, {"page_text", "capture"})

    def test_an_unreachable_neo_or_gone_tab_is_not_seen_and_never_raises(self) -> None:
        self.chrome.page_text.side_effect = BrowserUnavailable("down")
        self.chrome.capture.side_effect = BrowserUnavailable("down")
        with self.assertLogs("jobpilot", "WARNING"):
            self.assertFalse(self.verify())

    def test_the_view_exposes_the_verdict_only_when_valid(self) -> None:
        base = {"capturedAt": "t", "url": "u", "title": "T", "excerpt": "e"}
        assert evidence_view({**base, "confirmation": "seen"})["confirmation"] == "seen"  # type: ignore[index]
        assert evidence_view({**base, "confirmation": "<script>"})["confirmation"] is None  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()

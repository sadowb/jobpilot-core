"""Exercise the measurement harness with fake public/model responses; no network, CLI, DB or browser."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobpilot.domain.posting_form import PostingForm, PostingMeta, PostingQuestion
from jobpilot.llm import LlmResult

SPEC = importlib.util.spec_from_file_location("measure_prep", Path(__file__).resolve().parents[1] / "scripts/measure_prep.py")
assert SPEC and SPEC.loader
measurement = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = measurement
SPEC.loader.exec_module(measurement)

URL = "https://boards.greenhouse.io/acme/jobs/123"


class MeasureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "input.pdf").write_bytes(b"fake PDF; extraction is mocked")
        context = measurement.Context.model_validate(
            {
                "profile": {"first_name": "Ada"},
                "cvs": [
                    {"track_id": "swe", "title": "SWE", "market": "us", "pdf": "input.pdf"},
                ],
            }
        )
        self.svc = measurement.MemoryServices(URL, context, self.root, self.root / "scratch")
        self.form = PostingForm(
            "greenhouse",
            PostingMeta(role="Engineer", company="Acme", location="New York"),
            (
                PostingQuestion("q:first", "First name", "text", True),
                PostingQuestion("q:r", "Why us?", "textarea", True),
                PostingQuestion("q:o", "Nickname", "text", False),
            ),
        )
        for target in ("jobpilot.repository.database.Database.__init__", "jobpilot.browser.neo.NeoBrowser.__init__"):
            # No real persistence or browser may be constructed, even accidentally.
            p = patch(target, side_effect=AssertionError("external access forbidden"))
            p.start()
            self.addCleanup(p.stop)

    def run_measure(self, form: PostingForm | None, raw: str | None = None) -> tuple[dict, object]:
        raw = raw or json.dumps(
            {"cv_choice": {"track_id": "swe", "job_market": "us", "summary": "Fits", "evidence": []}, "answers": []}
        )
        with (
            patch("jobpilot.service.posting_prefetch.fetch_posting_form", return_value=form),
            patch(
                "jobpilot.service.cv_selection.pdf_text", return_value="Built Python APIs and maintained backend services. " * 5
            ),
            patch.object(measurement, "compare_text", return_value=LlmResult(raw, 123, 45, 0.01)) as model,
        ):
            result = measurement.measure(self.svc)
        return result, model

    def test_real_preflight_reports_usage_cv_and_question_groups(self) -> None:
        result, model = self.run_measure(self.form)
        self.assertTrue(result["success"])
        self.assertGreaterEqual(result["seconds_to_questions_ready"], 0)
        self.assertEqual((result["model_calls"], result["input_tokens"], result["output_tokens"]), (1, 123, 45))
        self.assertEqual(result["cv_chosen"], "swe")
        self.assertEqual(result["required_open"], ["Why us?"])
        self.assertEqual(result["optional_open"], ["Nickname"])
        self.assertEqual(self.svc.form["missing"], ["page:0:q:r"])
        model.assert_called_once()

    def test_optional_only_preflight_stays_with_agent(self) -> None:
        result, _ = self.run_measure(PostingForm("greenhouse", self.form.meta, (self.form.questions[-1],)))
        self.assertTrue(result["success"])
        self.assertEqual(result["required_open"], [])
        self.assertEqual(result["optional_open"], ["Nickname"])
        self.assertEqual(self.svc.row["waiting_on"], "agent")

    def test_fetch_failure_reports_no_ready_list_and_never_calls_model(self) -> None:
        result, model = self.run_measure(None)
        self.assertFalse(result["success"])
        self.assertIsNone(result["seconds_to_questions_ready"])
        self.assertIsNone(result["required_open"])
        self.assertEqual(result["model_calls"], 0)
        model.assert_not_called()

    def test_invalid_cv_choice_reports_failure_without_retry(self) -> None:
        result, model = self.run_measure(self.form, '{"cv_choice":{"track_id":"unknown"},"answers":[]}')
        self.assertFalse(result["success"])
        self.assertEqual(result["failure"], "ValueError")
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(result["input_tokens"], 123)
        model.assert_called_once()

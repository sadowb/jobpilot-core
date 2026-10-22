"""Evidence-based selection: actual CV contents, invalid choices, and tool isolation."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from jobpilot.config import Settings
from jobpilot.llm import LlmResult
from jobpilot.repository.direct import DirectRepository
from jobpilot.service.cv_selection import SYSTEM, CvSelectionService
from jobpilot.worker.claude import AgentInterrupted, ClaudeRunError
from jobpilot.worker.text import compare_text

POSTING = "We seek a backend engineer to design transactional services, test APIs and improve reliability in production. This is a United States position."
CVS = {
    "general": "Candidate experience: Built transactional services and tested API failures under concurrent load. Maintained production reliability and mentored engineers.",
    "keyword-heavy": "Candidate experience: Backend Engineer Python SQL API Docker are topics studied in coursework. Organized a campus club and prepared presentations for new members.",
}


def decision(**changes: Any) -> dict[str, Any]:
    return {
        "track_id": "general",
        "job_market": "us",
        "summary": "The services project provides relevant production evidence; the other CV mainly shows coursework.",
        "evidence": [
            {
                "requirement": "Transactional services",
                "excerpt": "Built transactional services and tested API failures under concurrent load.",
            }
        ],
        **changes,
    }


class SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc: Any = MagicMock()
        self.svc.cfg = Settings(_env_file=None)
        self.svc.job.return_value = {"role": "Engineer", "location": None}
        self.svc.store.object_dir.side_effect = lambda sha: Path("/cv") / sha
        self.service = CvSelectionService(self.svc)
        self.service.repository = MagicMock()
        self.service.repository.selection_candidates.return_value = [
            {"id": key, "title": key, "sha256": key, "version_id": index, "market": "us"} for index, key in enumerate(CVS, 1)
        ]
        self.check = MagicMock()
        self.started = MagicMock()
        reader = patch("jobpilot.service.cv_selection.pdf_text", side_effect=lambda path: CVS[path.parent.name])
        completion = patch("jobpilot.service.cv_selection.compare_text")
        reader.start()
        self.completion = completion.start()
        self.addCleanup(reader.stop)
        self.addCleanup(completion.stop)
        self.completion.return_value = LlmResult(json.dumps(decision()), 100, 20, 0.01)

    def choose(self) -> Any:
        return self.service.choose("j", POSTING, self.check, self.started)

    def test_model_compares_full_text_and_can_choose_against_title_keywords(self) -> None:
        choice, version = self.choose()
        self.assertEqual((choice.track_id, version), ("general", 1))
        prompt = json.loads(self.completion.call_args.args[2])
        self.assertEqual([item["cv_text"] for item in prompt["candidates"]], list(CVS.values()))
        self.assertNotIn("keywords", prompt["candidates"][0])
        self.started.assert_called_once()
        self.svc.cost.assert_called_once_with("j", self.svc.cfg.agent_model_cv, 100, 20, "cv_select", 0.01)

    def test_unknown_track_gets_one_correction_then_no_choice(self) -> None:
        self.completion.return_value.text = json.dumps(decision(track_id="arbitrary"))
        self.assertIsNone(self.choose())
        self.assertEqual(self.completion.call_count, 2)  # one retry with a fixed correction note
        self.assertIn("track_id must be exactly one of", json.loads(self.completion.call_args.args[2])["correction"])
        self.assertIn("no usable answer", self.service.decline_reason)

    def test_a_bad_first_answer_is_corrected_by_the_second(self) -> None:
        self.completion.side_effect = [
            LlmResult(json.dumps(decision(track_id="arbitrary")), 10, 5, 0.01),
            LlmResult(json.dumps(decision()), 10, 5, 0.01),
        ]
        choice, version = self.choose()
        self.assertEqual((choice.track_id, version), ("general", 1))

    def test_invented_quotes_and_missing_evidence_never_veto_a_choice(self) -> None:
        for changes in (
            {"evidence": []},
            {"evidence": [{"requirement": "Engineering", "excerpt": "Led production engineering for ten years"}]},
        ):
            with self.subTest(changes=changes):
                self.completion.return_value = LlmResult(json.dumps(decision(**changes)), 10, 5, 0.01)
                choice, version = self.choose()
                self.assertEqual((choice.track_id, version, len(choice.evidence)), ("general", 1, 0))

    def test_sloppy_but_decidable_answers_are_accepted(self) -> None:
        raw = json.dumps({**decision(), "confidence": 0.9, "summary": "x" * 900})
        self.completion.return_value = LlmResult("```json\n" + raw + "\n```", 10, 5, 0.01)
        choice, _ = self.choose()
        self.assertEqual(choice.track_id, "general")
        self.assertEqual(len(choice.summary), 600)

    def test_the_model_cannot_decline_and_no_default_cv_is_ever_substituted(self) -> None:
        self.assertIn("MUST return exactly one candidate track_id", SYSTEM)
        self.completion.return_value.text = json.dumps(decision(track_id=None, evidence=[]))
        self.assertIsNone(self.choose())
        self.assertEqual(self.completion.call_count, 2)
        self.svc.suggest_track.assert_not_called()

    def test_decline_reasons_are_recorded_for_each_way_a_choice_can_fail(self) -> None:
        self.assertIsNone(self.service.choose("j", "too short", self.check, self.started))
        self.assertIn("9 characters", self.service.decline_reason)
        self.completion.return_value.text = json.dumps(decision(track_id=None, evidence=[]))
        self.assertIsNone(self.choose())
        self.assertIn("no usable answer", self.service.decline_reason)
        self.service.repository.selection_candidates.return_value = []
        self.assertIsNone(self.choose())
        self.assertIn("no readable CV text", self.service.decline_reason)

    def test_geographic_market_must_match_the_job(self) -> None:
        self.completion.return_value.text = json.dumps(decision(job_market="morocco"))  # a US CV for a Morocco job
        self.assertIsNone(self.choose())
        self.assertIn("market", json.loads(self.completion.call_args.args[2])["correction"])
        self.completion.return_value.text = json.dumps(decision(job_market=None))  # location unknown, none claimed
        self.assertEqual(self.choose()[0].track_id, "general")

    def test_no_candidates_or_excessive_candidates_does_not_start_a_model(self) -> None:
        for rows in ([], self.service.repository.selection_candidates.return_value * 13):
            self.service.repository.selection_candidates.return_value = rows
            self.assertIsNone(self.choose())
        self.completion.assert_not_called()
        self.started.assert_not_called()

    def test_secret_shaped_lines_are_removed_from_inputs_and_refused_in_results(self) -> None:
        with patch.dict(CVS, {"general": CVS["general"] + "\nAuthorization: Bearer " + "x" * 40}):
            self.choose()
        self.assertNotIn("Bearer", self.completion.call_args.args[2])
        self.completion.return_value.text = json.dumps(decision(summary="Authorization: Bearer " + "x" * 40))
        self.assertIsNone(self.choose())

    def test_stop_interrupts_selection_without_storing_a_choice(self) -> None:
        self.check.side_effect = AgentInterrupted("automation_stopped")
        with self.assertRaises(AgentInterrupted):
            self.choose()
        self.completion.assert_not_called()
        self.svc.event.assert_not_called()

    def test_repository_assignment_checks_version_owner_state_and_submit_fence(self) -> None:
        db = MagicMock()
        DirectRepository(db).assign_reasoned_track("general", 1, "j")
        sql, params = db.one.call_args.args
        self.assertEqual(params, ("j", "general", 1))
        for guard in ("v.id=%s", "t.archived_at IS NULL", "j.cv_track_id IS NULL", "j.cv_version_id IS NULL", "submit_clicked"):
            self.assertIn(guard, sql)


class TextDriverTests(unittest.TestCase):
    def test_completion_is_monitored_tool_free_and_discards_raw_failure_details(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = Settings(_env_file=None, jobpilot_home=Path(directory))
            with patch("jobpilot.worker.text.run_stream") as stream:
                stream.return_value = subprocess.CompletedProcess([], 0, json.dumps({"result": "{}"}), "")
                check = MagicMock()
                self.assertEqual(compare_text(cfg, "j", "DATA", "SYSTEM", check).text, "{}")
                args = stream.call_args.args[0]
                self.assertEqual(args[args.index("--tools") + 1], "")
                self.assertEqual(args[args.index("--mcp-config") + 1], '{"mcpServers":{}}')
                self.assertIs(stream.call_args.kwargs["check"], check)
                stream.return_value = subprocess.CompletedProcess(
                    [], 1, '{"is_error":true,"result":"private content"}', "private stderr"
                )
                with self.assertRaises(ClaudeRunError) as error:
                    compare_text(cfg, "j", "DATA", "SYSTEM", check)
                self.assertNotIn("private", str(error.exception))

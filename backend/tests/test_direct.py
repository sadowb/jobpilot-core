"""Direct contracts with a fake driver and service state; no Chrome, DB or network."""

from __future__ import annotations

import json
import subprocess
import unittest
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from jobpilot.browser.neo import BrowserError, BrowserUnavailable, PageState
from jobpilot.browser.tabs import BrowserOffline
from jobpilot.config import RuntimeSettings, Settings
from jobpilot.domain.answers import Known
from jobpilot.domain.forms import page_fields
from jobpilot.domain.posting_form import PostingForm, PostingMeta, PostingQuestion
from jobpilot.domain.state_machine import GateContext, can_transition
from jobpilot.dto.direct import ResultInvalid, data_block, parse_result
from jobpilot.repository.approvals import ApprovalsRepository
from jobpilot.repository.jobs import JobsRepository
from jobpilot.service import Services
from jobpilot.service.approvals import ApprovalsService
from jobpilot.service.direct import DirectService
from jobpilot.service.submit_mode import ModeDecision
from jobpilot.worker.claude import AgentInterrupted, ClaudeDriver, ClaudeRunError
from jobpilot.worker.runner import JobRunner, RunEnd

ROOT = Path(__file__).resolve().parents[2]


def report(status: str = "filled", **changes: Any) -> str:
    data: dict[str, Any] = {
        "status": status,
        "role": "Engineer",
        "company": "Company",
        "fields": [{"label": "Name", "kind": "text", "required": True, "value": "Owner"}],
        "questions": [],
        "note": None,
        "confirmation": None,
    }
    if status == "needs_answers":
        data["questions"] = [{"label": "Available?", "kind": "select", "required": True, "options": ["Yes", "No"]}]
    if status == "submitted":
        data["confirmation"] = "Thank you for applying"
    return json.dumps({**data, **changes})


class ResultTests(unittest.TestCase):
    def test_invalid_missing_coercions_unknown_keys_and_limits_fail_without_echo(self) -> None:
        invalid = [
            None,
            "",
            "private page text",
            "{}",
            report(extra="secret"),
            report().replace('{"status": "filled",', '{"status":"submitted","status":"filled",'),
            "x" * 131073,
        ]
        invalid += [report(fields=[{"label": "Name", "kind": "text", "required": "true", "value": "Owner"}])]
        for raw in invalid:
            with self.subTest(raw=str(raw)[:20]), self.assertRaises(ValueError) as error:
                parse_result(raw)
            self.assertNotIn("private page text", str(error.exception))

    def test_email_verification_reports_metadata_only(self) -> None:
        result = parse_result(report(verification={"used": True, "source": "email"}))
        self.assertTrue(result.verification and result.verification.used)
        for value in (
            {"used": True, "source": "sms"},
            {"used": "true", "source": "email"},
            {"used": 1, "source": "email"},
            {"used": True, "source": "email", "code": "123456"},
        ):
            with self.assertRaises(ResultInvalid):
                parse_result(report(verification=value))
        for note in (
            "verification code is 123456",
            "OTP: 123456",
            "code: 123456",
            "123456",
            "I entered 123456 in the form",
            "password is hunter2",
        ):
            with self.subTest(note=note), self.assertRaises(ResultInvalid):
                parse_result(report(note=note))
        parse_result(report("blocked", note="No fresh email code found; please check the application."))

    def test_one_object_wrapped_in_short_prose_is_recovered(self) -> None:
        for raw in (
            "All fields are filled and read back.\n\n" + report(),
            "```json\n" + report() + "\n```",
            report() + "\n\nStopped before Submit.",
        ):
            with self.subTest(raw=raw[:24]):
                self.assertEqual(parse_result(raw).status, "filled")

    def test_ambiguous_or_invalid_wrapped_results_still_fail(self) -> None:
        cases = {
            "invalid_json": [
                report() + "\n" + report(),  # two top-level objects: which one is the result?
                "x" * 2_100 + report(),  # more prose than a final note
                'prose {"not": "closed',
            ],
            "?:extra_forbidden": ["Done.\n" + report(extra="secret")],
            "duplicate_key": [report().replace('{"status": "filled",', '{"status":"submitted","status":"filled",')],
        }
        for expected, raws in cases.items():
            for raw in raws:
                with self.subTest(expected=expected, raw=raw[:24]), self.assertRaises(ResultInvalid) as caught:
                    parse_result(raw)
                self.assertEqual(caught.exception.detail, expected)

    def test_incomplete_and_secret_results_are_rejected(self) -> None:
        for raw in (
            report(fields=[]),
            report(fields=[{"label": "Password", "kind": "text", "required": True, "value": "secret"}]),
            report("submitted", confirmation=None),
            report("needs_answers", questions=[]),
            report(fields=[{"label": "Choice", "kind": "select", "required": True, "value": "Else", "options": ["One"]}]),
        ):
            with self.assertRaises(ValueError):
                parse_result(raw)

    def test_failures_carry_a_safe_detail_and_never_an_input_value(self) -> None:
        private = "PRIVATE-PAGE-TEXT"
        field = {"label": "Name", "kind": "text", "required": True, "value": "Owner"}
        cases = {
            "filled result has incomplete fields": report(fields=[]),
            "needs_answers requires questions": report("needs_answers", questions=[]),
            "submitted requires confirmation text": report("submitted", confirmation=None),
            "reported option must be an exact inventoried option": report(
                fields=[{**field, "kind": "select", "value": private, "options": ["One"]}]
            ),
            "secret field refused": report(fields=[{**field, "label": "Password", "value": private}]),
            "fields.0.required:bool_type": report(fields=[{**field, "required": private}]),
            "?:extra_forbidden": report(**{private: "x"}),
            "invalid_json": "{" + private,
            "duplicate_key": report().replace('{"status": "filled",', '{"status":"submitted","status":"filled",'),
            "missing_or_oversized": None,
        }
        for expected, raw in cases.items():
            with self.subTest(expected=expected), self.assertRaises(ResultInvalid) as caught:
                parse_result(raw)
            self.assertEqual(caught.exception.detail, expected)
            self.assertNotIn(private, caught.exception.detail)

    def test_recoverable_shapes_become_owner_questions_not_discarded_runs(self) -> None:
        name = {"label": "Name", "kind": "text", "required": True, "value": "Owner"}
        empty = {"label": "Pronouns", "kind": "select", "required": True, "value": None, "options": ["He", "She"]}
        blank = {"label": "Website", "kind": "url", "required": True, "value": "  "}
        optional = {"label": "Nickname", "kind": "text", "required": False, "value": None}
        result = parse_result(report(fields=[name, empty, blank, optional]))
        self.assertEqual((result.status, result.downgraded), ("needs_answers", 2))
        self.assertEqual([f.label for f in result.fields], ["Name", "Nickname"])
        self.assertEqual(
            [(q.label, q.kind, q.options) for q in result.questions],
            [("Pronouns", "select", ["He", "She"]), ("Website", "url", [])],
        )
        listed = parse_result(report(questions=[{"label": "Visa?", "kind": "select", "required": True, "options": ["Yes"]}]))
        self.assertEqual((listed.status, listed.downgraded, len(listed.fields)), ("needs_answers", 1, 1))
        self.assertEqual(parse_result(report()).downgraded, 0)

    def test_optional_questions_are_empty_fields_and_never_need_answers(self) -> None:
        optional = {"label": "Nickname", "kind": "text", "required": False}
        for status in ("filled", "needs_answers"):
            for fields in ([], json.loads(report())["fields"]):
                with self.subTest(status=status, fields=fields):
                    result = parse_result(report(status, fields=fields, questions=[optional]))
                    self.assertEqual(result.status, "filled")
                    self.assertEqual(result.questions, [])
                    self.assertEqual(result.fields[-1].label, "Nickname")
                    self.assertIsNone(result.fields[-1].value)

    def test_mixed_questions_only_require_required_answers(self) -> None:
        required = {"label": "Visa?", "kind": "text", "required": True}
        optional = {"label": "Nickname", "kind": "text", "required": False}
        result = parse_result(report("needs_answers", questions=[optional, required]))
        self.assertEqual(result.status, "needs_answers")
        self.assertEqual([q.label for q in result.questions], ["Visa?"])

    def test_optional_normalization_preserves_validation(self) -> None:
        optional = {"label": "Nickname", "kind": "text", "required": False}
        for question in ({**optional, "required": "false"}, {**optional, "label": "Password"}, {**optional, "extra": "x"}):
            with self.subTest(question=question), self.assertRaises(ResultInvalid):
                parse_result(report("needs_answers", questions=[question]))
        missing = {"label": "Resume", "kind": "file", "required": True, "value": None}
        with self.assertRaises(ResultInvalid):
            parse_result(report("needs_answers", fields=[missing], questions=[optional]))

    def test_downgrade_never_weakens_strict_checks(self) -> None:
        name = {"label": "Name", "kind": "text", "required": True, "value": "Owner"}
        missing_cv = {"label": "Resume", "kind": "file", "required": True, "value": None}
        wrong_option = {"label": "Choice", "kind": "select", "required": True, "value": "Else", "options": ["One"]}
        empty = {"label": "Phone", "kind": "tel", "required": True, "value": ""}
        for fields, questions in (
            ([name, missing_cv], []),  # an unattached CV is retried, not asked of the owner
            ([name, missing_cv], [{"label": "Q", "kind": "text", "required": True, "options": []}]),
            ([name, wrong_option, empty], []),  # option exactness still rejects the run
            ([{**name, "label": "API Key", "value": ""}], []),  # secret-looking labels are still refused
        ):
            with self.subTest(fields=str(fields)[:40]), self.assertRaises(ResultInvalid):
                parse_result(report(fields=fields, questions=questions))

    def test_prompt_scrubs_nested_secret_keys_and_recognizable_tokens(self) -> None:
        block = data_block(
            {"profile": {"name": "Owner", "password": "pw", "nested": {"api_key": "key", "cookie": "c"}}, "redis_url": "r"}
        )
        for key in ("password", "api_key", "cookie", "redis_url"):
            self.assertNotIn(key, block)
        self.assertIn('"name":"Owner"', block)
        with self.assertRaises(ValueError):
            data_block({"fact": "Bearer sensitive"})


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = Settings(_env_file=None, jobpilot_home=ROOT)
        self.app: Any = MagicMock(spec=Services)
        self.app.db = MagicMock()
        self.app.cfg, self.app.node = self.cfg, "mac"
        self.app.db.transaction.side_effect = lambda: nullcontext()
        self.app.db.session_lock.side_effect = lambda *_: nullcontext()
        preflight = patch("jobpilot.worker.runner.PreflightRepository")
        preflight.start().return_value.done.return_value = False
        self.addCleanup(preflight.stop)
        watch = patch("jobpilot.worker.runner.ParkWatchService")
        self.watch = watch.start()
        self.addCleanup(watch.stop)
        bulk = patch("jobpilot.worker.runner.BulkFillService")
        bulk.start().return_value.run.return_value = {}
        self.addCleanup(bulk.stop)
        self.app.lease_run.side_effect = lambda *_: nullcontext()
        self.app.load_runtime_settings.return_value = RuntimeSettings()
        self.job = {"status": "discovered", "waiting_on": "agent", "source": "platform", "board_id": "custom", "cv_version_id": 1}
        self.form: dict[str, Any] = {}
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.calls: list[str] = []
        self.approval: str | None = None
        self.app.job.side_effect = lambda _: self.job.copy()
        self.app.form.side_effect = lambda _: self.form.copy() or None
        self.app.event.side_effect = lambda _, kind, actor, payload=None: self.events.append((kind, payload or {}))
        self.app.wait.side_effect = self.wait
        self.app.transition.side_effect = self.transition
        self.app.board_policy.return_value = "auto"
        self.app.browser_session_present.return_value = True
        self.app.approval_for.side_effect = lambda *_: self.approval
        self.app.submit_clicked.side_effect = lambda _: any(kind == "submit_clicked" for kind, _ in self.events)
        self.app.set_inventory.side_effect = self.inventory
        self.app.set_meta.return_value = {"ok": True}
        self.app.record_fill.side_effect = lambda *_: self.form.update(fill_done_at=True)
        self.app.record_verification.side_effect = self.verification
        self.app.approvals = ApprovalsService(self.app)
        self.mode = ModeDecision("review", "review", None, None)
        self.app.submit_mode = MagicMock()
        self.app.submit_mode.decide.side_effect = lambda _: self.mode
        self.app.load_known.return_value = Known()
        self.audit: list[str] = []
        self.retry = True
        self.approvals: Any = MagicMock(spec=ApprovalsRepository)
        self.app.approvals.repository = self.approvals
        self.approvals.find_job_events_for_verification_gates.side_effect = lambda _: {"payload": self.latest_check()}
        self.approvals.find_applications_for_record_submitted.return_value = None
        self.app.verification_gates.side_effect = self.app.approvals.verification_gates
        self.driver: Any = MagicMock()
        self.driver.run_direct.side_effect = self.model
        self.raw: object = report()
        self.runner = JobRunner(self.cfg, self.app, self.driver)
        self.runner.repository = MagicMock()
        self.runner.repository.find_automation_enabled.return_value = True
        self.stop = MagicMock(is_set=MagicMock(return_value=False))

    def wait(self, _: str, who: str, reason: str) -> None:
        self.job["waiting_on"] = who
        self.events.append(("wait", {"reason": reason}))

    def transition(self, _: str, to: str, context: GateContext | None = None, **kwargs: Any) -> None:
        result = can_transition(self.job["status"], to, context)
        if not result.ok:
            raise PermissionError(result.reason)
        self.job["status"] = to
        self.events.append(("transition", {"to": to}))

    def inventory(self, _: str, fields: list[dict[str, Any]], **kwargs: Any) -> None:
        self.form = {"inventory": page_fields(fields, 0), "missing": [], "pages_complete": kwargs["complete"], "draft_hash": None}
        if self.job["status"] == "discovered":
            self.transition("j", "preparing")

    def latest_check(self) -> dict[str, Any]:
        return next((payload for kind, payload in reversed(self.events) if kind == "check"), {})

    def verification(self, _: str, draft: str, payload: dict[str, Any]) -> None:
        self.form.update(draft_hash=draft, verified_at=True)
        self.events.append(("check", payload))
        self.transition("j", "ready", self.app.verification_gates("j"))

    def model(self, *_: Any, **kwargs: Any) -> object:
        self.calls.append("model")
        return self.raw

    def run_job(self) -> RunEnd:
        def plan(_: str, values: list[dict[str, Any]], missing: list[str]) -> None:
            self.form.update(plan=values, missing=missing)

        with (
            patch("jobpilot.service.direct.DirectRepository") as repository,
            patch.object(DirectService, "cv_path", return_value=Path("/cv.pdf")),
            patch.object(DirectService, "prepare", return_value="DATA") as prepare,
            patch.object(DirectService, "page_text", return_value="Application form"),
            patch.object(DirectService, "audit_filled", side_effect=lambda *_: self.audit),
            patch.object(DirectService, "form_retry_available", side_effect=lambda *_: self.retry),
        ):
            self.prepare = prepare
            repository.return_value.plan.side_effect = plan
            repository.return_value.skipped_question_keys.return_value = set()
            repository.return_value.owner_completed_labels.return_value = set()
            repository.return_value.count_events_since.return_value = 0
            return self.runner.run("j", "task", self.stop)

    def approve(self) -> None:
        self.job.update(status="awaiting_approval", waiting_on="agent")
        self.form.update(draft_hash="hash", verified_at=True, pages_complete=True, missing=[])
        self.events.append(("check", {"ok": True, "draftHash": "hash", "verification": "agent-reported"}))
        self.approval = "hash"

    def test_required_questions_nobody_can_answer_block_before_any_tab_or_model_run(self) -> None:
        form = PostingForm("greenhouse", PostingMeta(), (PostingQuestion("gh:q1", "Why us?", "textarea", True),))
        with (
            patch("jobpilot.worker.runner.PostingPrefetch") as prefetch,
            patch("jobpilot.worker.runner.QuestionPlanner") as planner,
        ):
            prefetch.return_value.fetch.return_value = form
            prefetch.return_value.duplicate = False
            planner.return_value.surface.return_value = True
            self.assertEqual(self.run_job(), RunEnd("blocked"))
        planner.return_value.plan.assert_called_once()
        self.driver.run_direct.assert_not_called()  # the agent never started
        self.app.ensure_tab.assert_not_called()  # and no tab was opened

    def test_prefetch_failure_does_not_stop_the_browser_path(self) -> None:
        with patch("jobpilot.worker.runner.PostingPrefetch") as prefetch:
            prefetch.return_value.fetch.side_effect = RuntimeError("network")
            prefetch.return_value.duplicate = False
            self.assertEqual(self.run_job(), RunEnd("blocked"))  # reaches the agent and parks for approval
        self.driver.run_direct.assert_called_once()

    def test_filled_stores_draft_and_parks_at_approval_without_code_verification(self) -> None:
        self.assertEqual(self.run_job(), RunEnd("blocked"))
        self.assertEqual(self.job["status"], "awaiting_approval")
        self.assertEqual(self.form["plan"][0]["source"], "agent")
        self.assertEqual(self.latest_check()["verification"], "agent-reported")
        self.assertFalse(self.app.verification_gates("j").form_fully_mapped)
        self.assertTrue(self.app.verification_gates("j").agent_reported_draft)
        self.assertEqual(len(self.form["draft_hash"]), 64)
        self.driver.run_direct.assert_called_once()

    def test_missing_questions_keep_options_and_owner_answer_keys(self) -> None:
        self.raw = report("needs_answers")
        self.assertEqual(self.run_job(), RunEnd("blocked"))
        self.assertEqual(self.job["waiting_on"], "you")
        self.assertEqual(self.form["missing"], ["page:0:reported:1"])
        self.assertEqual(self.form["inventory"][-1]["options"], ["Yes", "No"])

    def test_optional_only_agent_result_reaches_approval_without_answer_wait(self) -> None:
        self.raw = report("needs_answers", questions=[{"label": "Nickname", "kind": "text", "required": False}])
        self.run_job()
        self.assertEqual(self.job["status"], "awaiting_approval")
        self.assertEqual(self.form["missing"], [])
        self.assertFalse(self.form["inventory"][-1]["required"])
        waits = [p["reason"] for kind, p in self.events if kind == "wait"]
        self.assertEqual(waits, ["review the filled application in Neo, submit it yourself, then confirm here"])

    def test_mixed_agent_result_parks_only_required_question(self) -> None:
        required = {"label": "Visa?", "kind": "text", "required": True}
        optional = {"label": "Nickname", "kind": "text", "required": False}
        self.raw = report("needs_answers", questions=[optional, required])
        self.run_job()
        self.assertEqual(self.form["missing"], ["page:0:reported:2"])
        self.assertEqual(self.form["inventory"][-1]["label"], "Visa?")

    def test_blocked_hands_note_to_owner(self) -> None:
        self.raw = report("blocked", note="CAPTCHA")
        self.assertEqual(self.run_job(), RunEnd("blocked"))
        self.assertIn(("wait", {"reason": "CAPTCHA"}), self.events)

    def test_invalid_json_requeues_one_failed_step_never_success(self) -> None:
        self.raw = "not JSON"
        self.assertEqual(self.run_job(), RunEnd("queued", delay=30))
        self.assertEqual(self.job["status"], "discovered")
        self.assertIn("agent_run_failed", [kind for kind, _ in self.events])
        self.driver.run_direct.assert_called_once()

    def test_unapproved_report_is_submitted_and_in_needs_you_without_any_approval(self) -> None:
        self.raw = report("submitted")
        self.assertEqual(self.run_job(), RunEnd("blocked"))
        self.assertEqual((self.job["status"], self.job["waiting_on"]), ("submitted", "you"))
        self.assertIn("unapproved_submit_reported", [kind for kind, _ in self.events])
        self.assertTrue(self.app.submit_clicked("j"))
        self.approvals.insert_approvals_for_approve_submit.assert_not_called()
        self.approvals.insert_reported_application.assert_called_once_with(
            "j", 1, "unapproved-reported", "Thank you for applying"
        )

    def auto_mode(self) -> None:
        self.mode = ModeDecision("auto", "auto", None, None)

        def delete_fence(_: str) -> int:
            before = len(self.events)
            self.events[:] = [e for e in self.events if not (e[0] == "submit_clicked" and e[1].get("auto"))]
            return before - len(self.events)

        self.approvals.delete_auto_submit_fence.side_effect = delete_fence
        self.approvals.find_unconsumed_submit_approval.return_value = None
        self.approvals.insert_approvals_for_approve_submit.side_effect = lambda _, draft, __: setattr(self, "approval", draft)

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.events]

    def test_mode_is_snapshotted_and_recorded_before_the_run(self) -> None:
        self.run_job()
        snapshot = next(p for kind, p in self.events if kind == "submit_mode")
        self.assertEqual(snapshot["mode"], "review")
        self.assertNotIn("submit_clicked", self.kinds())  # review runs never write the fence

    def test_review_mode_instruction_is_prepared_with_the_review_mode(self) -> None:
        self.run_job()
        self.assertEqual(self.prepare.call_args.kwargs["mode"], "review")

    def test_auto_mode_writes_the_fence_before_the_agent_runs_and_records_the_snapshot(self) -> None:
        self.auto_mode()
        order: list[str] = []
        self.driver.run_direct.side_effect = lambda *a, **k: order.append(
            "fenced" if "submit_clicked" in self.kinds() else "unfenced"
        )
        self.raw = report("filled")
        self.run_job()
        self.assertEqual(order, ["fenced"])
        self.assertEqual(next(p for kind, p in self.events if kind == "submit_mode")["mode"], "auto")

    def test_auto_submitted_report_is_recorded_with_a_system_approval_and_no_kill_switch(self) -> None:
        self.auto_mode()
        self.raw = report("submitted")
        with patch("jobpilot.worker.runner.SubmitEvidenceService") as evidence:
            self.assertEqual(self.run_job(), RunEnd("done"))
        evidence.return_value.verify.assert_called_once()
        self.assertEqual(self.job["status"], "submitted")
        self.assertNotIn("unapproved_submit_reported", self.kinds())
        self.app.set_automation.assert_not_called()
        approval = next(p for kind, p in self.events if kind == "approval")
        self.assertEqual(approval["scope"], "auto-mode")
        self.approvals.insert_approvals_for_approve_submit.assert_called_once_with("j", self.form["draft_hash"], "auto-mode")
        self.approvals.update_approvals_for_record_submitted.assert_called_once_with("j")  # consumed by this run
        self.approvals.insert_reported_application.assert_called_once_with("j", 1, "agent-reported", "Thank you for applying")

    def test_review_mode_submitted_report_stays_the_unapproved_kill_switch(self) -> None:
        self.raw = report("submitted")
        self.assertEqual(self.run_job(), RunEnd("blocked"))
        self.assertIn("unapproved_submit_reported", self.kinds())
        self.app.set_automation.assert_called_once_with(False, actor="system", reason="unapproved_submit")

    def test_auto_run_that_stopped_before_the_click_releases_the_fence_and_parks_for_review(self) -> None:
        self.auto_mode()
        self.raw = report("filled")
        self.assertEqual(self.run_job(), RunEnd("blocked"))
        self.assertEqual(self.job["status"], "awaiting_approval")
        self.assertNotIn("submit_clicked", self.kinds())
        self.assertIn("submit_fence_released", self.kinds())

    def test_parked_job_runs_only_the_verification_and_only_while_a_request_is_open(self) -> None:
        self.job.update(status="awaiting_approval", waiting_on="agent")
        for pending, expected in ((False, RunEnd("blocked")), (True, RunEnd("done"))):
            with (
                patch("jobpilot.worker.runner.VerificationService") as verification,
                patch("jobpilot.worker.runner.BrowserRepository") as tabs,
                patch.object(DirectService, "cv_path", return_value=Path("/cv.pdf")),
            ):
                verification.return_value.pending.return_value = pending
                verification.return_value.finish.return_value = "done"
                verification.return_value.instruction.return_value = "VERIFICATION RUN"
                tabs.return_value.find_session_tab.return_value = {"target_id": "7"}
                self.raw = report("blocked", note="no fresh mail")
                self.assertEqual(self.runner.run("j", "task", self.stop), expected)
                self.assertEqual(self.driver.run_direct.called, pending)
                self.driver.run_direct.reset_mock()
                if pending:
                    verification.return_value.finish.assert_called_once()
                    self.assertEqual(verification.return_value.finish.call_args.args[1].status, "blocked")

    def test_filled_with_visible_problems_is_not_parked_and_retries_once(self) -> None:
        self.audit = ["required field still empty: Phone"]
        self.raw = report("filled")
        end = self.run_job()
        self.assertEqual(end, RunEnd("queued", delay=5, refund_attempt=True))
        self.assertEqual(end.detail, "form_incomplete")
        self.assertNotEqual(self.job["status"], "awaiting_approval")
        self.assertIn(("form_incomplete", {"problems": self.audit, "retry": True}), self.events)
        self.assertNotIn("wait", self.kinds())

    def test_second_incomplete_report_goes_to_needs_you_with_the_list(self) -> None:
        self.audit = ["an email field shows a@b.c, not your email"]
        self.retry = False
        self.raw = report("filled")
        self.assertEqual(self.run_job(), RunEnd("blocked"))
        self.assertEqual(self.job["waiting_on"], "you")
        self.assertNotEqual(self.job["status"], "awaiting_approval")
        reason = next(p["reason"] for k, p in self.events if k == "wait")
        self.assertIn("not your email", reason)

    def test_incomplete_auto_run_releases_the_fence_before_holding(self) -> None:
        self.auto_mode()
        self.audit = ["the page shows an error: x"]
        self.raw = report("filled")
        self.run_job()
        self.assertIn("submit_fence_released", self.kinds())
        self.assertNotIn("submit_clicked", self.kinds())

    def test_a_run_that_parks_for_review_stores_the_tab_baseline_for_the_watcher(self) -> None:
        self.raw = report("filled")
        self.run_job()
        self.assertEqual(self.job["status"], "awaiting_approval")
        self.watch.return_value.store_baseline.assert_called_once_with("j")

    def test_a_run_that_does_not_park_stores_no_baseline(self) -> None:
        self.raw = report("blocked", note="CAPTCHA")
        self.run_job()
        self.watch.return_value.store_baseline.assert_not_called()

    def test_auto_run_that_needs_answers_releases_the_fence_so_the_owner_can_answer(self) -> None:
        self.auto_mode()
        self.raw = report("needs_answers")
        self.run_job()
        self.assertNotIn("submit_clicked", self.kinds())
        self.assertEqual(self.job["waiting_on"], "you")

    def test_auto_run_blocked_after_the_fence_keeps_it_and_says_uncertain(self) -> None:
        self.auto_mode()
        self.raw = report("blocked", note="CAPTCHA")
        self.assertEqual(self.run_job(), RunEnd("blocked"))
        self.assertIn("submit_clicked", self.kinds())
        reason = next(p["reason"] for kind, p in reversed(self.events) if kind == "wait")
        self.assertIn("submission uncertain", reason)

    def test_auto_run_with_unusable_output_is_observed_once_then_handed_over_never_retried(self) -> None:
        self.auto_mode()
        self.raw = "not JSON"
        self.app.browser_session_present.return_value = False
        self.assertEqual(self.run_job(), RunEnd("blocked"))
        self.assertIn("submit_clicked", self.kinds())
        self.assertEqual(self.driver.run_direct.call_count, 1)
        reason = next(p["reason"] for kind, p in reversed(self.events) if kind == "wait")
        self.assertIn("no second submit", reason)

    def test_auto_run_with_unusable_output_but_a_confirmation_on_the_page_is_recorded_as_submitted(self) -> None:
        self.auto_mode()
        self.raw = "not JSON"
        with patch("jobpilot.worker.runner.check_confirmation", return_value=type("R", (), {"ok": True})()):
            self.assertEqual(self.run_job(), RunEnd("done"))
        self.assertEqual(self.job["status"], "submitted")
        self.assertEqual(next(p for kind, p in self.events if kind == "approval")["scope"], "auto-mode")

    def test_mail_failure_note_is_recorded_so_the_next_decision_falls_back_to_review(self) -> None:
        self.raw = report("blocked", note="The email verification code never arrived")
        self.run_job()
        self.assertIn("email_verification_failed", self.kinds())

    def test_preparation_submission_stops_automation_even_with_an_existing_approval(self) -> None:
        self.form.update(draft_hash="hash")
        self.approval = "hash"  # an old approval cannot convert a preparation run into an approved submit
        self.raw = report("submitted")
        self.assertEqual(self.run_job(), RunEnd("blocked"))
        self.app.set_automation.assert_called_once_with(False, actor="system", reason="unapproved_submit")
        self.approvals.insert_reported_application.assert_called_once_with(
            "j", 1, "unapproved-reported", "Thank you for applying"
        )
        self.assertIn("unapproved_submit_reported", [kind for kind, _ in self.events])

    def test_no_agent_run_starts_on_a_job_waiting_for_the_owner_to_submit(self) -> None:
        self.approve()  # defence in depth: the claim query already refuses awaiting_approval jobs
        self.assertEqual(self.run_job(), RunEnd("blocked"))
        self.driver.run_direct.assert_not_called()
        self.assertFalse(self.app.submit_clicked("j"))
        self.assertEqual(self.run_job(), RunEnd("blocked"))
        self.driver.run_direct.assert_not_called()

    def test_invalid_output_still_checks_for_unapproved_confirmation(self) -> None:
        self.raw = "invalid"
        with patch.object(self.runner, "_detect_direct_submission", return_value=True) as detect:
            self.assertEqual(self.run_job(), RunEnd("blocked"))
        detect.assert_called_once_with("j")

    def test_preparation_interruptions_requeue_without_attempt_refund(self) -> None:
        for reason in ("agent_stalled", "draining", "automation_stopped", "lease_check_failed"):
            with self.subTest(reason=reason):
                self.setUp()
                self.driver.run_direct.side_effect = AgentInterrupted(reason)
                self.assertEqual(
                    self.run_job(), RunEnd("queued", delay=30 if reason in ("agent_stalled", "lease_check_failed") else 5)
                )
                if reason == "agent_stalled":
                    self.assertIn("agent_stalled", [kind for kind, _ in self.events])

    def test_preparation_lost_lease_keeps_existing_outcome_without_refund(self) -> None:
        self.driver.run_direct.side_effect = PermissionError("job lease lost")
        self.assertEqual(self.run_job(), RunEnd("queued"))

    def test_an_external_tracker_row_never_starts_the_agent(self) -> None:
        self.job["source"] = "manual"
        self.app.external_missing.return_value = []
        self.assertEqual(self.runner.run("j", "task", self.stop), RunEnd("done"))
        self.driver.run_direct.assert_not_called()

    def test_track_selection_runs_before_the_model_in_preparation_only(self) -> None:
        with patch.object(DirectService, "ensure_track", return_value="ready") as ensure:
            self.run_job()
        self.assertEqual(ensure.call_args.args[0], "j")
        self.assertTrue(callable(ensure.call_args.args[1]))
        self.assertIn("model", self.calls)

    def test_unmatched_track_blocks_and_unloaded_page_requeues_before_the_model(self) -> None:
        for outcome, expected in (("blocked", RunEnd("blocked")), ("retry", RunEnd("queued", delay=30))):
            with self.subTest(outcome=outcome), patch.object(DirectService, "ensure_track", return_value=outcome):
                self.assertEqual(self.run_job(), expected)
        self.assertNotIn("model", self.calls)

    def test_permission_denied_requeues_with_a_count_instead_of_blocking_the_owner(self) -> None:
        self.driver.run_direct.side_effect = ClaudeRunError("denied", reason="permission_denied", count=2)
        self.assertEqual(self.run_job(), RunEnd("queued", delay=30))
        self.assertIn(("agent_run_failed", {"step": "direct", "reason": "permission_denied", "count": 2}), self.events)
        self.assertFalse([e for e in self.events if e[0] == "wait"])

    def test_invalid_result_event_names_the_failed_check_not_the_value(self) -> None:
        self.raw = report(fields=[])
        with self.assertLogs("jobpilot.worker", "WARNING") as logged:
            self.assertEqual(self.run_job(), RunEnd("queued", delay=30))
        failed = [p for kind, p in self.events if kind == "agent_run_failed"]
        self.assertEqual(
            failed, [{"step": "direct", "reason": "invalid_result", "detail": "filled result has incomplete fields"}]
        )
        self.assertIn("filled result has incomplete fields", logged.output[0])

    def test_cli_failure_detail_is_its_reason(self) -> None:
        self.driver.run_direct.side_effect = ClaudeRunError("agent result missing", reason="cli_error")
        self.run_job()
        failed = [p for kind, p in self.events if kind == "agent_run_failed"]
        self.assertEqual(failed[0]["detail"], "cli_error: agent result missing")

    def test_downgraded_result_asks_the_owner_and_is_recorded(self) -> None:
        self.raw = report(
            fields=[
                {"label": "Name", "kind": "text", "required": True, "value": "Owner"},
                {"label": "Pronouns", "kind": "text", "required": True, "value": None},
            ]
        )
        self.assertEqual(self.run_job(), RunEnd("blocked"))
        self.assertIn(("direct_result_downgraded", {"questions": 1}), self.events)
        self.assertIn(("wait", {"reason": "answer the missing application questions"}), self.events)


class TrackSelectionTests(unittest.TestCase):
    """Direct mode picks the base CV track from the job page before the sandbox needs a CV path."""

    def setUp(self) -> None:
        self.svc: Any = MagicMock(spec=Services)
        self.svc.db = MagicMock()
        self.svc.db.transaction.side_effect = lambda: nullcontext()
        self.svc.cfg, self.svc.node = Settings(_env_file=None), "mac"
        self.svc.store = MagicMock()
        self.job: dict[str, Any] = {
            "role": "Engineer",
            "cv_track_id": None,
            "cv_version_id": None,
            "cv_mode": "base",
            "url": "https://boards.example/jobs/1",
            "apply_url": None,
            "location": "",
        }
        self.svc.job.side_effect = lambda _: self.job.copy()
        from jobpilot.service.cv_selection import CvChoice, Evidence

        self.choice = CvChoice(
            track_id="swe",
            job_market="us",
            summary="Relevant engineering projects.",
            evidence=[Evidence(requirement="Engineering", excerpt="Built APIs with Python")],
        )
        self.service = DirectService(self.svc)
        self.service.repository = MagicMock()
        self.service.repository.assign_reasoned_track.return_value = {"sha256": "a" * 64}
        self.chrome: Any = MagicMock()
        self.posting = "Software Engineer Intern. Python and SQL. " * 10  # long enough to count as a rendered posting
        self.chrome.page_state.side_effect = [
            PageState("about:blank", "complete", ""),
            PageState("https://boards.example/jobs/1", "complete", self.posting),
            PageState("https://boards.example/jobs/1", "complete", self.posting),  # unchanged: text has settled
        ]
        directory = patch("jobpilot.service.direct.job_dir_for", return_value=Path("unused-mocked-cv-job"))
        directory.start()
        self.addCleanup(directory.stop)
        patches = [
            patch("jobpilot.service.direct.neo_browser", return_value=self.chrome),
            patch("jobpilot.service.direct.BrowserRepository"),
            patch("jobpilot.service.direct.time.sleep"),
            patch("jobpilot.service.direct.CvSelectionService"),
        ]
        self.tabs = patches[1].start()
        self.tabs.return_value.find_session_tab.return_value = {"target_id": "tab-1"}
        patches[0].start()
        patches[2].start()
        self.selector = patches[3].start().return_value
        self.selector.choose.return_value = (self.choice, 9)
        self.selector.decline_reason = ""
        self.service.repository.count_events.return_value = 0
        for item in patches:
            self.addCleanup(item.stop)

    def events(self) -> list[Any]:
        return [call.args for call in self.svc.event.call_args_list]

    def test_fresh_job_gets_a_track_from_the_loaded_page_in_its_own_tab(self) -> None:
        self.assertEqual(self.service.ensure_track("j"), "ready")
        self.svc.ensure_tab.assert_called_once_with("j")
        self.chrome.navigate.assert_called_once_with("tab-1", "https://boards.example/jobs/1")
        self.assertEqual(self.selector.choose.call_args.args[:2], ("j", self.posting))
        self.svc.suggest_track.assert_not_called()
        self.service.repository.assign_reasoned_track.assert_called_once_with("swe", 9, "j")
        self.assertEqual(
            self.events(),
            [
                (
                    "j",
                    "cv_track_selected",
                    "system",
                    {
                        "from": None,
                        "track": "swe",
                        "version": 9,
                        "source": "model-comparison",
                        "summary": self.choice.summary,
                        "evidence": [item.model_dump() for item in self.choice.evidence],
                    },
                )
            ],
        )

    def test_owner_chosen_track_and_existing_cv_are_untouched(self) -> None:
        for change in ({"cv_track_id": "owner"}, {"cv_version_id": 7}, {"cv_mode": "tailor"}):
            with self.subTest(change=change):
                self.job.update(change)
                self.assertEqual(self.service.ensure_track("j"), "ready")
        self.svc.ensure_tab.assert_not_called()
        self.chrome.page_state.assert_not_called()
        self.service.repository.assign_reasoned_track.assert_not_called()

    def test_undecided_comparison_tells_the_owner_why_and_never_picks_a_default(self) -> None:
        reason = "the comparison gave no usable answer (track_id must be exactly one of: a, b)"
        self.selector.choose.return_value = None
        self.selector.decline_reason = reason
        self.assertEqual(self.service.ensure_track("j"), "blocked")
        self.svc.wait.assert_called_once_with("j", "you", f"choose a CV for this job: {reason}")
        self.service.repository.assign_reasoned_track.assert_not_called()
        self.svc.suggest_track.assert_not_called()

    def test_a_failed_comparison_is_named_without_leaking_its_content(self) -> None:
        self.selector.choose.side_effect = ValueError("untrusted result content")
        self.assertEqual(self.service.ensure_track("j"), "blocked")
        message = self.svc.wait.call_args.args[2]
        self.assertIn("ValueError", message)
        self.assertNotIn("untrusted", message)
        self.service.repository.assign_reasoned_track.assert_not_called()

    def test_page_shell_is_not_mistaken_for_the_posting(self) -> None:
        shell = PageState("https://boards.example/jobs/1", "complete", "Loading...")
        done = PageState("https://boards.example/jobs/1", "complete", self.posting)
        self.chrome.page_state.side_effect = [PageState("about:blank", "complete", ""), shell, shell, done, done]
        self.assertEqual(self.service.ensure_track("j"), "ready")
        self.assertEqual(self.selector.choose.call_args.args[1], self.posting)

    def test_page_that_never_renders_is_retried_a_bounded_number_of_times(self) -> None:
        shell = PageState("https://boards.example/jobs/1", "complete", "Loading...")
        self.svc.suggest_track.return_value = ("swe", 3)
        self.service.repository.current_track_version.return_value = 7
        for tries, outcome in ((0, "retry"), (2, "retry"), (3, "ready")):
            with self.subTest(tries=tries):
                self.chrome.page_state.side_effect = [PageState("about:blank", "complete", "")] + [shell] * 40
                self.service.repository.count_events.return_value = tries
                self.svc.event.reset_mock()
                self.svc.wait.reset_mock()
                clock = iter([0.0, 1.0, 2.0, 3.0] + [30.0] * 40)  # the 20 s deadline passes after the third poll
                with patch("jobpilot.service.direct.time.monotonic", side_effect=clock.__next__):
                    self.assertEqual(self.service.ensure_track("j"), outcome)
                self.selector.choose.assert_not_called()  # no model call, no guess from a shell
                if outcome == "retry":
                    self.assertEqual(self.events()[0][1], "cv_select_page_retry")
                else:  # out of retries: the agent reads the page itself, so the job is not stuck on a CV comparison
                    kinds = [kind for _, kind, *_ in self.events()]
                    self.assertIn("cv_select_fallback", kinds)
                    self.svc.wait.assert_not_called()
                    self.service.repository.assign_reasoned_track.assert_called_with("swe", 7, "j")

    def test_page_that_cannot_be_read_falls_back_to_the_best_matching_track(self) -> None:
        self.svc.suggest_track.return_value = ("swe", 3)
        self.service.repository.current_track_version.return_value = 7
        self.chrome.page_state.side_effect = BrowserError("No target with given id")
        self.assertEqual(self.service.ensure_track("j"), "ready")
        self.svc.wait.assert_not_called()
        fallback = next(e for e in self.events() if e[1] == "cv_select_fallback")
        self.assertEqual(fallback[3], {"reason": "page_unreadable:BrowserError", "track": "swe"})
        self.selector.choose.assert_not_called()

    def test_without_any_usable_track_the_owner_is_still_asked(self) -> None:
        self.svc.suggest_track.return_value = None
        self.chrome.page_state.side_effect = BrowserError("gone")
        self.assertEqual(self.service.ensure_track("j"), "blocked")
        self.assertIn("could not be read", self.svc.wait.call_args.args[2])

    def test_a_page_level_neo_failure_is_not_offline_but_a_real_outage_is(self) -> None:
        self.svc.suggest_track.return_value = ("swe", 3)
        self.service.repository.current_track_version.return_value = 7
        self.chrome.page_state.side_effect = BrowserUnavailable("Invalid Neo page data")
        self.svc.tab_binder.return_value.online.return_value = True  # Neo answers: only this page is unreadable
        self.assertEqual(self.service.ensure_track("j"), "ready")
        self.assertIn("cv_select_fallback", [e[1] for e in self.events()])
        self.svc.tab_binder.return_value.online.return_value = False
        with self.assertRaises(BrowserOffline):
            self.service.ensure_track("j")

    def test_page_that_never_loads_is_requeued_not_blocked(self) -> None:
        self.chrome.page_state.side_effect = lambda *_: PageState("https://boards.example/jobs/1", "loading", "")
        with patch("jobpilot.service.direct.time.monotonic", side_effect=[0.0, 0.0, 99.0]):
            self.assertEqual(self.service.ensure_track("j"), "retry")
        self.svc.wait.assert_not_called()
        self.service.repository.assign_reasoned_track.assert_not_called()

    def test_a_track_chosen_meanwhile_is_not_overwritten_or_announced(self) -> None:
        self.service.repository.assign_reasoned_track.return_value = None
        self.job["cv_track_id"] = "owner"
        self.svc.job.side_effect = [dict(self.job, cv_track_id=None), self.job]
        self.assertEqual(self.service.ensure_track("j"), "ready")
        self.assertEqual(self.events(), [])

    def test_changed_candidate_version_requeues_without_assigning_a_different_cv(self) -> None:
        self.service.repository.assign_reasoned_track.return_value = None
        self.assertEqual(self.service.ensure_track("j"), "retry")
        self.assertEqual(self.events(), [])
        self.svc.store.link.assert_not_called()

    def test_archived_tracks_are_never_candidates(self) -> None:
        db: Any = MagicMock()
        db.all.return_value = []
        JobsRepository(db).list_cv_tracks_for_suggest_track()
        self.assertIn("archived_at IS NULL", db.all.call_args.args[0])


class ResumePreparationTests(unittest.TestCase):
    """A preparation run after owner answers says so in DATA when the earlier fill is still parked."""

    def setUp(self) -> None:
        self.svc: Any = MagicMock(spec=Services)
        self.svc.cfg = MagicMock(home=Path(__file__).resolve().parents[2])
        self.svc.credentials = MagicMock()
        self.svc.credentials.stored_hosts.return_value = []
        self.svc.db = MagicMock()
        self.svc.node = "mac"
        self.svc.job.return_value = {"url": "https://boards.example/jobs/1", "apply_url": None}
        self.svc.form.return_value = {
            "inventory": [
                {"key": "reported:0", "label": "Full name", "type": "text", "required": True, "options": None, "reported": True},
                {
                    "key": "reported:1",
                    "label": "Work authorization",
                    "type": "select",
                    "required": True,
                    "options": ["Yes", "No"],
                    "reported": True,
                },
                {
                    "key": "reported:2",
                    "label": "Account password",
                    "type": "text",
                    "required": True,
                    "options": None,
                    "reported": True,
                },
                {"key": "reported:3", "label": "Legacy", "type": "text", "required": False, "options": None},
            ],
            "plan": [{"key": "page:0:reported:0", "value": "Jane Doe", "source": "agent"}],
            "missing": ["page:0:reported:1"],
        }
        self.service = DirectService(self.svc)
        self.service.repository = MagicMock()
        self.service.repository.owner_data.return_value = {"profile": {}, "facts": [], "answers": []}
        self.service.repository.last_event.return_value = None
        patcher = patch("jobpilot.service.direct.BrowserRepository")
        self.tabs = patcher.start()
        self.tabs.return_value.find_session_tab.return_value = {"target_id": "tab-1"}
        self.addCleanup(patcher.stop)

    def data(self, text: str) -> dict[str, Any]:
        body = text.split("DATA", 1)[1]
        return json.loads(body[body.index("{") : body.rindex("}") + 1])

    def test_the_prompt_never_describes_or_authorizes_a_submit_run(self) -> None:
        text = self.service.prepare("j", Path("/cv/Abdelhadi Marjane.pdf"))
        self.assertNotIn("SUBMIT RUN", text)
        self.assertNotIn("approved_fields", text)
        self.assertIn("the owner clicks Submit, never you", text)

    def test_preparation_stop_rule_is_first_and_last(self) -> None:
        from jobpilot.service.direct import PREPARATION_STOP

        prompt = self.service.prepare("j", Path("/cv.pdf"))
        self.assertEqual(prompt.splitlines()[0], PREPARATION_STOP)
        self.assertEqual(prompt.splitlines()[-1], PREPARATION_STOP)

    def test_auto_prompt_allows_exactly_one_final_click_and_carries_the_mode(self) -> None:
        from jobpilot.service.direct import AUTO_MARK, PREPARATION_STOP

        prompt = self.service.prepare("j", Path("/cv.pdf"), mode="auto")
        self.assertEqual(prompt.splitlines()[0], AUTO_MARK)
        self.assertNotIn(PREPARATION_STOP, prompt)
        self.assertNotIn("never you", prompt)
        self.assertIn("exactly once", prompt)
        self.assertEqual(self.data(prompt)["submit_mode"], "auto")
        self.assertEqual(self.data(self.service.prepare("j", Path("/cv.pdf")))["submit_mode"], "review")

    def test_parked_tab_marks_the_run_as_a_resume_with_the_reported_fields(self) -> None:
        self.svc.browser_session_present.return_value = True
        text = self.service.prepare("j", Path("/cv/Abdelhadi Marjane.pdf"))
        data = self.data(text)
        self.assertTrue(data["resume"])
        self.assertEqual(
            data["previous_fields"],
            [
                {"label": "Full name", "kind": "text", "required": True, "value": "Jane Doe"},
                {"label": "Work authorization", "kind": "select", "required": True, "options": ["Yes", "No"]},
            ],
        )
        self.assertIn("RESUME", text)
        self.assertNotIn("password", json.dumps(data["previous_fields"]).lower())

    def test_missing_tab_is_a_plain_preparation(self) -> None:
        for present, form in ((False, self.svc.form.return_value),):
            with self.subTest(present=present):
                self.svc.browser_session_present.return_value = present
                self.svc.form.return_value = form
                text = self.service.prepare("j", Path("/cv/Abdelhadi Marjane.pdf"))
                data = self.data(text)
                self.assertNotIn("resume", data)
                self.assertNotIn("previous_fields", data)
                self.assertNotIn("RESUME", text)

    def test_presence_is_read_before_the_tab_is_ensured(self) -> None:
        order: list[str] = []
        self.svc.browser_session_present.side_effect = lambda _: order.append("present") or True
        self.svc.ensure_tab.side_effect = lambda _: order.append("ensure")
        self.service.prepare("j", Path("/cv/Abdelhadi Marjane.pdf"))
        self.assertEqual(order, ["present", "ensure"])

    def test_new_tab_with_bulk_prefill_is_not_mistaken_for_a_previous_agent_run(self) -> None:
        self.svc.browser_session_present.return_value = True
        self.svc.form.return_value = None
        prefill = {"fields": [{"label": "Name", "value": "Jane Doe"}], "remaining": []}
        text = self.service.prepare("j", Path("/cv/Abdelhadi Marjane.pdf"), prefill=prefill)
        data = self.data(text)
        self.assertEqual(data["bulk_prefill"], prefill)
        self.assertNotIn("resume", data)
        self.assertNotIn("RESUME", text)


class DriverArgvTests(unittest.TestCase):
    def test_direct_argv_has_the_neo_server_only_no_builtin_tools_and_a_scrubbed_env(self) -> None:
        cfg = Settings(_env_file=None, jobpilot_home=ROOT)
        svc: Any = MagicMock()
        driver = ClaudeDriver(cfg, svc)
        with patch("jobpilot.worker.claude.run_stream") as run:
            run.return_value = subprocess.CompletedProcess("claude", 0, json.dumps({"result": report()}), "")
            self.assertEqual(
                driver._run(
                    "j", "DATA", model="claude-haiku-5-5", check=lambda: None, session_id="00000000-0000-4000-8000-000000000001"
                ),
                report(),
            )
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("--allowedTools") + 1 : args.index("--disallowedTools")], ["mcp__neo"])
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertIn("--strict-mcp-config", args)
        self.assertNotIn("--safe-mode", args)  # it would also disable the configured MCP server
        self.assertEqual(args[args.index("--setting-sources") + 1], "")
        self.assertEqual(args[args.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(args[args.index("--output-format") + 1], "stream-json")
        denied = args[args.index("--disallowedTools") + 1 : args.index("--append-system-prompt")]
        self.assertTrue({"Bash", "Read", "Edit", "Write", "WebFetch", "WebSearch"} <= set(denied))
        self.assertEqual(args.count("--mcp-config"), 1)
        config = json.loads(args[args.index("--mcp-config") + 1])
        self.assertEqual(config, {"mcpServers": {"neo": {"type": "http", "url": cfg.neo_mcp_url}}})
        self.assertIn("Use the Neo browser MCP tools", run.call_args.args[1])
        env = run.call_args.kwargs["env"]
        self.assertFalse(any(key.startswith(("PG", "REDIS", "JP_")) for key in env))

    def test_permission_denials_fail_the_run_with_a_count_and_no_command_text(self) -> None:
        cfg = Settings(_env_file=None, jobpilot_home=ROOT)
        driver = ClaudeDriver(cfg, MagicMock())
        envelope = {
            "result": report("blocked", note="Bash permission was denied"),
            "permission_denials": [{"tool_name": "mcp__neo__evaluate", "tool_input": {"code": "SECRET-CODE"}}] * 2,
        }
        with patch("jobpilot.worker.claude.run_stream") as run:
            run.return_value = subprocess.CompletedProcess("claude", 0, json.dumps(envelope), "")
            with self.assertRaises(ClaudeRunError) as caught:
                driver._run(
                    "j", "DATA", model="claude-haiku-5-5", check=lambda: None, session_id="00000000-0000-4000-8000-000000000001"
                )
        self.assertEqual((caught.exception.reason, caught.exception.count), ("permission_denied", 2))
        self.assertNotIn("SECRET-CODE", str(caught.exception))

    def test_run_direct_refuses_any_other_model_before_anything_starts(self) -> None:
        for model in ("claude-sonnet-5-5", "claude-opus-5-5", "sonnet", "opus"):
            with self.subTest(model=model):
                cfg = Settings(_env_file=None, jobpilot_home=ROOT).model_copy(update={"agent_model_browser": model})
                driver = ClaudeDriver(cfg, MagicMock())
                with patch("jobpilot.worker.claude.run_stream") as run, self.assertRaisesRegex(ValueError, "claude-haiku-5-5"):
                    driver.run_direct("j", "DATA", worker="t", cv=Path("/cv.pdf"), check=lambda: None)
                run.assert_not_called()

    def test_the_agent_tab_is_read_from_postgres_and_a_missing_one_fails_before_the_model(self) -> None:
        svc: Any = MagicMock()
        svc.browser_target.return_value = None
        driver = ClaudeDriver(Settings(_env_file=None, jobpilot_home=ROOT), svc)
        with patch("jobpilot.worker.claude.run_stream") as run, self.assertRaisesRegex(ClaudeRunError, "tab is missing"):
            driver.run_direct("j", "DATA", worker="t", cv=Path("/cv.pdf"), check=lambda: None)
        svc.browser_target.assert_called_once_with("j")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

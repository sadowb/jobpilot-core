"""Emailed-code verification run: challenge detection, one-shot request, policy refusals, outcomes (ADR 0026 part C)."""

from __future__ import annotations

import unittest
from contextlib import nullcontext
from typing import Any
from unittest.mock import MagicMock, patch

from jobpilot.browser.neo import PageState
from jobpilot.domain.challenge import code_challenge
from jobpilot.dto.direct import DirectResult
from jobpilot.service import Services
from jobpilot.service.park_watch import ParkWatchService, Reading, fingerprint
from jobpilot.service.verification import VerificationService


class ChallengeTests(unittest.TestCase):
    def test_recognises_emailed_code_prompts(self) -> None:
        for text in (
            "Security code  Enter the 8-character security code sent to your email",
            "We just emailed you a 6-digit code. Enter it below.",
            "Check your email for the verification code",
            "Confirmation code",
        ):
            self.assertIsNotNone(code_challenge(text), text)

    def test_ignores_ordinary_form_and_confirmation_text(self) -> None:
        for text in (
            "First name Last name Email Phone Resume Submit application",
            "Thank you for applying",
            "Enter your zip code",
            "",
        ):
            self.assertIsNone(code_challenge(text), text)


def service(
    status: str = "awaiting_approval", *, applied: bool = False, fenced: bool = False, claim: int = 1
) -> tuple[VerificationService, Any]:
    app: Any = MagicMock(spec=Services)
    app.db = MagicMock()
    app.db.transaction.side_effect = lambda: nullcontext()
    app.approvals = MagicMock()
    app.approvals.repository.find_applications_for_record_submitted.return_value = {"x": 1} if applied else None
    app.submit_clicked.return_value = fenced
    job = {"status": status, "archived_at": None, "company_name": "Capstone", "role": "Eng", "url": "https://x"}
    app.job.return_value = job
    app._live.return_value = job
    svc = VerificationService(app)
    svc.repository = MagicMock()
    svc.repository.claim_request.return_value = claim
    return svc, app


def kinds(app: Any) -> list[str]:
    return [call.args[1] for call in app.event.call_args_list]


class RequestTests(unittest.TestCase):
    def test_request_writes_the_owner_fence_the_challenge_event_and_queues_the_run(self) -> None:
        svc, app = service()
        self.assertTrue(svc.request("j", "watcher", "security code"))
        self.assertEqual(kinds(app), ["submit_clicked", "verification_challenge"])
        self.assertEqual(app.event.call_args_list[0].args[3], {"owner": True, "method": "challenge-observed"})
        svc.repository.queue_run.assert_called_once_with("j")
        app.request_dispatch.assert_called_once()

    def test_an_existing_fence_is_not_written_twice(self) -> None:
        svc, app = service(fenced=True)
        svc.request("j", "owner")
        self.assertEqual(kinds(app), ["verification_challenge"])

    def test_one_shot_the_watcher_cannot_ask_twice_the_owner_may_retry(self) -> None:
        svc, app = service(claim=0)
        self.assertFalse(svc.request("j", "watcher"))
        svc.repository.claim_request.assert_called_with("j", retry=False)
        svc.request("j", "owner")
        svc.repository.claim_request.assert_called_with("j", retry=True)
        svc.repository.queue_run.assert_not_called()
        self.assertEqual(kinds(app), [])

    def test_refused_unless_parked_and_unsubmitted(self) -> None:
        for kwargs in ({"status": "preparing"}, {"status": "submitted"}, {"applied": True}):
            svc, app = service(**kwargs)
            with self.assertRaises(PermissionError):
                svc.request("j", "owner")
            svc.repository.queue_run.assert_not_called()
            self.assertEqual(kinds(app), [])

    def test_pending_needs_open_request_owner_click_and_no_application(self) -> None:
        svc, _ = service(fenced=True)
        svc.repository.find_open_request.return_value = {"verify_requested_at": 1}
        self.assertTrue(svc.pending("j"))
        svc.repository.find_open_request.return_value = None
        self.assertFalse(svc.pending("j"))
        svc, _ = service(fenced=False)
        svc.repository.find_open_request.return_value = {"verify_requested_at": 1}
        self.assertFalse(svc.pending("j"))
        svc, _ = service(fenced=True, applied=True)
        svc.repository.find_open_request.return_value = {"verify_requested_at": 1}
        self.assertFalse(svc.pending("j"))

    def test_instruction_limits_the_run_and_never_carries_secrets_or_answers(self) -> None:
        svc, _ = service()
        text = svc.instruction("j", "42")
        self.assertTrue(text.startswith("VERIFICATION RUN") and text.endswith(text.split("\n")[0]))
        self.assertIn("ONLY the one button that confirms the code", text)
        self.assertIn("verify_email_code", text)
        self.assertNotIn("answers", text)


def result(status: str, note: str | None = None, verification: bool = False) -> DirectResult:
    return DirectResult.model_validate(
        {
            "status": status,
            "role": None,
            "company": None,
            "fields": [],
            "questions": [],
            "note": note,
            "confirmation": "Thank you" if status == "submitted" else None,
            **({"verification": {"used": True, "source": "email"}} if verification else {}),
        }
    )


class FinishTests(unittest.TestCase):
    def test_confirmation_seen_records_the_owner_submit_by_email_code(self) -> None:
        svc, app = service(fenced=True)
        with patch("jobpilot.service.evidence.SubmitEvidenceService") as evidence:
            evidence.return_value.verify.return_value = True
            self.assertEqual(svc.finish("j", result("submitted", verification=True)), "done")
        app.approvals.record_owner_submit.assert_called_once_with("j", True, "email-code")
        self.assertIn("email_verification_used", kinds(app))
        svc.repository.update_done.assert_called_once_with("j")

    def test_report_without_a_seen_confirmation_goes_to_the_owner_not_to_submitted(self) -> None:
        svc, app = service(fenced=True)
        with patch("jobpilot.service.evidence.SubmitEvidenceService") as evidence:
            evidence.return_value.verify.return_value = False
            self.assertEqual(svc.finish("j", result("submitted")), "blocked")
        app.approvals.record_owner_submit.assert_not_called()
        self.assertIn("no confirmation page appeared", app.wait.call_args.args[2])

    def test_blocked_report_becomes_a_paste_the_code_card_with_the_note(self) -> None:
        svc, app = service(fenced=True)
        self.assertEqual(svc.finish("j", result("blocked", "no fresh mail from the employer")), "blocked")
        self.assertTrue(app.wait.call_args.args[2].startswith("Paste the code yourself"))
        self.assertIn("no fresh mail", app.wait.call_args.args[2])
        app.approvals.record_owner_submit.assert_not_called()
        self.assertIn("verification_blocked", kinds(app))

    def test_unusable_report_and_already_submitted_job(self) -> None:
        svc, app = service(fenced=True)
        self.assertEqual(svc.finish("j", None, "the agent's report could not be read"), "blocked")
        self.assertIn("could not be read", app.wait.call_args.args[2])
        svc, app = service("submitted", fenced=True)  # the watcher recorded it while the agent worked
        self.assertEqual(svc.finish("j", result("blocked", "x")), "done")
        app.wait.assert_not_called()
        svc.repository.update_done.assert_called_once()


class WatcherTests(unittest.TestCase):
    def watcher(
        self, text: str, baseline_text: str = "First name Last name Email Resume"
    ) -> tuple[ParkWatchService, Any, dict[str, Any]]:
        app: Any = MagicMock(spec=Services)
        app.db = MagicMock()
        app.node = "mac"
        watch = ParkWatchService(app)
        baseline = fingerprint(PageState("https://x/apply", "complete", baseline_text, "t"), [], [])
        row = {
            "id": "j",
            "board_id": "greenhouse",
            "target_id": "7",
            "park_baseline": baseline,
            "park_signal": None,
            "verify_requested_at": None,
        }
        self.state = PageState("https://x/apply", "complete", text, "t")
        return watch, app, row

    def apply(self, watch: ParkWatchService, row: dict[str, Any], started: bool = True) -> str:
        reader = lambda _t: Reading(self.state)  # noqa: E731
        with patch("jobpilot.service.park_watch.VerificationService") as verification:
            verification.return_value.request.return_value = started
            outcome = watch._apply(row, reader("7"), reader, lambda _s: None)
            self.requested = verification.return_value.request
        return outcome

    def test_code_prompt_that_the_baseline_lacked_starts_one_verification(self) -> None:
        watch, _, row = self.watcher("Security code. Enter the 8-character security code sent to your email")
        self.assertEqual(self.apply(watch, row), "challenge")
        self.requested.assert_called_once_with("j", "watcher", "security code")

    def test_no_request_when_one_is_open_when_baseline_had_it_or_when_nothing_changed(self) -> None:
        watch, _, row = self.watcher("Security code sent to your email")
        row["verify_requested_at"] = "then"
        self.assertEqual(self.apply(watch, row), "none")
        self.requested.assert_not_called()
        watch, _, row = self.watcher("Security code sent to your email", baseline_text="Security code sent to your email")
        self.assertEqual(self.apply(watch, row), "none")
        watch, _, row = self.watcher("First name Last name Email Resume")
        self.assertEqual(self.apply(watch, row), "none")
        self.requested.assert_not_called()

    def test_owner_got_there_first_is_not_an_error(self) -> None:
        watch, _, row = self.watcher("Verification code sent to your email")
        with patch("jobpilot.service.park_watch.VerificationService") as verification:
            verification.return_value.request.side_effect = PermissionError("already submitted")
            reader = lambda _t: Reading(self.state)  # noqa: E731
            self.assertEqual(watch._apply(row, reader("7"), reader, lambda _s: None), "none")


if __name__ == "__main__":
    unittest.main()

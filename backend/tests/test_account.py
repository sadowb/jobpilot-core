"""Account walls (ADR 0028, 0031): the result schema, the server taking over the login step, and the agent's rules."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from jobpilot.dto.direct import ResultInvalid, parse_result
from jobpilot.service.account_flow import FlowResult
from jobpilot.service.account_wall import MAX_WALL_RUNS, AccountWallService
from jobpilot.service.application import Services
from jobpilot.service.direct import DirectService

NEO = (Path(__file__).resolve().parents[2] / "agent" / "NEO.md").read_text()


def result(**extra: Any) -> str:
    base: dict[str, Any] = {
        "status": "blocked",
        "role": None,
        "company": None,
        "fields": [{"label": "Email", "kind": "email", "required": True, "value": "me@example.com", "options": []}],
        "questions": [],
        "note": "account created, needs the password saved",
        "confirmation": None,
    }
    base.update(extra)
    return json.dumps(base)


class SchemaTests(unittest.TestCase):
    def test_account_fact_is_accepted_for_sso_only(self) -> None:
        parsed = parse_result(result(account={"created": True, "method": "sso"}))
        assert parsed.account is not None
        self.assertEqual((parsed.account.created, parsed.account.method), (True, "sso"))
        with self.assertRaises(ResultInvalid):
            parse_result(result(account={"created": True, "method": "generated"}))  # the server records those now

    def test_account_wall_needs_a_blocked_result_and_a_known_kind(self) -> None:
        for kind in ("signup", "signin"):
            self.assertEqual(parse_result(result(account_wall=kind)).account_wall, kind)
        for bad in ({"account_wall": "login"}, {"account_wall": "signup", "status": "filled"}):
            with self.assertRaises(ResultInvalid):
                parse_result(result(**bad))

    def test_extra_and_unknown_account_keys_are_refused(self) -> None:
        for account in (
            {"created": True, "method": "sso", "extra": 1},
            {"created": True, "method": "magic"},
            {"created": "yes", "method": "sso"},
        ):
            with self.assertRaises(ResultInvalid):
                parse_result(result(account=account))

    def test_any_password_named_key_is_refused_at_any_depth(self) -> None:
        for name in ("password", "Password", "newPassword", "passcode", "pwd", "passphrase"):
            with self.subTest(name=name):
                with self.assertRaises(ResultInvalid) as raised:
                    parse_result(result(account={"created": True, "method": "sso", name: "x"}))
                self.assertEqual(str(raised.exception), "agent result refused: it names a password")
                with self.assertRaises(ResultInvalid):
                    parse_result(result(extra={"nested": [{name: "x"}]}))

    def test_a_refusal_names_where_the_secret_shaped_value_sits_and_never_the_value(self) -> None:
        cases = {
            "note": result(note="Application 1234567 received"),
            "fields.0.label": result(
                fields=[{"label": "token: abc123", "kind": "text", "required": False, "value": None, "options": []}]
            ),
        }
        for where, payload in cases.items():
            with self.subTest(where=where):
                with self.assertRaises(ResultInvalid) as raised:
                    parse_result(payload)
                self.assertEqual(raised.exception.detail, f"secret value refused at {where}")

    def test_a_zip_or_phone_code_is_not_a_secret_but_a_verification_code_is(self) -> None:
        def field(label: str, value: str) -> str:
            return result(fields=[{"label": label, "kind": "text", "required": False, "value": value, "options": []}])

        for label in ("Postal Code", "ZIP Code", "Country Phone Code"):
            parse_result(field(label, "62704"))
        for label in ("Verification code", "Code", "PIN"):
            with self.assertRaises(ResultInvalid):
                parse_result(field(label, "482913"))

    def test_an_overlong_label_is_clipped_not_refused(self) -> None:
        field = {
            "label": "I acknowledge " + "and agree " * 60,
            "kind": "custom-checkbox",
            "required": False,
            "value": None,
            "options": [],
        }
        parsed = parse_result(result(fields=[field]))
        self.assertEqual(len(parsed.fields[0].label), 240)

    def test_a_reported_password_field_is_refused(self) -> None:
        field = {"label": "Password", "kind": "text", "required": True, "value": "hunter2hunter2", "options": []}
        with self.assertRaises(ResultInvalid):
            parse_result(result(fields=[field]))


class WallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc: Any = MagicMock()
        self.svc.job.return_value = {"status": "preparing"}
        self.svc.load_known.return_value.profile = {"email": "me@example.com"}
        self.svc.credentials.has_login.return_value = False
        self.wall = AccountWallService(self.svc)
        self.wall.repository = MagicMock()
        self.wall.repository.count_events.return_value = 0
        self.flow = MagicMock()
        for target, value in (
            (
                "jobpilot.service.account_wall.neo_browser",
                MagicMock(**{"return_value.evaluate.return_value": '"https://jobs.example"'}),
            ),
            ("jobpilot.service.account_wall.AccountFlow", MagicMock(return_value=self.flow)),
            (
                "jobpilot.service.account_wall.BrowserRepository",
                MagicMock(**{"return_value.find_session_tab.return_value": {"target_id": "7"}}),
            ),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_signup_uses_the_profile_email_records_the_account_and_queues_a_retry(self) -> None:
        self.flow.create_account.return_value = FlowResult("created", 1)
        self.assertEqual(self.wall.handle("j", "signup", auto=False), "retry")
        self.flow.create_account.assert_called_once_with("7", "https://jobs.example", "me@example.com")
        self.svc.event.assert_any_call("j", "account_created", "system", {"method": "generated"})
        self.svc.wait.assert_not_called()

    def test_a_fresh_signup_is_proven_by_signing_in_in_the_same_tab(self) -> None:
        self.flow.create_account.return_value = FlowResult("created", 1)
        self.flow.sign_in.return_value = FlowResult("signed_in", 1)
        self.assertEqual(self.wall.handle("j", "signup", auto=False), "retry")
        self.flow.sign_in.assert_called_once_with("7", "https://jobs.example")

    def test_a_rejected_first_sign_in_means_verify_email_not_a_failed_signup(self) -> None:
        self.flow.create_account.return_value = FlowResult("created", 1)
        self.flow.sign_in.return_value = FlowResult("rejected", 1, "wrong password")
        self.assertEqual(self.wall.handle("j", "signup", auto=False), "retry")
        self.svc.wait.assert_not_called()

    def test_signup_on_a_site_with_a_stored_login_signs_in_instead(self) -> None:
        self.svc.credentials.has_login.return_value = True
        self.flow.sign_in.return_value = FlowResult("signed_in", 1)
        self.assertEqual(self.wall.handle("j", "signup", auto=False), "retry")
        self.flow.create_account.assert_not_called()

    def test_failures_wait_for_the_owner_with_a_code_free_reason(self) -> None:
        for status in ("consent_required", "rejected", "unreadable", "wrong_page"):
            with self.subTest(status=status):
                self.svc.wait.reset_mock()
                self.flow.create_account.return_value = FlowResult(status, 1, "Email already used")  # type: ignore[arg-type]
                self.assertEqual(self.wall.handle("j", "signup", auto=False), "blocked")
                self.assertEqual(self.svc.wait.call_args.args[:2], ("j", "you"))

    def test_auto_mode_releases_the_submit_fence_and_a_loop_is_stopped(self) -> None:
        self.flow.sign_in.return_value = FlowResult("signed_in", 1)
        self.wall.handle("j", "signin", auto=True)
        self.svc.approvals.release_auto_fence.assert_called_once_with("j")
        self.wall.repository.count_events.return_value = MAX_WALL_RUNS
        self.flow.sign_in.reset_mock()
        self.assertEqual(self.wall.handle("j", "signin", auto=False), "blocked")
        self.flow.sign_in.assert_not_called()

    def test_a_job_that_moved_on_is_left_alone(self) -> None:
        self.svc.job.return_value = {"status": "submitted"}
        self.flow.sign_in.return_value = FlowResult("signed_in", 1)
        self.assertEqual(self.wall.handle("j", "signin", auto=False), "blocked")
        self.svc.wait.assert_not_called()


class PromptTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svc: Any = MagicMock(spec=Services)
        self.svc.db = MagicMock()
        self.svc.node = "mac"
        self.svc.job.return_value = {"url": "https://boards.example/jobs/1", "apply_url": None}
        self.svc.credentials = MagicMock()
        self.svc.credentials.stored_hosts.return_value = ["boards.example"]
        self.svc.cfg = MagicMock(home=Path(__file__).resolve().parents[2])
        self.service = DirectService(self.svc)
        self.service.repository = MagicMock()
        self.service.repository.owner_data.return_value = {"profile": {}, "facts": [], "answers": []}
        self.service.repository.last_event.return_value = None
        patcher = patch("jobpilot.service.direct.BrowserRepository")
        patcher.start().return_value.find_session_tab.return_value = {"target_id": "tab-1"}
        self.addCleanup(patcher.stop)

    def test_both_modes_allow_account_buttons_and_keep_the_submit_rules_at_both_ends(self) -> None:
        for mode, mark in (
            ("review", "do not click the button that sends"),
            ("auto", "click the final Submit button exactly once"),
        ):
            with self.subTest(mode=mode):
                text = self.service.prepare("j", Path("/cv/x.pdf"), mode=mode)
                self.assertEqual(text.count("is NOT the application submit"), 2)
                self.assertEqual(len(re.findall(re.escape(mark), text)), 2)

    def test_data_carries_the_account_fact_and_whether_a_login_is_stored_never_a_credential(self) -> None:
        self.service.repository.last_event.side_effect = lambda _j, kind: (
            (5, {"method": "generated"}) if kind == "account_created" else None
        )
        text = self.service.prepare("j", Path("/cv/x.pdf"))
        data = text[text.index("DATA JSON") : text.index("END DATA")]
        self.assertIn('"account":{"created":true,"method":"generated"}', data)
        self.assertIn('"login_hosts":["boards.example"]', data)
        self.assertIn("do not create another", text)
        self.assertIn("account_wall signin", text)
        self.assertNotRegex(data, r"(?i)passw")


class NeoDocTests(unittest.TestCase):
    def test_the_agent_has_no_password_helper_and_hands_account_steps_to_the_server(self) -> None:
        for gone in ("crypto.getRandomValues", "SYMBOLS", "Runtime.evaluate", "```js"):
            self.assertNotIn(gone, NEO)
        for kept in ('"account_wall":"signup"', '"account_wall":"signin"', "Do NOT click the create-account button"):
            self.assertIn(kept, NEO)

    def test_the_prose_forbids_typing_or_reading_passwords(self) -> None:
        self.assertIn("never fill a password with `act` fill/type/press".lower(), NEO.lower())
        self.assertIn(
            "never `read`, `snapshot`, `screenshot`, `evaluate` or `grep` a password field".lower(), " ".join(NEO.lower().split())
        )
        self.assertEqual(NEO.count("are account steps, not the"), 2)

    def test_gmail_accepts_only_employer_or_ats_mail(self) -> None:
        self.assertIn("(never any other sender)", NEO)
        self.assertIn("whose host is that same\nemployer or ATS", NEO)


if __name__ == "__main__":
    unittest.main()

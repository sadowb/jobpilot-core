"""Server-driven account creation and sign-in (ADR 0031) against a local fixture ATS.

The flow logic runs over a scripted browser double. The page scripts themselves run in a real Neo browser only when
JOBPILOT_LIVE_NEO=1 (a Neo instance must be reachable at the configured MCP URL); otherwise those tests are skipped."""

from __future__ import annotations

import json
import os
import time
import unittest
from typing import Any
from unittest.mock import MagicMock

from ats_fixture import FixtureAts
from test_credentials import FakeRepository, make

from jobpilot.browser.neo import NeoBrowser
from jobpilot.browser.signin import fill_script, outcome_script, submit_script
from jobpilot.service.account_flow import AccountFlow

ORIGIN = "https://jobs.example"


class ScriptedNeo:
    """Answers evaluate() by script kind, recording what was typed."""

    def __init__(self, fill: dict[str, Any], submit: dict[str, Any], outcomes: list[dict[str, Any]]) -> None:
        self.fill, self.submit, self.outcomes = fill, submit, list(outcomes)
        self.typed: list[str] = []

    def evaluate(self, tab: str, expression: str) -> str:
        if "readback_mismatch" in expression:
            self.typed.append(expression)
            return json.dumps(self.fill)
        if "no_submit_button" in expression:
            return json.dumps(self.submit)
        return json.dumps(self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0])


def flow(neo: Any) -> tuple[AccountFlow, Any, FakeRepository, MagicMock]:
    service, repo, svc = make()
    svc.credentials = service
    return AccountFlow(svc, neo, sleep=lambda _s: None, clock=iter(range(0, 10_000)).__next__), service, repo, svc


OK_FILL = {"ok": True, "passwordFields": 2, "consentPending": 0}
OK_SUBMIT = {"ok": True, "clicked": "Create account"}
GONE = {"origin": True, "passwordFieldVisible": False, "errors": [], "invalid": 0, "verify": False}


class FlowLogicTests(unittest.TestCase):
    def test_signup_stores_pending_first_and_leaves_it_pending_until_a_signin_works(self) -> None:
        neo = ScriptedNeo(OK_FILL, OK_SUBMIT, [GONE])
        f, service, repo, svc = flow(neo)
        result = f.create_account("1", ORIGIN, "me@x.io")
        self.assertEqual((result.status, repo.rows[result.credential_id]["state"]), ("created", "pending"))
        password = service.reveal(result.credential_id)
        self.assertEqual(len(neo.typed), 1)
        self.assertIn(json.dumps(password), neo.typed[0])
        self.assertNotIn(password, json.dumps(result.public()))
        for call in svc.event.call_args_list:
            self.assertNotIn(password, json.dumps(call.args[3], default=str))

    def test_signin_activates_only_on_a_clean_outcome(self) -> None:
        f, service, repo, _ = flow(ScriptedNeo(OK_FILL, {"ok": True}, [GONE]))
        i = service.add_manual(ORIGIN, "me@x.io", "pw-1")["id"]
        repo.rows[i]["state"] = "pending"
        self.assertEqual(f.sign_in("1", ORIGIN).status, "signed_in")
        self.assertEqual(repo.rows[i]["state"], "active")
        bad = {
            "origin": True,
            "passwordFieldVisible": True,
            "errors": ["Invalid email or password"],
            "invalid": 0,
            "verify": False,
        }
        f2, service2, repo2, _ = flow(ScriptedNeo(OK_FILL, {"ok": True}, [bad]))
        j = service2.add_manual(ORIGIN, "me@x.io", "pw-1")["id"]
        repo2.rows[j]["state"] = "pending"
        self.assertEqual(f2.sign_in("1", ORIGIN).status, "rejected")
        self.assertEqual(repo2.rows[j]["state"], "pending")  # a rejected sign-in proves nothing about the account

    def test_unreadable_page_never_marks_the_account_failed(self) -> None:
        unclear = {"origin": True, "passwordFieldVisible": True, "errors": [], "invalid": 0, "verify": False}
        f, _, repo, _ = flow(ScriptedNeo(OK_FILL, OK_SUBMIT, [unclear]))
        result = f.create_account("1", ORIGIN, "me@x.io")
        self.assertEqual((result.status, repo.rows[result.credential_id]["state"]), ("unreadable", "pending"))

    def test_policy_rejection_retries_once_with_the_conservative_symbol_set(self) -> None:
        policy = {
            "origin": True,
            "passwordFieldVisible": True,
            "errors": ["Password must not contain special characters"],
            "invalid": 0,
            "verify": False,
        }
        neo = ScriptedNeo(OK_FILL, OK_SUBMIT, [policy, GONE])
        f, service, repo, _ = flow(neo)
        result = f.create_account("1", ORIGIN, "me@x.io")
        self.assertEqual((result.status, len(neo.typed)), ("created", 2))
        self.assertTrue(
            set(service.reveal(result.credential_id)) - set("-_.")
            <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        )

    def test_other_rejection_fails_the_row(self) -> None:
        taken = {
            "origin": True,
            "passwordFieldVisible": True,
            "errors": ["An account with this email already exists"],
            "invalid": 0,
            "verify": False,
        }
        f, _, repo, _ = flow(ScriptedNeo(OK_FILL, OK_SUBMIT, [taken]))
        result = f.create_account("1", ORIGIN, "me@x.io")
        self.assertEqual((result.status, repo.rows[result.credential_id]["state"]), ("rejected", "failed"))

    def test_unknown_required_consent_stops_the_flow_and_nothing_is_submitted(self) -> None:
        neo = ScriptedNeo({**OK_FILL, "consentPending": 1}, OK_SUBMIT, [GONE])
        f, _, _, _ = flow(neo)
        self.assertEqual(f.create_account("1", ORIGIN, "me@x.io").status, "consent_required")

    def test_wrong_origin_is_refused(self) -> None:
        f, _, _, _ = flow(ScriptedNeo({"ok": False, "reason": "origin_mismatch"}, OK_SUBMIT, [GONE]))
        self.assertEqual(f.create_account("1", ORIGIN, "me@x.io").status, "wrong_page")

    def test_verify_email_page_is_reported(self) -> None:
        verify = {**GONE, "verify": True}
        f, _, repo, _ = flow(ScriptedNeo(OK_FILL, OK_SUBMIT, [verify]))
        result = f.create_account("1", ORIGIN, "me@x.io")
        self.assertEqual((result.status, repo.rows[result.credential_id]["state"]), ("verify_email", "pending"))

    def test_signin_without_a_stored_login_does_nothing(self) -> None:
        f, _, _, _ = flow(ScriptedNeo(OK_FILL, OK_SUBMIT, [GONE]))
        self.assertEqual(f.sign_in("1", ORIGIN).status, "unreadable")


class ScriptTests(unittest.TestCase):
    def test_scripts_embed_values_as_json_and_guard_the_origin(self) -> None:
        text = fill_script(ORIGIN, "signup", 'a"b@x.io', "p'w\\\"")
        self.assertIn('"https://jobs.example"', text)
        self.assertIn('"a\\"b@x.io"', text)
        for script in (text, submit_script(ORIGIN, "signin"), outcome_script(ORIGIN)):
            self.assertIn("location.origin", script)


LIVE = os.environ.get("JOBPILOT_LIVE_NEO") == "1"


@unittest.skipUnless(LIVE, "set JOBPILOT_LIVE_NEO=1 with Neo running to drive a real browser")
class LiveFixtureTests(unittest.TestCase):
    """Real Neo, real DOM, local fixture ATS on 127.0.0.1."""

    def setUp(self) -> None:
        self.neo = NeoBrowser(os.environ.get("NEO_MCP_URL", "http://127.0.0.1:9010/mcp"))
        self.tabs: list[str] = []

    def tearDown(self) -> None:
        for tab in self.tabs:
            self.neo.close_tab(tab)

    def goto(self, tab: str, url: str) -> None:
        self.neo.navigate(tab, url)
        for _ in range(60):
            state = self.neo.evaluate(tab, 'document.readyState + "|" + location.href')
            if "complete|" in state and url in state:
                return
            time.sleep(0.25)
        self.fail(f"page did not load: {url}")

    def run_flows(self, ats: FixtureAts) -> tuple[AccountFlow, Any, FakeRepository, str]:
        f, service, repo, _ = flow(self.neo)
        f._sleep = time.sleep
        f._clock = time.monotonic
        tab = self.neo.open_tab("about:blank").target_id
        self.tabs.append(tab)
        return f, service, repo, tab

    def test_signup_then_signin_round_trip(self) -> None:
        with FixtureAts() as ats:
            f, service, repo, tab = self.run_flows(ats)
            self.goto(tab, ats.origin + "/signup")
            created = f.create_account(tab, ats.origin, "live@example.test")
            self.assertEqual(created.status, "created", created.detail)
            self.assertEqual(ats.accounts["live@example.test"], service.reveal(created.credential_id))
            self.goto(tab, ats.origin + "/signin")
            self.assertEqual(f.sign_in(tab, ats.origin).status, "signed_in")
            self.assertEqual(repo.rows[created.credential_id]["state"], "active")

    def test_policy_retry_and_verify_and_consent_and_wrong_password(self) -> None:
        with FixtureAts(strict_policy=True) as ats:
            f, service, repo, tab = self.run_flows(ats)
            self.goto(tab, ats.origin + "/signup")
            created = f.create_account(tab, ats.origin, "policy@example.test")
            self.assertEqual(created.status, "created", created.detail)
            self.assertEqual(len(ats.attempts), 2)
        with FixtureAts(verify=True) as ats:
            f, service, repo, tab = self.run_flows(ats)
            self.goto(tab, ats.origin + "/signup")
            self.assertEqual(f.create_account(tab, ats.origin, "v@example.test").status, "verify_email")
        with FixtureAts(terms=True) as ats:  # a terms/privacy box is ticked by the account step
            f, service, repo, tab = self.run_flows(ats)
            self.goto(tab, ats.origin + "/signup")
            self.assertEqual(f.create_account(tab, ats.origin, "t@example.test").status, "created")
        with FixtureAts(age_gate=True) as ats:  # any other required box is the owner's
            f, service, repo, tab = self.run_flows(ats)
            self.goto(tab, ats.origin + "/signup")
            self.assertEqual(f.create_account(tab, ats.origin, "t@example.test").status, "consent_required")
            self.assertEqual(ats.attempts, [])
        with FixtureAts() as ats:
            f, service, repo, tab = self.run_flows(ats)
            service.add_manual(ats.origin, "w@example.test", "not-the-password")
            self.goto(tab, ats.origin + "/signin")
            self.assertEqual(f.sign_in(tab, ats.origin).status, "rejected")

    def test_workday_style_page_ticks_privacy_not_marketing_and_clicks_the_overlay(self) -> None:
        with FixtureAts(workday=True) as ats:
            f, service, repo, tab = self.run_flows(ats)
            self.goto(tab, ats.origin + "/signup")
            created = f.create_account(tab, ats.origin, "wd@example.test")
            self.assertEqual(created.status, "created", created.detail)
            self.assertEqual(ats.accounts["wd@example.test"], service.reveal(created.credential_id))

    def test_set_password_page_without_a_username_field_is_filled_by_the_server(self) -> None:
        with FixtureAts() as ats:
            f, service, repo, tab = self.run_flows(ats)
            self.goto(tab, ats.origin + "/set-password")
            created = f.create_account(tab, ats.origin, "setpw@example.test")
            self.assertEqual(created.status, "created", created.detail)
            self.assertEqual(ats.attempts[-1][1], service.reveal(created.credential_id))

    def test_a_tab_on_another_origin_never_receives_the_password(self) -> None:
        with FixtureAts() as ats, FixtureAts() as other:
            f, service, repo, tab = self.run_flows(ats)
            service.add_manual(ats.origin, "x@example.test", "secret-pw-1")
            self.goto(tab, other.origin + "/signin")
            self.assertEqual(f.sign_in(tab, ats.origin).status, "wrong_page")
            self.assertEqual(other.attempts, [])

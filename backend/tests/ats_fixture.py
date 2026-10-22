"""A tiny local applicant-tracking site for exercising account creation and sign-in.

Behaviour switches (constructor): `verify` sends new accounts to a "check your email" page, `terms` adds a required
terms checkbox, `age_gate` adds a required box the account step must not tick, `strict_policy` rejects any password
containing a symbol other than - _ . (the retry set), `workday` mimics Workday's sign-up page: a not-marked-required
privacy checkbox the server insists on, and a submit button whose click handler lives on a transparent
`click_filter` overlay (clicking the button itself does nothing)."""

from __future__ import annotations

import threading
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

PAGE = "<!doctype html><meta charset=utf-8><title>Fixture ATS</title><body>{body}</body>"
SYMBOLS_OK = set("-_.")


class FixtureAts:
    def __init__(
        self,
        *,
        verify: bool = False,
        terms: bool = False,
        age_gate: bool = False,
        strict_policy: bool = False,
        workday: bool = False,
    ) -> None:
        self.verify, self.terms, self.age_gate = verify, terms, age_gate
        self.strict_policy, self.workday = strict_policy, workday
        self.accounts: dict[str, str] = {}
        self.attempts: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:  # silence the test run
                pass

            def _send(self, body: str, status: int = 200, location: str | None = None) -> None:
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(PAGE.format(body=body).encode())

            def do_GET(self) -> None:
                routes = {
                    "/signup": outer.signup_form,
                    "/signin": outer.signin_form,
                    "/set-password": outer.set_password_form,
                    "/verify": lambda: "<h1>Verify your email</h1><p>Check your email for a confirmation link.</p>",
                    "/dashboard": lambda: "<h1>Your applications</h1><a href='/signin'>switch account</a>",
                }
                handler = routes.get(self.path.split("?")[0])
                self._send(handler()) if handler else self._send("not found", 404)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
                path = self.path.split("?")[0]
                if path == "/signup":
                    error = outer.signup(form)
                    if error:
                        return self._send(outer.signup_form(error))
                    return self._send("", 303, "/verify" if outer.verify else "/dashboard")
                if path == "/set-password":
                    if form.get("website"):
                        return self._send("honeypot filled: automated", 400)
                    outer.attempts.append(("", form.get("password", "")))
                    return self._send("", 303, "/dashboard")
                if path == "/signin":
                    error = outer.signin(form)
                    if error:
                        return self._send(outer.signin_form(error))
                    return self._send("", 303, "/dashboard")
                self._send("not found", 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    # --- lifecycle ---------------------------------------------------------------------------------------------------
    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self) -> FixtureAts:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()

    # --- pages and rules ---------------------------------------------------------------------------------------------
    def signup_form(self, error: str = "") -> str:
        terms = "<label><input type=checkbox name=terms required> I accept the terms</label>" if self.terms else ""
        if self.age_gate:
            terms += "<label><input type=checkbox name=age required> I am over 18</label>"
        alert = f"<div role=alert>{escape(error)}</div>" if error else ""
        if self.workday:
            return (
                f"<h1>Create Account</h1>{alert}<form id=f method=post action=/signup>"
                "<input name=email type=text data-automation-id=email>"
                "<input name=password type=password data-automation-id=password>"
                "<input name=confirm type=password data-automation-id=verifyPassword>"
                "<label><input type=checkbox name=terms data-automation-id=createAccountCheckbox> "
                "<span>I have read the privacy notice and agree</span></label>"
                "<label><input type=checkbox name=news> Send me job alerts and marketing updates</label>"
                "<div><button type=button data-automation-id=createAccountSubmitButton>Create Account</button>"
                "<div data-automation-id=click_filter aria-label='Create Account' style='width:20px;height:20px' "
                "onclick=\"document.getElementById('f').requestSubmit()\"></div></div></form>"
            )
        return (
            f"<h1>Create account</h1>{alert}<form method=post action=/signup>"
            "<label>Email <input name=email type=email></label>"
            "<label>Password <input name=password type=password></label>"
            "<label>Confirm password <input name=confirm type=password></label>"
            f"{terms}<button type=submit>Create account</button></form>"
        )

    def set_password_form(self) -> str:
        return (
            "<h1>Set your password</h1><form method=post action=/set-password>"
            "<input name=website type=text tabindex=-1 data-automation-id=beecatcher>"
            "<label>New password <input name=password type=password></label>"
            "<label>Confirm password <input name=confirm type=password></label>"
            "<button type=submit>Set password</button></form>"
        )

    def signin_form(self, error: str = "") -> str:
        alert = f"<div role=alert>{escape(error)}</div>" if error else ""
        return (
            f"<h1>Sign in</h1>{alert}<form method=post action=/signin>"
            "<label>Email <input name=email type=email></label>"
            "<label>Password <input name=password type=password></label>"
            "<button type=submit>Sign in</button></form>"
        )

    def signup(self, form: dict[str, str]) -> str:
        email, password = form.get("email", ""), form.get("password", "")
        self.attempts.append((email, password))
        if password != form.get("confirm"):
            return "Passwords do not match"
        if self.workday and not form.get("terms"):
            return "Please accept the privacy notice"
        if self.workday and form.get("news"):
            return "Marketing consent must not be ticked"
        if self.strict_policy and any(not c.isalnum() and c not in SYMBOLS_OK for c in password):
            return "Password must not contain special characters other than - _ ."
        if email in self.accounts:
            return "An account with this email already exists"
        self.accounts[email] = password
        return ""

    def signin(self, form: dict[str, str]) -> str:
        if self.accounts.get(form.get("email", "")) == form.get("password"):
            return ""
        return "Invalid email or password"

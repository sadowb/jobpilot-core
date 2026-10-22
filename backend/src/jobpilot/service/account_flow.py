"""Server-driven account creation and sign-in on job sites (ADR 0031, building on ADR 0028).

The password comes from `CredentialService` and goes straight into the page through `browser/signin.py`; it is never
returned, logged, put in an event or shown to a model. Correctness rules:

* **Write-ahead.** `create_account` stores the generated password as `pending` before the form is submitted.
* **Fail only on an explicit rejection.** A page the classifier cannot read leaves the row `pending`: an account that may
  exist is never marked failed. Only visible site errors count, and a password-policy error retries once with the
  conservative symbol set.
* **Consent is narrow.** A sign-up ticks only the site's terms/privacy box (never marketing); any other required
  unticked checkbox stops the flow with `consent_required` and the page untouched past the fill.
* **Activation needs proof.** A `pending` login becomes `active` only when a sign-in leaves the form with no error.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from ..browser.neo import BrowserError, BrowserUnavailable, NeoBrowser
from ..browser.signin import Mode, fill_script, outcome_script, submit_script
from ..domain.credentials import RETRY_SYMBOLS, normalize_origin

if TYPE_CHECKING:
    from .application import Services

log = logging.getLogger("jobpilot.account_flow")

Status = Literal[
    "created",  # signup submitted, form gone, nothing contradicts it (the row stays pending until a sign-in works)
    "verify_email",  # the site wants an email code or link before the account works
    "signed_in",  # sign-in worked; the login is now active
    "rejected",  # the site showed an error
    "consent_required",  # an unticked required checkbox; the owner decides
    "unreadable",  # the page did not match; nothing was concluded
    "wrong_page",  # the tab is not on the credential's origin, or the form is not recognisable
]
POLICY_ERROR = re.compile(
    r"password.{0,60}(must|least|character|weak|strong|special|complex|digit|number|uppercase|symbol|invalid)", re.I
)
SETTLE_SECONDS = 10.0
POLL_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class FlowResult:
    status: Status
    credential_id: int | None = None
    detail: str | None = None

    def public(self) -> dict[str, Any]:
        return {"status": self.status, "credentialId": self.credential_id, "detail": self.detail}


class AccountFlow:
    def __init__(
        self,
        svc: Services,
        neo: NeoBrowser,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.svc = svc
        self.neo = neo
        self._sleep = sleep
        self._clock = clock

    # --- public ------------------------------------------------------------------------------------------------------

    def create_account(self, tab_id: str, origin: str, username: str) -> FlowResult:
        """Fill and submit the create-account form open in `tab_id`, with a freshly stored password."""
        key = normalize_origin(origin)
        symbols: str | None = None
        for attempt in (1, 2):
            meta = (
                self.svc.credentials.reserve(key, username)
                if symbols is None
                else self.svc.credentials.reserve(key, username, symbols=symbols)
            )
            found = self.svc.credentials.password_for_signin(key, str(meta["username"]))
            assert found is not None
            credential_id, password = int(meta["id"]), found[1]
            blocked = self._fill_and_submit(tab_id, key, "signup", str(meta["username"]), password)
            if blocked:
                return FlowResult(blocked.status, credential_id, blocked.detail)
            result = self._redact(self._settle(tab_id, key, credential_id, signup=True), password)
            if result.status == "rejected" and attempt == 1 and POLICY_ERROR.search(result.detail or ""):
                symbols = RETRY_SYMBOLS
                continue
            if result.status == "rejected":
                self.svc.credentials.fail(credential_id)
            self.svc.event(None, "account_flow", "system", {"mode": "signup", "origin": key, "status": result.status})
            return result
        raise AssertionError("unreachable")  # pragma: no cover

    def sign_in(self, tab_id: str, origin: str, username: str | None = None) -> FlowResult:
        """Type the stored login for `origin` into the sign-in form in `tab_id`. A worked sign-in activates the row."""
        key = normalize_origin(origin)
        found = self.svc.credentials.password_for_signin(key, username)
        if found is None:
            return FlowResult("unreadable", None, "no stored login for this site")
        meta, password = found
        credential_id = int(meta["id"])
        blocked = self._fill_and_submit(tab_id, key, "signin", str(meta["username"]), password)
        if blocked:
            return FlowResult(blocked.status, credential_id, blocked.detail)
        result = self._redact(self._settle(tab_id, key, credential_id, signup=False), password)
        if result.status == "signed_in":
            self.svc.credentials.activate(credential_id)
        self.svc.event(None, "account_flow", "system", {"mode": "signin", "origin": key, "status": result.status})
        return result

    # --- internals ---------------------------------------------------------------------------------------------------

    @staticmethod
    def _redact(result: FlowResult, password: str) -> FlowResult:
        """Site error text is untrusted and ends up in events and Needs You: it must never echo the password."""
        if result.detail and password in result.detail:
            return FlowResult(result.status, result.credential_id, result.detail.replace(password, "[redacted]"))
        return result

    def _run(self, tab_id: str, script: str) -> dict[str, Any]:
        try:
            data = json.loads(self.neo.evaluate(tab_id, script))
        except (BrowserError, BrowserUnavailable):
            raise
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _fill_and_submit(self, tab_id: str, origin: str, mode: Mode, username: str, password: str) -> FlowResult | None:
        filled = self._run(tab_id, fill_script(origin, mode, username, password))
        if not filled.get("ok"):
            reason = str(filled.get("reason") or "unreadable")
            return FlowResult("unreadable" if reason == "readback_mismatch" else "wrong_page", None, reason)
        if filled.get("consentPending"):
            return FlowResult("consent_required", None, "tick the required terms checkbox yourself, then submit")
        submitted = self._run(tab_id, submit_script(origin, mode))
        if not submitted.get("ok"):
            return FlowResult("wrong_page", None, str(submitted.get("reason") or "no_submit_button"))
        return None

    def _settle(self, tab_id: str, origin: str, credential_id: int, *, signup: bool) -> FlowResult:
        """Poll until the page shows a decision or the time is up. Only explicit site errors count as rejection."""
        deadline = self._clock() + SETTLE_SECONDS
        state: dict[str, Any] = {}
        while True:
            self._sleep(POLL_SECONDS)
            state = self._run(tab_id, outcome_script(origin))
            if state.get("errors") or state.get("invalid"):
                return FlowResult("rejected", credential_id, " | ".join(state.get("errors") or []) or "invalid field")
            if state.get("verify"):
                return FlowResult("verify_email", credential_id)
            if state.get("origin") and not state.get("passwordFieldVisible"):
                return FlowResult("created" if signup else "signed_in", credential_id)
            if self._clock() >= deadline:
                return FlowResult("unreadable", credential_id, "the page did not change")

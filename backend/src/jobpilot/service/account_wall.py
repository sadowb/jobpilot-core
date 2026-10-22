"""Account walls met during an application run (ADR 0031, replaces the save-password card of ADR 0028).

The agent never touches a password. When a site wants an account it fills the non-secret fields, stops and reports
`account_wall: signup|signin`. This service then drives the parked tab itself through `AccountFlow`: the password comes from
the encrypted store, is typed by the server, and is saved before the form is submitted, so there is nothing left for the
owner to save. On success the run is queued again with the account fact in DATA; on anything else the job waits for the
owner with a code-free reason.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from ..browser.neo import BrowserError, BrowserUnavailable, neo_browser
from ..domain.credentials import normalize_origin
from ..domain.state_machine import PRE_SUBMIT_ORDER
from ..repository.browser import BrowserRepository
from ..repository.direct import DirectRepository
from .account_flow import AccountFlow, FlowResult

if TYPE_CHECKING:
    from .application import Services

log = logging.getLogger("jobpilot.account_wall")

MAX_WALL_RUNS = 4  # sign-up, verification sign-in, and one spare per job; more means the site loops
GOOD = frozenset({"created", "verify_email", "signed_in"})
OWNER_REASON = {
    "consent_required": "The site needs you to accept its terms: tick the box in the application tab and press its Create account button",
    "rejected": "The site rejected the account step",
    "unreadable": "JobPilot could not tell whether the account step worked: check the application tab",
    "wrong_page": "JobPilot could not find the account form in the application tab",
}


class AccountWallService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.repository = DirectRepository(svc.db)

    def handle(self, job_id: str, wall: str, *, auto: bool) -> str:
        """Run the account step for a parked tab. Returns `retry` (queue the agent again) or `blocked` (owner has it).
        Browser I/O happens outside any transaction; the outcome is recorded in one short transaction afterwards."""
        result = self._drive(job_id, wall)
        with self.svc.db.transaction():
            self.svc.approvals.repository.lock_job(job_id)
            if self.svc.job(job_id)["status"] not in PRE_SUBMIT_ORDER:
                return "blocked"
            self.svc.require_automation(job_id)
            if auto:
                self.svc.approvals.release_auto_fence(job_id)  # an account step is never the application submit
            self.svc.event(
                job_id, "account_wall", "system", {"wall": wall, "status": result.status, "detail": (result.detail or "")[:200]}
            )
            if result.status in GOOD:
                if wall == "signup" and self.repository.count_events(job_id, "account_created") == 0:
                    self.svc.event(job_id, "account_created", "system", {"method": "generated"})
                return "retry"
            self.svc.wait(job_id, "you", self._reason(result))
            return "blocked"

    def _drive(self, job_id: str, wall: str) -> FlowResult:
        if self.repository.count_events(job_id, "account_wall") >= MAX_WALL_RUNS:
            return FlowResult("unreadable", None, "the site keeps asking for the account step")
        row = BrowserRepository(self.svc.db).find_session_tab(job_id, self.svc.node)
        if not row or not row["target_id"]:
            return FlowResult("wrong_page", None, "the application tab is gone")
        tab = str(row["target_id"])
        neo = neo_browser(self.svc.cfg)
        try:
            origin = normalize_origin(_text(neo.evaluate(tab, "location.origin")))
            if wall == "signup" and self.svc.credentials.has_login(origin):
                wall = "signin"  # never create a second account for a site that already has a stored login
            flow = AccountFlow(self.svc, neo)
            if wall == "signin":
                return flow.sign_in(tab, origin)
            email = str(self.svc.load_known().profile.get("email") or "").strip()
            if not email:
                return FlowResult("unreadable", None, "the profile has no email address")
            created = flow.create_account(tab, origin, email)
            return self._prove_sign_in(flow, tab, origin, created)
        except (BrowserError, BrowserUnavailable, ValueError) as error:
            log.warning("account wall failed job=%s %s", job_id, type(error).__name__)
            return FlowResult("wrong_page", None, "the browser could not be reached")

    @staticmethod
    def _prove_sign_in(flow: AccountFlow, tab: str, origin: str, created: FlowResult) -> FlowResult:
        """A finished sign-up is not an account until a sign-in works. Workday-style sites land on their sign-in form
        right after sign-up, so try it in the same tab before the run is queued again. A rejection here is not a
        failure of the sign-up: the site most likely wants the emailed verification first, which the agent handles."""
        if created.status != "created":
            return created
        proved = flow.sign_in(tab, origin)
        if proved.status == "signed_in":
            return proved
        if proved.status == "rejected":
            return FlowResult("verify_email", created.credential_id, "the site did not accept the new login yet")
        return created  # no sign-in form on this page (it may have signed in by itself): the next run sees the page

    @staticmethod
    def _reason(result: FlowResult) -> str:
        reason = OWNER_REASON.get(result.status, "The account step needs you")
        if result.status == "rejected" and result.detail:
            return f"{reason}: {result.detail[:160]}"
        return reason


def _text(value: str) -> str:
    try:
        parsed = json.loads(value)
    except ValueError:
        return value.strip()
    return parsed if isinstance(parsed, str) else value.strip()

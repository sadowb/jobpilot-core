"""Server-owned preparation and validation of agent-reported direct-mode results."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..boards import entry_url, recipe_text
from ..browser.neo import BrowserError, BrowserUnavailable, neo_browser
from ..browser.tabs import BrowserOffline
from ..domain.answers import JobContext
from ..domain.canonical import norm
from ..domain.cv import CV_FILENAME
from ..domain.form_audit import problems as form_problems
from ..domain.market import market_for
from ..domain.reask import classify_reported
from ..domain.state_machine import PRE_SUBMIT_ORDER
from ..dto.direct import SECRET_KEY, SECRET_VALUE, DirectResult, data_block
from ..projection.vault import job_dir_for
from ..repository.browser import BrowserRepository
from ..repository.direct import DirectRepository
from .application import Services
from .cv_selection import CvSelectionService

ACCOUNT_RULE = (
    'Creating an account or signing in ("Create account", "Sign up", "Register", "Continue with Google", "Sign in") is NOT '
    "the application submit and is allowed, under the account rule in agent/NEO.md: prefer Continue with Google, else stop "
    "and report account_wall; never type, read or report a password."
)

PREPARATION_STOP = (
    'PREPARATION: do not click the button that sends the filled form ("Submit", "Submit application", "Send application", '
    '"Finish", "Complete", or an "Apply" at the end of the filled form). "Apply" / "Apply now" on a posting page with no '
    "form open only opens the form: click it to reach the form. If unsure whether a button submits, do not click it. The owner submits, never you: leave the final button untouched and "
    "return filled when complete (needs_answers if required questions remain). " + ACCOUNT_RULE
)

AUTO_MARK = (
    "AUTO MODE (owner-enabled for this job): you may click the final Submit button exactly once, after every field is filled "
    "and verified. If a required question has no answer in DATA, or anything looks wrong, do not click: return needs_answers "
    "or blocked. After clicking, never click again. " + ACCOUNT_RULE
)

PAGE_LOAD_SECONDS = 20.0
# Application pages are often script-rendered shells: "complete" arrives before the posting text does. Wait for a
# substantial, unchanging text instead of taking the first non-empty one.
POSTING_MIN_CHARS = 300
MIN_POSTING_FOR_SELECTION = 100
PAGE_RETRIES = 3
log = logging.getLogger("jobpilot.direct")
PAGE_TEXT_CHARS = 200_000


NOTE_CHARS = 600
# A run that only re-asks settled questions is retried this many times (per owner Send) before the owner is told.
MAX_REASK_RUNS = 2
REASK_ANSWER_CHARS = 300
REASK_LIMIT = 40
# An auto run blocked by the owner's mailbox or the emailed code: that job is not auto-submitted again (review instead).
_MAIL_FAILURE = re.compile(r"verification|e-?mail code|gmail|mailbox|one-time", re.I)


def _display_note(note: str | None) -> str:
    """Agent text is untrusted display data: one line, control characters removed, length-capped, secret-shaped
    values withheld (the result validator already refuses them; this is the second line of defence)."""
    text = " ".join("".join(c if c.isprintable() else " " for c in (note or "")).split())
    if SECRET_VALUE.search(text):
        return ""
    return text[:NOTE_CHARS]


class DirectService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.repository = DirectRepository(svc.db)

    def ensure_track(
        self, job_id: str, check: Callable[[], None] = lambda: None, started: Callable[[], None] = lambda: None
    ) -> str:
        """Pick the base CV track from the job page before the sandbox profile needs a CV path.

        Returns "ready" (a track or CV already exists, or one was just chosen), "blocked" (the owner was asked) or
        "retry" (the page or compared version changed). The model sees only server-supplied posting and CV text;
        no tools or broader vault access. An explicit owner choice or pinned CV is preserved."""
        job = self.svc.job(job_id)
        if job.get("cv_track_id") or job.get("cv_version_id") or job.get("cv_mode", "base") != "base":
            return "ready"
        # A posting the ATS already published (stored by the pre-browser prefetch or an earlier capture) is the same
        # text the page would show: compare against it and leave the tab closed.
        stored = (self.svc.posting(job_id) or "").strip()
        text: str | None = stored if len(stored) >= POSTING_MIN_CHARS else None
        if text is None:
            self.svc.ensure_tab(job_id)
            row = BrowserRepository(self.svc.db).find_session_tab(job_id, self.svc.node)
            if not row or not row["target_id"]:
                raise ValueError("job tab is missing")
            try:
                text = self._read_page(job_id, row["target_id"], job["url"], check)
            except BrowserUnavailable as error:
                # Only an unreachable Neo is "offline". A page that cannot be read (script shell, evaluate refused,
                # navigation error) says nothing about Neo: the agent reads the page itself, so do not stall on it.
                if not self._neo_reachable():
                    raise BrowserOffline("shared browser is not available") from error
                return self._fallback_track(job_id, f"page_unreadable:{type(error).__name__}", check)
            except BrowserError:
                return self._fallback_track(job_id, "page_unreadable:BrowserError", check)
        if text is None or len(text.strip()) < MIN_POSTING_FOR_SELECTION:
            # The page has not shown a posting (script-rendered shell, slow site). Look again a few times instead
            # of guessing a CV from nothing; after that the owner is told what was seen.
            seen = len((text or "").strip())
            tries = self.repository.count_events(job_id, "cv_select_page_retry")
            if tries < PAGE_RETRIES:
                self.svc.event(job_id, "cv_select_page_retry", "system", {"characters": seen, "try": tries + 1})
                return "retry"
            return self._fallback_track(job_id, f"page_too_short:{seen}", check)
        selector = CvSelectionService(self.svc)
        try:
            selection = selector.choose(job_id, text, check, started)
        except (ValueError, subprocess.TimeoutExpired) as error:
            # Name the cause (the exception text may quote model output, so only its type is recorded).
            selector.decline_reason = f"the comparison failed ({type(error).__name__})"
            log.warning("cv selection failed for %s: %s", job_id, type(error).__name__)
            selection = None
        if selection is not None:
            choice, version = selection
            assert choice.track_id is not None
            return self._assign_track(
                job_id, choice.track_id, version, "model-comparison", choice.summary,
                [item.model_dump() for item in choice.evidence], check,
            )  # fmt: skip
        # No default CV: a wrong CV costs an interview, so the owner is told exactly why there is no choice.
        reason = selector.decline_reason or "the comparison did not produce a choice"
        self.svc.wait(job_id, "you", f"choose a CV for this job: {reason}")
        return "blocked"

    def _neo_reachable(self) -> bool:
        try:
            return bool(self.svc.tab_binder().online())
        except Exception:  # noqa: BLE001 - an unanswerable probe counts as unreachable
            return False

    def _fallback_track(self, job_id: str, reason: str, check: Callable[[], None]) -> str:
        """The page could not be compared, so choose from what the server knows (role, company, location) instead of
        stopping the job: the keyword-best track for the job's market. The browser agent still reads the page itself.
        With no usable track at all the owner is asked, as before."""
        job = self.svc.job(job_id)
        location = job.get("location")
        hint = "\n".join(str(job.get(key) or "") for key in ("role", "company_name", "location"))
        suggestion = self.svc.suggest_track(hint, market_for(location))
        version = self.repository.current_track_version(suggestion[0]) if suggestion else None
        if suggestion is None or version is None:
            self.svc.wait(job_id, "you", f"choose a CV for this job: the job page could not be read ({reason.split(':')[0]})")
            return "blocked"
        self.svc.event(job_id, "cv_select_fallback", "system", {"reason": reason, "track": suggestion[0]})
        return self._assign_track(
            job_id,
            suggestion[0],
            version,
            "fallback-best-match",
            "Chosen from the job title and market: the page could not be read.",
            [],
            check,
        )

    def _assign_track(
        self,
        job_id: str,
        track: str,
        version: int,
        source: str,
        summary: str,
        evidence: list[dict[str, str]],
        check: Callable[[], None],
    ) -> str:
        check()
        with self.svc.db.transaction():
            selected = self.repository.assign_reasoned_track(track, version, job_id)
            if selected:
                current = self.svc.job(job_id)
                self.svc.store.link(
                    job_dir_for(self.svc.cfg.vault, current.get("company_name") or "Unknown", current["role"], job_id),
                    selected["sha256"],
                )
                self.svc.queue_cv_job(job_id)
                self.svc.event(
                    job_id,
                    "cv_track_selected",
                    "system",
                    {
                        "from": None,
                        "track": track,
                        "version": version,
                        "source": source,
                        "summary": summary,
                        "evidence": evidence,
                    },
                )
                self.svc.project(job_id)
            elif not self.svc.job(job_id).get("cv_track_id"):
                return "retry"
        return "ready"

    def _read_page(self, job_id: str, target_id: str, url: str, check: Callable[[], None] = lambda: None) -> str | None:
        """Visible text of the job page. Waits until the page is complete and its text is long enough and has stopped
        changing; at the deadline returns the longest text seen (possibly short), or None if there was none."""
        chrome = neo_browser(self.svc.cfg)
        deadline = time.monotonic() + PAGE_LOAD_SECONDS
        navigated = False
        best, previous = "", None
        while True:
            check()
            state = chrome.page_state(target_id, PAGE_TEXT_CHARS)
            if state.url in ("", "about:blank") and not navigated:
                chrome.navigate(target_id, url)
                navigated = True
            elif state.ready == "complete" and state.text.strip():
                text = state.text[:PAGE_TEXT_CHARS]
                best = max(best, text, key=len)
                if len(text.strip()) >= POSTING_MIN_CHARS and text == previous:
                    return text
                previous = text
            if time.monotonic() >= deadline:
                return best or None
            time.sleep(1.0)

    def cv_path(self, job_id: str) -> Path:
        job = self.svc.job(job_id)
        if not job.get("cv_version_id"):
            if job["cv_mode"] != "base":
                raise ValueError("direct mode needs a selected, prepared CV; prepare the tailored CV first")
            self.svc.cv_use_base(job_id)
        row = self.repository.cv(job_id)
        if not row:
            raise ValueError("no selected CV for this job")
        path = (self.svc.store.object_dir(row["sha256"]) / CV_FILENAME).resolve(strict=True)
        if not path.is_file():
            raise ValueError("selected CV file is missing")
        return path

    def prepare(self, job_id: str, cv: Path, *, prefill: dict[str, Any] | None = None, mode: str = "review") -> str:
        # Asked before ensure_tab: a tab that already existed may still hold the earlier fill.
        parked = self.svc.browser_session_present(job_id)
        self.svc.ensure_tab(job_id)
        row = BrowserRepository(self.svc.db).find_session_tab(job_id, self.svc.node)
        if not row or not row["target_id"]:
            raise ValueError("job tab is missing")
        job = self.svc.job(job_id)
        data = {
            "job_id": job_id,
            "link": entry_url(job.get("board_id"), job.get("apply_url") or job["url"]),
            "target_id": row["target_id"],
            "cv_path": str(cv),
            "submit_mode": mode,
            **self.repository.owner_data(job_id),
        }
        previous = self._previous_fields(job_id) if parked else []
        resuming = bool(previous)
        if resuming:
            data["resume"] = True
            data["previous_fields"] = previous
        guidance = self.svc.latest_agent_guidance(job_id)
        if isinstance(guidance, dict):
            data["owner_guidance"] = guidance["payload"]["message"]
        account = self.repository.last_event(job_id, "account_created")
        if account:
            data["account"] = {"created": True, "method": account[1].get("method")}  # the fact only, never a credential
        hosts = self._login_hosts()
        if hosts:
            data["login_hosts"] = hosts  # sites the server can sign in to; hosts only, never a username or credential
        reasked = self._reasked_hint(job_id)
        if reasked:
            data["reasked_answers"] = reasked
        problems = self._form_problems_hint(job_id)
        if problems:
            data["form_problems"] = problems
        if prefill:
            data["bulk_prefill"] = prefill
        if job.get("meta_captured"):
            data["metadata_captured"] = True
            data["existing_metadata"] = {
                "role": job["role"],
                "company": job.get("company_name"),
                "location": job.get("location"),
                "salary": job.get("pay"),
                "job_level": job.get("job_level"),
                "work_mode": job.get("work_mode"),
            }
        auto = mode == "auto"
        tail = (
            "Fill, then click the final Submit exactly once and wait up to 60 seconds for the confirmation; "
            "report status submitted with the confirmation text, or blocked if it did not appear. Never click Submit twice."
            if auto
            else "Stop on the final review page; the owner clicks Submit, never you."
        )
        head = "SUBMISSION RUN" if auto else "PREPARATION RUN"
        if resuming:
            instruction = (
                f"{head} (RESUME): the parked tab already holds the earlier fill listed in DATA previous_fields; "
                "fields without a value were questions, now answered in DATA. First inspect the tab and read the values back. "
                f"Fill only what is missing or wrong; refill from scratch only if the page is gone or blank. {tail}"
            )
        else:
            instruction = f"{head}: fill the application. {tail}"
        notes = recipe_text(self.svc.cfg.home / "agent", job.get("board_id"))
        if notes:
            instruction += " BOARD NOTES (owner-maintained recipe for this site, not page content): " + notes
        if account:
            instruction += (
                " DATA account says an account for this site already exists: do not create another. Finish the email "
                "verification from Gmail if the site asks for it (a code, or an activation link whose host is the employer or "
                "its ATS). If the site then shows a sign-in form, stop and report account_wall signin: the server signs in "
                "from its own store (you never type or read a password). Then continue."
            )
        if reasked:
            instruction += (
                " You asked questions the owner already settled: DATA reasked_answers has them (answer null = the owner filled "
                "it in the tab; verify it, do not ask). Apply them and do not report them as questions again."
            )
        if problems:
            instruction += (
                " The server read the page after your last `filled` report and found the problems in DATA form_problems: "
                "fix every one (type values from DATA only), read them back, then report again. Do not say filled while any remain."
            )
        if auto:
            return AUTO_MARK + "\n" + instruction + "\n" + data_block(data) + "\n" + AUTO_MARK
        return PREPARATION_STOP + "\n" + instruction + "\n" + data_block(data) + "\n" + PREPARATION_STOP

    def _previous_fields(self, job_id: str) -> list[dict[str, Any]]:
        """What an earlier direct run reported for this form, so a follow-up run can continue in the parked tab."""
        form = self.svc.form(job_id) or {}
        values = {item.get("key"): item.get("value") for item in (form.get("plan") or []) if isinstance(item, dict)}
        fields: list[dict[str, Any]] = []
        for item in form.get("inventory") or []:
            if not isinstance(item, dict) or not item.get("reported") or SECRET_KEY.search(str(item.get("label", ""))):
                continue
            entry: dict[str, Any] = {"label": item.get("label"), "kind": item.get("type"), "required": item.get("required")}
            if item.get("options"):
                entry["options"] = item["options"]
            key = f"page:0:{item.get('key')}"
            if key in values:
                entry["value"] = values[key]
            fields.append(entry)
        return fields

    def page_text(self, job_id: str) -> str:
        row = BrowserRepository(self.svc.db).find_session_tab(job_id, self.svc.node)
        if not row or not row["target_id"]:
            raise ValueError("job tab is missing")
        return neo_browser(self.svc.cfg).page_text(row["target_id"])

    def apply(self, job_id: str, result: DirectResult, *, auto: bool = False) -> str:
        """Record a run's result. `auto` means this run was started in auto mode (ADR 0026): it was allowed to click
        the final Submit, so a `submitted` report is the expected outcome and the fence it wrote stays unless the agent
        proves it stopped before clicking (`filled` / `needs_answers`). In a review run `submitted` is an unauthorized
        submission: recorded, automation stopped, owner told."""
        with self.svc.db.transaction():
            self.svc.approvals.repository.lock_job(job_id)
            if self.svc.job(job_id)["status"] not in PRE_SUBMIT_ORDER:
                return "done"
            # Re-check the lease before any writes; model output has no authority over job identity or state.
            self.svc.require_automation(job_id)
            if result.verification is not None:
                self.svc.event(job_id, "email_verification_used", "system", result.verification.model_dump())
            if result.account is not None and result.account.created:
                self._record_account(job_id)
            if result.status == "submitted":
                if not auto:
                    self.svc.approvals.record_agent_submitted(job_id, result.confirmation or "")
                    return "blocked"
                draft = self._store_form(job_id, result)
                self.svc.approvals.record_auto_submitted(job_id, draft, result.confirmation or "")
                return "done"
            if auto:
                if result.status == "blocked":
                    # The agent may have clicked before the page blocked it: the fence stays and the owner checks the tab.
                    note = _display_note(result.note)
                    reason = "submission uncertain: check the parked application before continuing"
                    self.svc.event(
                        job_id, "direct_result", "system", {"status": "blocked", "auto": True, "fields": len(result.fields)}
                    )
                    self.svc.wait(job_id, "you", f"{reason}. Agent note: {note}" if note else reason)
                    return "blocked"
                self.svc.approvals.release_auto_fence(job_id)  # filled / needs_answers: nothing was clicked
            if self.svc.submit_clicked(job_id):
                raise PermissionError("submission already begun; confirm only")
            if result.status == "blocked":
                if _MAIL_FAILURE.search(result.note or ""):
                    self.svc.event(job_id, "email_verification_failed", "system", {})
                self.svc.wait(job_id, "you", result.note or "agent needs your help")
                return "blocked"
            skipped = self.repository.skipped_question_keys(job_id)
            if skipped:
                kept = [q for q in result.questions if norm(q.label) not in skipped]
                result = result.model_copy(update={"questions": kept})
                if result.status == "needs_answers" and not kept:
                    result = result.model_copy(update={"status": "filled"})
            meta = self._store_meta(job_id, result)
            if not meta["ok"] or meta.get("duplicateOf"):
                return "blocked"
            if result.status == "needs_answers":
                result, settled = self._drop_settled(job_id, result)
                if settled and not result.questions:
                    if self.repository.count_events_since(job_id, "answers_reasked", "resume") < MAX_REASK_RUNS:
                        self._record_reasked(job_id, settled, retry=True)
                        return "reask"
                    self.svc.wait(
                        job_id,
                        "you",
                        "the agent keeps asking questions you already answered and could not apply the saved answers: "
                        "fill them in the tab, then press 'I filled it in the tab myself'",
                    )
                    return "blocked"
                self._store_inventory(job_id, result)
                if settled:
                    self._record_reasked(job_id, settled, retry=False)  # after the inventory: its direct_result event is older
                self.svc.wait(job_id, "you", result.note or "answer the missing application questions")
                return "blocked"
            self._store_form(job_id, result, meta_done=True)
            self.svc.transition(job_id, "awaiting_approval")
            self.svc.wait(
                job_id,
                "you",
                "review the filled application in Neo, submit it yourself, then confirm here",
            )
            return "blocked"

    def _login_hosts(self) -> list[str]:
        try:
            return self.svc.credentials.stored_hosts()
        except Exception:  # noqa: BLE001 - the store is optional help; a run never fails on it
            log.warning("login hosts unavailable")
            return []

    def _record_account(self, job_id: str) -> None:
        """The agent says it signed in with the owner's Google account (ADR 0028). Records the fact once. Accounts the server
        creates are recorded by `AccountWallService` (ADR 0031)."""
        if self.repository.count_events(job_id, "account_created") == 0:
            self.svc.event(job_id, "account_created", "system", {"method": "sso"})

    def audit_filled(self, job_id: str) -> list[str]:
        """Read-only check of the parked tab before a `filled` report is believed: required fields still empty, visible
        validation errors, an email that is not the owner's. Nothing is written and no agent runs. Skipped (empty) when
        the owner has since said they completed the page, or when Neo cannot be read: a failed check never blocks."""
        if self.repository.count_events_since(job_id, "owner_completed", "form_incomplete") > 0:
            return []
        try:
            row = BrowserRepository(self.svc.db).find_session_tab(job_id, self.svc.node)
            if not row or not row["target_id"]:
                return []
            audit = neo_browser(self.svc.cfg).form_audit(str(row["target_id"]))
        except (BrowserError, BrowserUnavailable, BrowserOffline, ValueError) as error:
            log.warning("form audit unavailable job=%s %s", job_id, type(error).__name__)
            return []
        return form_problems(audit, self.svc.load_known().profile.get("email"))

    def form_retry_available(self, job_id: str) -> bool:
        """One automatic retry per owner Send/Resume; after that the owner sees the list."""
        return self.repository.count_events_since(job_id, "form_incomplete", "resume") == 0

    def _form_problems_hint(self, job_id: str) -> list[str]:
        flagged = self.repository.last_event(job_id, "form_incomplete")
        if not flagged:
            return []
        reported = self.repository.last_event(job_id, "direct_result")
        if reported and reported[0] > flagged[0]:
            return []
        items = flagged[1].get("problems")
        return [str(item) for item in items] if isinstance(items, list) else []

    def _drop_settled(self, job_id: str, result: DirectResult) -> tuple[DirectResult, list[dict[str, Any]]]:
        """Remove reported questions the owner already settled (saved answer, profile value, or filled in the tab).
        The agent keys questions per run, so the match is on the normalised label, like the answer bank."""
        job = self.svc.job(job_id)
        reported = [{"label": q.label, "kind": q.kind, "options": q.options} for q in result.questions]
        ctx = JobContext(
            job.get("company_name") or "",
            job.get("role") or "",
            job.get("apply_url") or job.get("url") or "",
            job.get("location") or "",
        )
        found = classify_reported(
            reported,
            self.svc.load_known(),
            job_id,
            job.get("company_slug") or "",
            ctx,
            self.repository.owner_completed_labels(job_id),
        )
        if not found.settled:
            return result, []
        keep = [result.questions[i] for i in found.new]
        settled: list[dict[str, Any]] = []
        for _, item in found.settled[:REASK_LIMIT]:
            entry: dict[str, Any] = {"label": item.label[:200], "answer": None, "source": item.source}
            if item.value is not None and not SECRET_KEY.search(item.label):
                entry["answer"] = item.value[:REASK_ANSWER_CHARS]
            settled.append(entry)
        return result.model_copy(update={"questions": keep}), settled

    def _record_reasked(self, job_id: str, settled: list[dict[str, Any]], *, retry: bool) -> None:
        self.svc.event(job_id, "answers_reasked", "system", {"settled": settled, "retry": retry})

    def _reasked_hint(self, job_id: str) -> list[dict[str, Any]]:
        """Answers for questions the previous run re-asked: only until the next run reports (its direct_result is newer)."""
        reasked = self.repository.last_event(job_id, "answers_reasked")
        if not reasked:
            return []
        reported = self.repository.last_event(job_id, "direct_result")
        if reported and reported[0] > reasked[0]:
            return []
        items = reasked[1].get("settled")
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    def _store_meta(self, job_id: str, result: DirectResult) -> dict[str, Any]:
        return self.svc.set_meta(
            job_id,
            role=result.role,
            company=result.company,
            location=result.location,
            pay=result.salary,
            job_level=result.job_level,
            work_mode=result.work_mode,
            employment_type=result.employment_type,
            posting=result.description,
        )

    def _store_inventory(self, job_id: str, result: DirectResult) -> None:
        inventory: list[dict[str, Any]] = []
        plan: list[dict[str, Any]] = []
        missing: list[str] = []
        for index, field in enumerate([*result.fields, *result.questions]):
            key = f"reported:{index}"
            inventory.append(
                {
                    "key": key,
                    "label": field.label,
                    "type": field.kind,
                    "required": field.required,
                    "options": field.options,
                    "reported": True,
                }
            )
            namespaced = f"page:0:{key}"
            if index < len(result.fields):
                value = result.fields[index].value
                if value is not None:
                    plan.append({"key": namespaced, "value": value, "source": "agent"})
            else:
                missing.append(namespaced)
        self.svc.set_inventory(job_id, inventory, page=0, complete=result.status in ("filled", "submitted"))
        self.repository.plan(job_id, plan, missing)
        self.svc.event(
            job_id,
            "direct_result",
            "system",
            {"status": result.status, "fields": len(result.fields), "questions": len(result.questions)},
        )

    def _store_form(self, job_id: str, result: DirectResult, *, meta_done: bool = False) -> str:
        """Persist the reported form and its draft hash (what the owner or the auto-mode approval is bound to)."""
        if not meta_done:
            self._store_meta(job_id, result)  # a duplicate posting cannot stop a submission that already happened
        self._store_inventory(job_id, result)
        draft = hashlib.sha256(
            json.dumps(
                {"fields": result.model_dump()["fields"], "cvVersion": self.svc.job(job_id)["cv_version_id"]},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        self.svc.record_fill(job_id, 0)
        self.svc.record_verification(
            job_id,
            draft,
            {"ok": True, "draftHash": draft, "verification": "agent-reported", "fields": len(result.fields)},
        )
        return draft

"""Jobs operations behind the shared Services facade."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any

from ..boards import adapter_by_id, cap_policy, resolve_board
from ..domain.canonical import norm, pacing_hostname, slug
from ..domain.cv import (
    keyword_score,
)
from ..domain.ids import JobIdentity, url_hash
from ..domain.ids import job_id as mint_job_id
from ..domain.market import market_for, market_matches
from ..domain.next_step import JobFacts, next_step
from ..domain.state_machine import PRE_SUBMIT_ORDER, QUESTION_STATES, GateContext, can_transition
from ..repository.database import Row
from ..repository.jobs import JobsRepository
from ..repository.preflight import PreflightRepository

if TYPE_CHECKING:
    from .application import Services


class JobsService:
    def __init__(self, application: Services) -> None:
        self.application = application
        self.repository = JobsRepository(application.db)

    def upsert_company(self, name: str) -> int:
        r = self.repository.insert_companies_for_upsert_company(slug(name), name)
        assert r
        return int(r["id"])

    def ingest(
        self, url: str, cv_track: str | None = None, cv_mode: str = "base", company: str | None = None, role: str | None = None
    ) -> dict[str, Any]:
        adapter, m = resolve_board(url)
        jid = mint_job_id(JobIdentity(m.canonical_url, adapter.id, m.tenant, m.requisition_id))
        # Serialize concurrent ingestion of the same canonical identity.
        self.repository.find_transaction_lock_for_ingest(jid)
        h = url_hash(m.canonical_url)
        dup = self.repository.find_jobs_for_ingest(jid, h, adapter.id, m.tenant, m.requisition_id, m.requisition_id)
        if dup:
            self.repository.insert_tasks_for_ingest(dup["id"])
            return {"id": dup["id"], "created": False, "board": adapter.id, "automation": adapter.automation}
        self.repository.insert_boards_for_ingest(adapter.id, adapter.display_name, adapter.automation)
        host = (url.split("/")[2] if "//" in url else url).split(":")[0]
        # A bare IP/localhost is not a company; a public hostname is only a placeholder until the posting is read.
        company_name = company or m.tenant or ("Unknown company" if re.fullmatch(r"[\d.]+|localhost|", host) else host)
        cid = self.application.upsert_company(company_name)
        role_name = role or "(role not captured yet)"
        self.repository.insert_jobs_for_ingest(
            jid,
            cid,
            role_name,
            m.canonical_url,
            h,
            adapter.id,
            m.tenant,
            m.requisition_id,
            norm(company_name),
            norm(role or ""),
            cv_track,
            cv_mode,
            pacing_hostname(m.canonical_url, m.tenant),
        )
        self.application.event(jid, "ingest", "system", {"url": m.canonical_url, "board": adapter.id})
        PreflightRepository(self.application.db).enqueue(jid)
        self.application.enqueue(jid)
        return {"id": jid, "created": True, "board": adapter.id, "automation": adapter.automation}

    def enqueue(self, job_id: str) -> None:
        if self.job(job_id)["status"] == "awaiting_approval":
            # The claim never picks these up (ADR 0026): queueing one would be a silent no-op for the owner.
            raise PermissionError("This application is waiting for you to submit; send it back to the agent first.")
        self.repository.insert_tasks_for_enqueue(job_id)
        self.application.request_dispatch()  # after commit; ingest, approve_submit, resume and tracker all pass through here

    def job(self, job_id: str) -> Row:
        r = self.repository.find_job_view(job_id)
        if not r:
            raise LookupError(f"unknown job {job_id}")
        return r

    def _live(self, job_id: str) -> Row:
        """The job, for a write: unknown -> LookupError (404), deleted -> PermissionError (409)."""
        row = self.repository.find_jobs_for_live(job_id)
        if not row:
            raise LookupError(f"unknown job {job_id}")
        if row["archived_at"] is not None:
            raise PermissionError("this application is deleted; restore it first")
        return row

    def posting(self, job_id: str) -> str:
        row = self.repository.find_jobs_for_posting(job_id)
        return (row["posting_text"] or "") if row else ""

    def form(self, job_id: str) -> Row | None:
        return self.repository.find_job_forms_for_form(job_id)

    def approval_for(self, job_id: str, draft_hash: str | None) -> str | None:
        if not draft_hash:
            return None
        r = self.repository.find_approvals_for_approval_for(job_id)
        return r["draft_hash"] if r else None

    def board_policy(self, board_id: str | None) -> str:
        if not board_id:
            return "assisted"
        r = self.repository.find_boards_for_board_policy(board_id)
        return cap_policy(board_id, r["automation"] if r else adapter_by_id(board_id).automation)

    def _order_state(self, job_id: str) -> tuple[Row, Row | None, JobFacts]:
        j = self.repository.find_order_state_job(job_id)
        if not j:
            raise LookupError(f"unknown job {job_id}")
        # One form load with indexed scalar lookups, regardless of the workflow step.
        f = self.repository.find_approvals_for_order_state(job_id, job_id, j["board_id"], job_id)
        assert f
        policy = cap_policy(j["board_id"], f["automation"] or adapter_by_id(j["board_id"]).automation)
        facts = JobFacts(
            status=j["status"],
            waiting_on=j["waiting_on"],
            meta_captured=bool(j["meta_captured"]),
            external=j["source"] != "platform",
            external_complete=not self.application.external_missing(j) if j["source"] != "platform" else True,
            agent_reported=any(field.get("reported") is True for field in (f["inventory"] or [])),
            has_inventory=bool(f["inventory"]),
            has_plan=f["plan"] is not None,
            missing_answers=len(f["missing"] or []),
            cv_attached=j["cv_version_id"] is not None,
            fill_done=bool(f["fill_done_at"]),
            verified=bool(f["verified_at"]),
            submit_clicked=bool(f["clicked"]),
            automation=policy,
            pages_complete=bool(f["pages_complete"]),
        )
        return j, f if f["job_id"] else None, facts

    def facts(self, job_id: str) -> JobFacts:
        return self.application._order_state(job_id)[2]

    def status(self, job_id: str) -> dict[str, Any]:
        j, _, facts = self.application._order_state(job_id)
        return {"job": job_id, "status": j["status"], "waiting": j["waiting_on"], "step": next_step(facts)}

    def done(self, job_id: str, worker: str) -> dict[str, bool]:
        self.repository.lock_job(job_id)
        if next_step(self.application.facts(job_id)) != "idle":
            raise PermissionError("done gate closed: work or human approval/confirmation remains")
        lease = self.repository.find_tasks_for_done(job_id, worker)
        if not lease:
            raise PermissionError("job lease lost")
        self.application.release(job_id, worker, "done")
        return {"ok": True}

    def context(self, job_id: str) -> dict[str, Any]:
        j, f, facts = self.application._order_state(job_id)
        return {
            "id": j["id"],
            "company": j["company_name"],
            "role": j["role"],
            "url": j["url"],
            "board": j["board_id"],
            "status": j["status"],
            "waiting_on": j["waiting_on"],
            "blocked_reason": j["blocked_reason"],
            "cv": {"track": j["cv_track_id"], "mode": j["cv_mode"], "attached": j["cv_version_id"] is not None},
            "form": {
                "fields": len(f["inventory"]) if f and f["inventory"] else 0,
                "planned": len(f["plan"]) if f and f["plan"] else 0,
                "missing": (f["missing"] if f else []) or [],
                "filled": bool(f and f["fill_done_at"]),
                "verified": bool(f and f["verified_at"]),
            },
            "next": next_step(facts),
            "choices": self.application._planned_choices(f),
        }

    def set_meta(
        self,
        job_id: str,
        *,
        company: str | None = None,
        role: str | None = None,
        location: str | None = None,
        work_mode: str | None = None,
        pay: str | None = None,
        deadline: str | None = None,
        posting: str | None = None,
        job_level: str | None = None,
        employment_type: str | None = None,
        company_description: str | None = None,
    ) -> dict[str, Any]:
        from ..clean import sanitize_fields  # lazy: clean imports the worker package, which imports this module

        clean = sanitize_fields(
            {
                "company": company, "role": role, "location": location, "work_mode": work_mode, "pay": pay,
                "job_level": job_level, "employment_type": employment_type, "company_description": company_description,
            }
        )  # fmt: skip
        company, role, location, work_mode, pay = (
            clean["company"],
            clean["role"],
            clean["location"],
            clean["work_mode"],
            clean["pay"],
        )
        job_level, employment_type, company_description = (
            clean["job_level"],
            clean["employment_type"],
            clean["company_description"],
        )
        if role and role.strip().lower().startswith("(role not captured"):
            role = None  # echoing the placeholder back is not a captured title
        j = self.application.job(job_id)
        cid = self.application.upsert_company(company) if company else None
        posting_text = posting if posting is not None else self.application.posting(job_id)
        sha = hashlib.sha256(posting_text.encode()).hexdigest() if posting_text else None
        co = company or j["company_name"] or ""
        ro = role or j["role"]
        loc = location or j["location"] or ""
        fuzzy = self.repository.find_jobs_for_set_meta(job_id, norm(co), norm(ro), norm(loc))
        self.repository.update_jobs_for_set_meta(
            cid, ro, location, work_mode, pay, deadline, posting_text, sha, norm(co), norm(ro), norm(loc), job_id
        )
        if job_level or employment_type:
            self.repository.update_job_classification(job_level, employment_type, job_id)
        if company_description and cid:
            self.repository.update_companies_for_set_meta(company_description, cid)
        # Direct preparation selects from actual CV evidence before upload. Metadata
        # capture must not install a keyword fallback or overwrite its fit assessment.
        reasoned = j.get("source", "platform") == "platform"
        sug = None if reasoned else self.application.suggest_track(posting_text or "", market_for(location or j["location"]))
        cur = self.application.job(job_id)
        if sug:
            if not cur["cv_track_id"]:
                self.repository.set_suggested_cv_track(sug[0], sug[1], job_id)
            else:
                self.repository.update_match_score(sug[1], job_id)
        self.application.event(
            job_id, "meta", "agent", {"company": co, "role": ro, "duplicateOf": fuzzy["id"] if fuzzy else None}
        )
        if sha and cur["status"] == "discovered":
            self.application.transition(job_id, "screened")
        if fuzzy:
            self.application.wait(job_id, "you", f"possible duplicate of {fuzzy['id']}")
        self.application.project(job_id)
        return {"ok": True, "duplicateOf": fuzzy["id"] if fuzzy else None, "suggestedTrack": sug[0] if sug else None}

    def suggest_track(self, posting: str, market: str = "us") -> tuple[str, int] | None:
        """Best keyword match among tracks written for this job's market (or market-agnostic ones)."""
        rows = self.repository.list_cv_tracks_for_suggest_track()
        pool = [t for t in rows if market_matches(t["market"], market)] or rows
        best: tuple[str, int] | None = None
        for t in pool:
            score = keyword_score(posting, list(t["keywords"] or []))
            if best is None or score > best[1]:
                best = (t["id"], score)
        return best

    def wait(self, job_id: str, who: str, reason: str) -> None:
        if not self.repository.update_jobs_for_wait(who, None if who == "agent" else reason, job_id):
            return
        self.application.event(job_id, "wait", "agent" if who == "you" else "system", {"who": who, "reason": reason})
        self.application.project(job_id)
        if who in ("you", "site"):
            self.application._defer_close(job_id)

    def resume(self, job_id: str, *, try_anyway: bool = False) -> None:
        self.repository.lock_job(job_id)
        job = self.application._live(job_id)
        if self.application.submit_clicked(job_id):
            raise PermissionError("submission already begun; confirm only")
        if job["status"] not in PRE_SUBMIT_ORDER:
            raise PermissionError(f"This application is already {job['status']}; there is nothing left to resume.")
        if job["status"] == "awaiting_approval" and not try_anyway:
            # Nothing claims a job at awaiting_approval (the owner submits it): "send it back" must move it first, or the
            # queued task would sit unclaimed forever. Submit already begun is refused inside reopen_parked.
            self.reopen_parked(job_id, reason="owner resume")
            return
        if try_anyway:
            if job["status"] not in QUESTION_STATES:
                raise PermissionError("This application is no longer waiting for answers.")
            self.repository.allow_unknown_questions(job_id)
            self.application.event(job_id, "questions_try_anyway", "user")
        elif self.application.required_open(job_id):
            self.application.wait(job_id, "you", "answer the missing application questions")
            return
        if job.get("waiting_on") == "agent":
            return  # repeated Send does not enqueue another run
        if self.repository.find_unsent_answers(job_id):
            try:  # answers were saved without resuming: plan again from them now
                self.application.invalidate_form(job_id, reset_plan=True)
            except PermissionError:
                pass  # submission already began; the plan is not re-derived
        self.repository.update_jobs_for_resume(job_id)
        self.application.enqueue(job_id)
        self.application.event(job_id, "resume", "user")
        self.application.project(job_id)

    def reopen_parked(self, job_id: str, reason: str = "reopened") -> None:
        """Send a parked application back to the agent: the owner closed the tab, or says it is not finished or gave new
        guidance. The earlier draft and any unconsumed approval are dropped; a kept tab is reused by the resume run, a
        missing one is opened fresh. Refused once a submit began, so this can never produce a second application."""
        self.repository.lock_job(job_id)
        job = self.application._live(job_id)
        if job["status"] != "awaiting_approval":
            raise PermissionError("This application is not waiting for you to submit.")
        if self.application.submit_clicked(
            job_id
        ) or self.application.approvals.repository.find_applications_for_record_submitted(job_id):
            raise PermissionError("submission already begun; confirm only")
        self.application.invalidate_form(job_id)  # drops the draft and approvals; moves the job back to preparing
        if self.application.job(job_id)["status"] == "awaiting_approval":
            self.application.transition(job_id, "preparing", actor="user")
        self.repository.update_jobs_for_resume(job_id)
        self.application.enqueue(job_id)
        self.application.event(job_id, "resume", "user", {"reopened": True, "reason": reason})
        self.application.project(job_id)

    def set_submit_mode(self, job_id: str, mode: str | None) -> None:
        """Per-job override (review / auto / None = use the default). Affects runs that start afterwards."""
        self.repository.lock_job(job_id)
        job = self.application._live(job_id)
        if self.application.submit_clicked(job_id) or job["status"] not in PRE_SUBMIT_ORDER:
            raise PermissionError("This application is already submitted or being submitted.")
        previous = self.application.job(job_id).get("submit_mode")
        if previous == mode:
            return
        self.repository.update_jobs_for_submit_mode(job_id, mode)
        self.application.event(job_id, "mode_changed", "user", {"scope": "job", "from": previous, "to": mode})
        self.application.project(job_id)

    def acknowledge_unapproved_submit(self, job_id: str) -> None:
        """Owner checked an application the agent submitted without approval. Idempotent; automation stays stopped."""
        self.repository.lock_job(job_id)
        self.application._live(job_id)
        if self.repository.find_job_events_for_kind(job_id, "unapproved_submit_acknowledged"):
            return
        if not self.repository.find_job_events_for_kind(job_id, "unapproved_submit_reported"):
            raise PermissionError("This application has no unapproved submission to check.")
        self.repository.update_for_acknowledge_unapproved_submit(job_id)
        self.application.event(job_id, "unapproved_submit_acknowledged", "user")
        self.application.project(job_id)

    def transition(self, job_id: str, to: str, ctx: GateContext | None = None, actor: str = "system") -> None:
        self.repository.lock_job(job_id)
        j = self.application.job(job_id)
        if actor == "user":
            self.application._live(job_id)
        r = can_transition(j["status"], to, ctx)
        if not r.ok:
            self.application.event(job_id, "transition_refused", actor, {"from": j["status"], "to": to, "reason": r.reason})
            raise PermissionError(r.reason)
        self.repository.update_jobs_for_transition(to, job_id)
        if to not in QUESTION_STATES:
            self.repository.update_job_forms_clear_missing(job_id)
        self.application.event(job_id, "transition", actor, {"from": j["status"], "to": to})
        self.application.project(job_id)
        if to not in PRE_SUBMIT_ORDER:
            self.application._defer_close(job_id)
            self.application.cleanup_agent_session(job_id)

    def set_job_settings(self, job_id: str, track: str | None, mode: str | None) -> None:
        self.application._live(job_id)
        if track is not None and not self.repository.find_cv_tracks_for_set_job_settings(track):
            raise LookupError(f"unknown CV track {track}")
        changed = self.repository.update_jobs_for_set_job_settings(track, mode, job_id, job_id)
        if not changed:
            raise PermissionError("job settings cannot change after submission begins")
        self.application.invalidate_form(job_id)
        self.application.event(job_id, "job_settings", "user", {"track": track, "mode": mode})
        self.application.resume(job_id)

"""Forms operations behind the shared Services facade."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from ..domain.answers import SKIPPED, JobContext, Known, build_plan
from ..domain.canonical import norm
from ..domain.canonical_answers import canonical_for, choose_option
from ..domain.forms import page_fields
from ..domain.state_machine import PRE_SUBMIT_ORDER, QUESTION_STATES
from ..dto.direct import SECRET_KEY
from ..repository.database import J
from ..repository.forms import FormsRepository

if TYPE_CHECKING:
    from .application import Services


OWNER_COMPLETED_GUIDANCE = (
    "The owner completed the pending questions directly on the page; do not ask about them again. Verify the fields are "
    "filled, then continue to the next step or the final review page."
)


class FormsService:
    def __init__(self, application: Services) -> None:
        self.application = application
        self.repository = FormsRepository(application.db)

    def set_inventory(
        self, job_id: str, fields: list[dict[str, Any]], *, page: int | None = None, complete: bool = False
    ) -> None:
        self.repository.lock_job(job_id)
        if self.application.submit_clicked(job_id) or self.application.job(job_id)["status"] not in PRE_SUBMIT_ORDER:
            raise PermissionError("inventory cannot change after submission begins")
        form = self.application.form(job_id)
        page = int(form["current_page"]) if page is None and form else page or 0
        prior = [f for f in (form["inventory"] or []) if f.get("pageIndex", 0) < page] if form else []
        previous = {f["key"]: f for f in (form or {}).get("inventory") or []}
        fields = prior + page_fields(fields, page)
        for field in fields:
            old = previous.get(field["key"], {})
            if field.get("type") == "custom-select" and old.get("label") == field.get("label"):
                for key in ("suggestions", "unmatchedValue"):
                    if key in old:
                        field[key] = old[key]
        self.application.invalidate_form(job_id)
        self.repository.insert_job_forms_for_set_inventory(job_id, J(fields), page, complete)
        self.application.event(job_id, "inventory", "system", {"fields": len(fields), "page": page, "complete": complete})
        if self.application.job(job_id)["status"] in ("discovered", "screened"):
            self.application.transition(job_id, "preparing")

    def load_known(self) -> Known:
        profile: dict[str, str] = {}
        lists: dict[str, list[Any]] = {}
        for r in self.repository.list_profile_for_load_known():
            v = r["value"]
            if isinstance(v, str) and v.strip():  # sections (lists) and empty values are never form answers
                profile[r["key"]] = v
            elif isinstance(v, list):
                lists[r["key"]] = v
        answers: dict[str, dict[str, str]] = {}
        for r in self.repository.list_answers_for_load_known():
            answers.setdefault(r["question_key"], {})[r["scope"]] = r["answer"]
        citizenships = tuple(
            str(e.get("country")) for e in lists.get("citizenships", []) if isinstance(e, dict) and e.get("country")
        )
        employers = tuple(str(e.get("company")) for e in lists.get("experience", []) if isinstance(e, dict) and e.get("company"))
        return Known(
            profile=profile,
            answers=answers,
            citizenships=citizenships,
            employers=employers,
            education=tuple(e for e in lists.get("education", []) if isinstance(e, dict)),
        )

    def make_plan(self, job_id: str) -> dict[str, Any]:
        self.repository.lock_job(job_id)
        j = self.application.job(job_id)
        if self.application.submit_clicked(job_id) or j["status"] not in PRE_SUBMIT_ORDER:
            raise PermissionError("plan cannot change after submission begins")
        f = self.application.form(job_id)
        if not f or not f["inventory"]:
            raise ValueError("no inventory yet")
        plan, missing = build_plan(
            f["inventory"],
            self.application.load_known(),
            job_id,
            j["company_slug"] or "",
            JobContext(j["company_name"] or "", j["role"] or "", j["apply_url"] or j["url"] or "", j["location"] or ""),
        )
        if f["plan"] is not None and f["plan"] == [asdict(p) for p in plan] and list(f["missing"] or []) == list(missing):
            return {"planned": len(plan), "missing": list(missing)}  # identical re-plan: no write, no event
        self.application.invalidate_form(job_id)
        self.repository.update_job_forms_for_make_plan(J([asdict(p) for p in plan]), J(missing), job_id)
        latest = self.application.form(job_id)
        assert latest
        missing = latest["missing"]
        self.application.event(job_id, "plan", "system", {"planned": len(plan), "missing": missing})
        return {"planned": len(plan), "missing": missing}

    def answer_set(self, question: str, answer: str, scope: str, source: str, job_id: str | None = None) -> None:
        self.repository.insert_answers_for_answer_set(norm(question), answer, scope, source, answer, source)

        self.application.event(job_id, "answer_updated", "user", {"questionKey": norm(question), "scope": scope})

    def answer_suggest(self, question: str, answer: str, job_id: str, source: str) -> bool:
        """Store an answer the agent worked out for this one job. Returns False when the question already has an
        answer in this scope (the owner's, or an earlier run's), which is left untouched."""
        key = norm(question)
        written = self.repository.insert_answer_if_absent(key, answer, f"job:{job_id}", source)
        if written:
            self.application.event(job_id, "answer_suggested", "system", {"questionKey": key})
        return bool(written)

    def required_open(self, job_id: str) -> list[str]:
        form = self.application.form(job_id) or {}
        missing = set(form.get("missing") or [])
        fields = [f for f in (form.get("inventory") or []) if f.get("key") in missing and f.get("required") is True]
        job = self.application.job(job_id)
        _, unresolved = build_plan(
            fields,
            self.application.load_known(),
            job_id,
            job.get("company_slug") or "",
            JobContext(job.get("company_name") or "", job.get("role") or "", job.get("url") or "", job.get("location") or ""),
        )
        return unresolved

    def set_job_answers(self, job_id: str, answers: list[tuple[str, str, str]], *, resume: bool = True) -> None:
        self.repository.lock_job(job_id)
        job = self.application._live(job_id)
        if self.application.submit_clicked(job_id):
            raise PermissionError("answers cannot change after submission begins")
        if job["status"] not in QUESTION_STATES:
            raise PermissionError("This application is no longer waiting for answers.")
        form = self.application.form(job_id)
        fields = {str(f.get("key")): f for f in ((form or {}).get("inventory") or [])}
        known = self.application.load_known()
        pending: dict[tuple[str, ...], tuple[str, str]] = {}
        comparison_options: dict[tuple[str, ...], list[str]] = {}
        key: tuple[str, ...]
        for question, answer, scope in answers:
            options: list[str] = []
            field = fields.get(question)
            if SECRET_KEY.search(str((field or {}).get("label") or question)):
                raise ValueError("passwords are never stored: sign in to the site yourself")  # ADR 0028
            if answer == SKIPPED:
                label = str((field or {}).get("label") or question)
                key = ("answer", f"job:{job_id}", norm(label))
                pending[key] = (label, SKIPPED)
                comparison_options[key] = []
                continue
            if field is not None:  # the dashboard sends the field key; answers are matched by the question's label
                label = str(field.get("label") or question)
                canon = canonical_for(label)
                options = [str(o) for o in (field.get("options") or [])]
                if field.get("unmatchedValue"):
                    options = list(dict.fromkeys([*(field.get("suggestions") or []), *options]))
                    if options:
                        selected = choose_option(answer, options, exact=True)
                        if selected is None:
                            raise ValueError(f"'{answer}' is not one of the choices for: {label}")
                        answer = selected
                    key = ("answer", f"job:{job_id}", norm(label))
                    pending[key] = (label, answer)
                    comparison_options[key] = options
                    continue
                if canon is not None:  # one answer for every wording of this question, now and on future forms
                    value = choose_option(answer, list(canon.options))
                    if value is None:
                        raise ValueError(f"'{answer}' is not one of the choices for: {canon.title}")
                    if canon.contextual:  # depends on this employer/posting: remembered for this job only
                        key = ("answer", f"job:{job_id}", norm(label))
                        pending[key] = (label, value)
                    else:
                        key = ("profile", canon.key)
                        pending[key] = (canon.key, value)
                    comparison_options[key] = list(canon.options)
                    continue
                if options:
                    selected = choose_option(answer, options, exact=field.get("type") == "custom-select")
                    if selected is None:
                        raise ValueError(f"'{answer}' is not one of the choices for: {label}")
                    answer = selected
                question = label
            key = ("answer", scope, norm(question))
            pending[key] = (question, answer)
            comparison_options[key] = options
        changes: dict[tuple[str, ...], tuple[str, str]] = {}
        for key, item in pending.items():
            previous = known.profile.get(key[1]) if key[0] == "profile" else known.answers.get(key[2], {}).get(key[1])
            if previous is not None and comparison_options[key]:
                previous = choose_option(previous, comparison_options[key])
            if previous != item[1]:
                changes[key] = item
        if not changes:
            if resume and self.application.job(job_id).get("waiting_on") == "you":
                self.application.resume(job_id)
            return
        # Saving without resuming keeps the plan and the missing list: the job stays "parked for answers", so its tab
        # is kept. The plan is reset when the owner sends the answers (JobService.resume).
        self.application.invalidate_form(job_id, reset_plan=False)
        for key, (question, answer) in changes.items():
            if key[0] == "profile":
                self.application.profile_set(question, answer, "user (dashboard)", replan=False)
            else:
                self.application.answer_set(question, answer, key[1], "user (dashboard)", job_id)
        self.application.event(job_id, "answers_saved", "user", {"count": len(changes)})
        if resume:
            self.application.resume(job_id)

    def owner_completed(self, job_id: str, *, user_session: bool) -> dict[str, Any]:
        """The owner filled the pending questions in the parked tab themselves ("I filled it in the tab myself").
        Clears the job's pending questions, remembers their labels so a re-report is not asked again, and resumes the agent
        with an instruction delta. The agent verifies the tab; the owner's word alone never authorizes a submit. Idempotent:
        when the agent already runs there is nothing to do."""
        if not user_session:
            raise PermissionError("owner-completed requires a user-session request")
        self.repository.lock_job(job_id)
        job = self.application._live(job_id)
        if self.application.submit_clicked(job_id):
            raise PermissionError("submission already begun; confirm only")
        if job["status"] not in PRE_SUBMIT_ORDER or job["status"] == "awaiting_approval":
            raise PermissionError("This application is not waiting for answers.")
        if job.get("waiting_on") != "you":
            return {"ok": True, "already": True}
        form = self.application.form(job_id) or {}
        missing = set(form.get("missing") or [])
        labels = sorted(
            {
                norm(str(f.get("label") or ""))
                for f in (form.get("inventory") or [])
                if f.get("key") in missing and norm(str(f.get("label") or ""))
            }
        )[:100]
        self.application.invalidate_form(job_id, reset_plan=True)  # nothing is pending any more
        self.application.event(job_id, "owner_completed", "user", {"labels": labels})
        self.application.guide_agent(job_id, OWNER_COMPLETED_GUIDANCE)  # records the hint and resumes
        return {"ok": True, "already": False}

    def invalidate_form(self, job_id: str, *, reset_plan: bool = False, agent_edit: bool = False) -> None:
        self.repository.lock_job(job_id)
        if self.application.submit_clicked(job_id) or self.application.job(job_id)["status"] not in PRE_SUBMIT_ORDER:
            raise PermissionError("submission already begun; confirm only")
        changed = self.repository.update_job_forms_for_invalidate_form(reset_plan, reset_plan, int(agent_edit), job_id)
        if self.repository.revoke_submit_approvals(job_id):
            self.application.event(job_id, "approval_revoked", "system")
        if self.application.job(job_id)["status"] in ("ready", "awaiting_approval"):
            self.application.transition(job_id, "preparing")
        if changed:
            self.application.event(job_id, "draft_invalidated", "system")

    def record_fill(self, job_id: str, failures: int) -> None:
        self.repository.update_job_forms_for_record_fill(failures, job_id)
        self.application.event(job_id, "fill", "system", {"failures": failures})

    def record_verification(self, job_id: str, draft: str | None, payload: dict[str, Any]) -> None:
        self.repository.lock_job(job_id)
        form = self.application.form(job_id)
        if not draft or draft != (form or {}).get("draft_hash"):
            self.application.invalidate_form(job_id)
        self.repository.update_job_forms_for_record_verification(draft, draft, job_id)
        self.application.event(job_id, "check", "system", payload)
        status = self.application.job(job_id)["status"]
        if draft and status == "preparing":
            self.application.transition(job_id, "ready", self.application.verification_gates(job_id))
        elif not draft and status in ("ready", "awaiting_approval"):
            self.application.transition(job_id, "preparing")

    def replan_blocked(self) -> int:
        """Profile changed: jobs waiting for the owner's answers are re-planned against it. Ones that now have every
        required answer resume on their own, so a question the owner already answered in About never needs answering again."""
        resumed = 0
        for row in self.repository.list_jobs_for_replan_blocked():
            if self.application.submit_clicked(row["id"]):
                continue
            before = list(row["missing"] or [])
            try:
                result = self.application.make_plan(row["id"])
            except ValueError:
                continue
            if result["missing"] != before:
                self.application.event(
                    row["id"], "auto_answered", "system", {"before": len(before), "after": len(result["missing"])}
                )
            if not result["missing"]:
                self.application.invalidate_form(row["id"])  # keeps the new plan; drops any stale verification
                self.application.resume(row["id"])
                resumed += 1
        return resumed

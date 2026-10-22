"""Jobs HTTP controllers: validate input, call application operations, shape responses."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..config.runtime import (
    application_service,
    cfg,
    dashboard_service,
    log,
    tracker_service,
)
from ..domain.answers import SKIPPED, Known
from ..domain.question_view import missing_field_views, required_missing_keys, step_of
from ..domain.state_machine import QUESTION_STATES
from ..dto import responses
from ..dto.requests import (
    AgentGuidanceRequest,
    AnswersRequest,
    IngestRequest,
    JobSettings,
    JobStatusRequest,
    JobSubmitMode,
)
from ..middleware.auth import Auth, UserAuth
from ..service.evidence import evidence_view, screenshot_path
from ..service.tabs import TabService
from ..service.tracker import JOB_STATUSES

router = APIRouter(tags=["Jobs"])

tabs = TabService(application_service)


@router.get("/stats", response_model=responses.StatsResponse)
def stats(_: Auth) -> dict[str, Any]:
    return application_service.stats()


@router.get("/jobs", response_model=list[responses.JobListItem])
def jobs(
    _: Auth,
    status: str | None = None,
    waitingOn: str | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
) -> list[dict[str, Any]]:
    if status is not None and status not in JOB_STATUSES:
        raise HTTPException(400, "unknown status")
    if waitingOn is not None and waitingOn not in ("agent", "you", "site"):
        raise HTTPException(400, "unknown waitingOn")
    rows = application_service.list(status, waitingOn, q, limit, offset)
    tabs.annotate(rows)
    return rows


@router.get("/jobs/{job_id}", response_model=responses.JobDetail)
def job_detail(job_id: str, _: Auth) -> dict[str, Any]:
    try:
        ctx = application_service.context(job_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    j = application_service.job(job_id)
    f = application_service.form(job_id)
    events = dashboard_service.job_events(job_id)
    return {
        **ctx,
        "tracking": tracker_service.detail_extra(job_id),
        "detailsRefresh": application_service.details_state(job_id),
        "usage": application_service.reporting.job_usage(job_id),
        "posting": application_service.posting(job_id),
        "location": j["location"],
        "pay": j["pay"],
        "events": events,
        "plan": (f["plan"] if f and f["plan"] else []),
        "agentEdits": [],  # legacy response field; adopted values are in plan with source=agent
        "draftRevision": int(f["revision"]) if f else 0,
        "inventory": (f["inventory"] if f and f["inventory"] else []),
        "submitEvidence": evidence_view(f["submit_evidence"]) if f else None,
        "tabOpen": job_id in tabs.open_jobs([job_id]),
        "submitMode": application_service.submit_mode_info(job_id),
        "parkSignal": f["park_signal"] if f else None,
    }


@router.get("/jobs/{job_id}/submit-evidence/screenshot")
def submit_evidence_screenshot(job_id: str, _: Auth) -> FileResponse:
    """The latest stored screenshot of this job's tab after a submit run. The path comes from the job's own stored
    record (a validated generated name under data/evidence/<job>/), never from the request."""
    form = application_service.form(job_id)
    stored = form["submit_evidence"] if form else None
    name = stored.get("screenshot") if isinstance(stored, dict) else None
    try:
        path = screenshot_path(cfg.data_dir, job_id, name) if isinstance(name, str) else None
    except ValueError:
        path = None
    if path is None:
        raise HTTPException(404, "no screenshot stored for this job")
    return FileResponse(
        path,
        media_type="image/png",
        content_disposition_type="inline",
        headers={
            "Content-Security-Policy": "default-src 'none'; img-src 'self'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/needs-you", response_model=responses.NeedsYou)
def needs_you(_: Auth, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=1_000_000)) -> dict[str, Any]:
    blocked = dashboard_service.blocked_jobs(limit, offset)
    rows = dashboard_service.blocked_jobs_ready(limit, offset)
    ready = []
    for row in rows:
        reasons: list[str] = []
        if row["cvMode"] == "tailor":
            reasons.append("Tailored CV: read it before sending")
        if row["agentSet"]:
            reasons.append("JobPilot typed some answers itself")
        if row["policy"] != "auto":
            reasons.append("This site needs you to press submit")
        ready.append(
            {
                "id": row["id"],
                "company": row["company"],
                "role": row["role"],
                "draftHash": row["draftHash"],
                "cvTrack": row["cvTrack"],
                "clean": not reasons,
                "reasons": reasons,
                "parkSignal": row["parkSignal"],
            }
        )
    bank: Known | None = None
    for row in blocked:
        inventory = row.pop("inventory", None)
        if row.get("status") not in QUESTION_STATES:
            inventory, row["missing"] = None, []  # a job past preparation never renders as a question card
        slug = row.pop("companySlug", None) or ""
        row["missing"] = required_missing_keys(inventory, row.get("missing"))
        views = missing_field_views(inventory, row["missing"])
        if views:  # a question the answer bank already covers is never asked again, whatever key the run gave it
            if bank is None:
                bank = dashboard_service.answer_bank()
            views = dashboard_service.unsettled(row["id"], slug, views, bank)
            row["missing"] = [k for k in row["missing"] if k in {v["key"] for v in views}]
        row["missingFields"] = views
        row["step"] = step_of(row.get("reason"))
        row["submitEvidence"] = evidence_view(row.pop("submit_evidence", None))
    tabs.annotate(blocked)
    tabs.annotate(ready)
    return {"blocked": blocked, "readyToApprove": ready}


@router.post("/ingest", response_model=list[responses.Ingested | responses.IngestError])
def ingest(body: IngestRequest, _: Auth) -> list[dict[str, Any]]:
    if body.cvTrack is not None and not dashboard_service.track_exists_for_ingest(body.cvTrack):
        raise HTTPException(422, "unknown CV track")
    out: list[dict[str, Any]] = []
    for url in body.urls:
        try:
            out.append(application_service.ingest(str(url), cv_track=body.cvTrack, cv_mode=body.cvMode))
        except (LookupError, PermissionError, ValueError) as e:
            out.append({"url": str(url), "error": str(e)[:200]})
        except Exception:  # noqa: BLE001 - never echo driver/database internals
            log.exception("ingest failed for one link")
            out.append({"url": str(url), "error": "could not add this link"})
    return out


@router.post("/jobs/{job_id}/owner-submitted", response_model=responses.OwnerSubmitted)
def owner_submitted(job_id: str, _: UserAuth) -> dict[str, Any]:
    """The owner submitted in the parked Neo tab and confirms it (ADR 0026). Idempotent; never started by an agent."""
    try:
        return application_service.owner_submitted(job_id, user_session=True)
    except LookupError as e:
        raise HTTPException(404, "unknown job") from e
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/jobs/{job_id}/park-signal/dismiss", response_model=responses.Ok)
def dismiss_park_signal(job_id: str, _: UserAuth) -> dict[str, bool]:
    """The owner says the "looks submitted" / "tab closed" signal was wrong. Idempotent."""
    try:
        application_service.dismiss_park_signal(job_id, user_session=True)
    except LookupError as e:
        raise HTTPException(404, "unknown job") from e
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/jobs/{job_id}/reopen", response_model=responses.Ok)
def reopen_application(job_id: str, _: UserAuth) -> dict[str, bool]:
    """The owner closed the parked tab without submitting: prepare the application again in a fresh tab."""
    try:
        application_service.reopen_application(job_id, user_session=True)
    except LookupError as e:
        raise HTTPException(404, "unknown job") from e
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/jobs/{job_id}/user-confirmed", response_model=responses.Ok)
def user_confirmed(job_id: str, _: UserAuth) -> dict[str, bool]:
    try:
        application_service.record_submitted(
            job_id, "user-confirmed", "User reported submitting this application.", user_session=True
        )
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/jobs/{job_id}/owner-completed", response_model=responses.OwnerCompleted)
def owner_completed(job_id: str, _: UserAuth) -> dict[str, Any]:
    try:
        return application_service.owner_completed(job_id, user_session=True)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except (PermissionError, ValueError) as e:
        raise HTTPException(409, str(e)) from e


@router.post("/jobs/refresh-missing-details", response_model=responses.MissingDetailsRequested)
def refresh_missing_details(_: UserAuth) -> dict[str, Any]:
    """Owner-only. Queue a refresh for every live job whose role or company is still a placeholder (bounded)."""
    try:
        return application_service.refresh_missing_details(user_session=True)
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/jobs/{job_id}/refresh-details", response_model=responses.DetailsRequested)
def refresh_details(job_id: str, _: UserAuth) -> dict[str, Any]:
    """Owner-only, any status. Re-reads the posting's details; never changes status, form, approvals or waiting_on."""
    try:
        return application_service.refresh_details(job_id, user_session=True)
    except LookupError as e:
        raise HTTPException(404, "unknown job") from e
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/jobs/{job_id}/email-code", response_model=responses.EmailCodeRequested)
def email_code(job_id: str, _: UserAuth) -> dict[str, bool]:
    """Owner-only. Starts the one verification run that reads the emailed code from Gmail for a parked application."""
    try:
        return application_service.request_email_code(job_id, user_session=True)
    except LookupError as e:
        raise HTTPException(404, "unknown job") from e
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/jobs/{job_id}/status", response_model=responses.Ok)
def job_status(job_id: str, body: JobStatusRequest, _: UserAuth) -> dict[str, bool]:
    try:
        application_service.transition(job_id, body.status, actor="user")
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/jobs/{job_id}/settings", response_model=responses.Ok)
def job_settings(job_id: str, body: JobSettings, _: UserAuth) -> dict[str, bool]:
    try:
        application_service.set_job_settings(job_id, body.cvTrack, body.cvMode)
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/jobs/{job_id}/submit-mode", response_model=responses.Ok)
def job_submit_mode(job_id: str, body: JobSubmitMode, _: UserAuth) -> dict[str, bool]:
    try:
        application_service.set_submit_mode(job_id, body.mode)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/jobs/{job_id}/answers", response_model=responses.Ok)
def job_answers(job_id: str, body: AnswersRequest, _: UserAuth) -> dict[str, bool]:
    try:
        application_service.set_job_answers(
            job_id,
            [(a.question, SKIPPED if a.skip else a.answer, "job:" + job_id if a.skip else a.scope) for a in body.answers],
            resume=body.resume,
        )
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/jobs/{job_id}/resume", response_model=responses.Ok)
def job_resume(job_id: str, _: UserAuth, try_anyway: bool = False) -> dict[str, bool]:
    try:
        application_service.resume(job_id, try_anyway=try_anyway)
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/jobs/{job_id}/unapproved-submit/acknowledge", response_model=responses.Ok)
def unapproved_submit_acknowledge(job_id: str, _: UserAuth) -> dict[str, bool]:
    """Owner-only, idempotent. Records that the owner checked the application; automation stays stopped."""
    try:
        application_service.acknowledge_unapproved_submit(job_id)
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/jobs/{job_id}/guidance", response_model=responses.Ok)
def job_guidance(job_id: str, body: AgentGuidanceRequest, _: UserAuth) -> dict[str, bool]:
    try:
        application_service.guide_agent(job_id, body.message)
    except LookupError as error:
        raise HTTPException(404, "Unknown job") from error
    except (PermissionError, ValueError) as error:
        raise HTTPException(409, str(error)) from error
    return {"ok": True}


@router.post("/jobs/{job_id}/tab/focus", response_model=responses.TabFocus)
def tab_focus(job_id: str, _: UserAuth) -> dict[str, Any]:
    """Where the agent's Neo tab for this job is. Only the signed-in owner session may do it; it
    never opens a tab, navigates, or touches leases or job state. A missing tab is a normal answer, not an error."""
    try:
        application_service.job(job_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    return tabs.focus(job_id)

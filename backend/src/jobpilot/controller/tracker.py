"""Tracker HTTP controllers: validate input, call application operations, shape responses."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import HttpUrl, TypeAdapter

from ..config.runtime import (
    tracker_service,
)
from ..dto import responses
from ..dto.requests import (
    AppliedRequest,
    ImportRequest,
    MergeRequest,
    TrackingUpdate,
)
from ..middleware.auth import Auth, UserAuth
from ..middleware.errors import _guard

_HTTP_URL: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)


router = APIRouter(tags=["Tracker"])


@router.get("/tracker", response_model=list[responses.TrackerRow])
def tracker_list(
    _: Auth,
    status: str | None = None,
    q: str | None = None,
    waitingOn: str | None = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0, le=1_000_000),
    archived: bool = False,
) -> list[dict[str, Any]]:
    try:
        return tracker_service.rows(status, q, waitingOn, limit, offset, archived)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/tracker/applied", response_model=list[responses.LoggedApplication])
def tracker_applied(body: AppliedRequest, _: UserAuth) -> list[dict[str, Any]]:
    extra = {"resume_label": body.resumeLabel, "submitted_via": body.submittedVia, "notes": body.notes}
    if body.appliedOn and body.appliedOn > date.today() + timedelta(days=1):
        raise HTTPException(400, "appliedOn: the date can't be in the future")
    # Validate per link so one typo doesn't refuse the whole paste.
    good: list[str] = []
    bad: list[dict[str, Any]] = []
    for raw in dict.fromkeys(u.strip() for u in body.urls if u.strip()):
        try:
            if len(raw) > 2000:
                raise ValueError
            good.append(str(_HTTP_URL.validate_python(raw)))
        except ValueError:
            bad.append({"url": raw[:200], "error": "That doesn't look like a web link (it must start with http:// or https://)."})
    if not good and not bad:
        raise HTTPException(400, "Paste at least one job link.")
    return [*(tracker_service.log_applied(good, body.appliedOn, extra) if good else []), *bad]


@router.post("/tracker/import", response_model=responses.ImportResult)
def tracker_import(body: ImportRequest, _: UserAuth) -> dict[str, Any]:
    try:
        return tracker_service.import_rows(body.text)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.patch("/jobs/{job_id}/tracking", response_model=responses.TrackingChange)
def tracker_update(job_id: str, body: TrackingUpdate, _: UserAuth) -> dict[str, Any]:
    try:
        return tracker_service.update(job_id, body.model_dump(exclude_unset=True))
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/jobs/{job_id}", response_model=responses.Ok)
def job_archive(job_id: str, _: UserAuth) -> dict[str, Any]:
    return _guard(lambda: tracker_service.archive(job_id))  # type: ignore[no-any-return]


@router.post("/jobs/{job_id}/restore", response_model=responses.Ok)
def job_restore(job_id: str, _: UserAuth) -> dict[str, Any]:
    return _guard(lambda: tracker_service.restore(job_id))  # type: ignore[no-any-return]


@router.delete("/jobs/{job_id}/permanent", response_model=responses.Ok)
def job_purge(job_id: str, _: UserAuth) -> dict[str, Any]:
    return _guard(lambda: tracker_service.purge(job_id))  # type: ignore[no-any-return]


@router.get("/tracker/duplicates", response_model=list[responses.DuplicateGroup])
def tracker_duplicates(_: Auth) -> list[dict[str, Any]]:
    return tracker_service.duplicates()


@router.post("/tracker/merge", response_model=responses.Ok)
def tracker_merge(body: MergeRequest, _: UserAuth) -> dict[str, Any]:
    return _guard(lambda: tracker_service.merge(body.keepId, body.mergeIds))  # type: ignore[no-any-return]

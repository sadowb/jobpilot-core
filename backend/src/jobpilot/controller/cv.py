"""Cv HTTP controllers: validate input, call application operations, shape responses."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..config.runtime import (
    application_service,
    cfg,
    dashboard_service,
)
from ..domain.cv import CV_FILENAME, sha256
from ..dto import responses
from ..dto.requests import (
    TrackCreate,
    TrackEdit,
    TrackFile,
    TrackRemove,
)
from ..middleware.auth import Auth, UserAuth
from ..middleware.errors import _guard
from ..projection.vault import cv_history
from ..service import cvtracks

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")


router = APIRouter(tags=["CV tracks"])


def _pdf(sha: str | None) -> FileResponse:
    if not sha:
        raise HTTPException(404, "no CV on file for this yet")
    try:
        path = application_service.store.object_dir(sha) / CV_FILENAME
    except ValueError as e:
        raise HTTPException(404, "CV not found") from e
    if not path.is_file():
        raise HTTPException(404, "CV not found")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=CV_FILENAME,
        content_disposition_type="inline",
        headers={"Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'"},
    )  # same-origin framing only, so the dashboard can preview it; always served under the one public name


@router.get("/tracks", response_model=list[responses.Track])
def tracks(_: Auth) -> list[dict[str, Any]]:
    return dashboard_service.tracks(sha256(cvtracks.UPLOADED_MARKER))


@router.post("/tracks", response_model=responses.TrackSaved)
def track_create(body: TrackCreate, _: UserAuth) -> dict[str, Any]:
    track = body.id or cvtracks.slug_for(body.title)
    if dashboard_service.track_exists(track):
        raise HTTPException(409, "a CV with that name already exists; choose another name or replace its file")
    return _guard(  # type: ignore[no-any-return]
        lambda: cvtracks.save_version(
            application_service, track, body.title, body.keywords, body.defaultMode, body.pdfBase64, body.texBase64, body.market
        )
    )


@router.put("/tracks/{track_id}/cv", response_model=responses.TrackSaved)
def track_replace(track_id: str, body: TrackFile, _: UserAuth) -> dict[str, Any]:
    row = dashboard_service.track_metadata(track_id)
    if not row:
        raise HTTPException(404, f"unknown CV track {track_id}")
    return _guard(  # type: ignore[no-any-return]
        lambda: cvtracks.save_version(
            application_service,
            track_id,
            row["title"],
            list(row["keywords"] or []),
            row["default_mode"],
            body.pdfBase64,
            body.texBase64,
            row["market"],
        )
    )


@router.patch("/tracks/{track_id}", response_model=responses.Ok)
def track_edit(track_id: str, body: TrackEdit, _: UserAuth) -> dict[str, bool]:
    _guard(lambda: cvtracks.update_meta(application_service, track_id, body.title, body.keywords, body.defaultMode, body.market))
    return {"ok": True}


@router.get("/tracks/{track_id}/tex", response_model=responses.TrackSource)
def track_tex(track_id: str, _: UserAuth) -> dict[str, Any]:
    if not _ID_RE.fullmatch(track_id):
        raise HTTPException(400, "bad track id")
    return _guard(lambda: cvtracks.read_source(application_service, track_id))  # type: ignore[no-any-return]


@router.delete("/tracks/{track_id}", response_model=responses.TrackRemoved)
def track_remove(track_id: str, body: TrackRemove, _: UserAuth) -> dict[str, Any]:
    return _guard(lambda: cvtracks.remove(application_service, track_id, body.reassignTo))  # type: ignore[no-any-return]


@router.get("/tracks/{track_id}/cv.pdf")
def track_cv(track_id: str, _: Auth) -> FileResponse:
    row = dashboard_service.track_cv(track_id)
    return _pdf(row["sha256"] if row else None)


@router.get("/jobs/{job_id}/cv.pdf")
def job_cv(job_id: str, _: Auth) -> FileResponse:
    """The CV this job uses: its own (tailored or pinned) version, else its track's current one."""
    row = dashboard_service.job_cv(job_id)
    if not row:
        raise HTTPException(404, f"unknown job {job_id}")
    return _pdf(row["sha"])


@router.get("/tracks/{track_id}/history", response_model=list[responses.History])
def track_history(track_id: str, _: Auth) -> list[dict[str, str]]:
    if not track_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "bad track id")
    return cv_history(cfg.vault, f"CV Tracks/{track_id}")

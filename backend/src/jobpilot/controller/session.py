"""Session HTTP controllers: validate input, call application operations, shape responses."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..config.runtime import (
    ALLOWED_ORIGINS,
)
from ..dto import responses

router = APIRouter(tags=["Session"])


@router.get("/session", response_model=responses.SessionResponse)
def session(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS:
        raise HTTPException(403, "bad origin")
    csrf = request.session.get("csrf")
    if not csrf:
        raise HTTPException(401, "unauthorized")
    return {"csrf": csrf}

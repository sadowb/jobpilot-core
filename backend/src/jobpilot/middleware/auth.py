"""Session and bearer authentication; approvals require UserAuth."""

from __future__ import annotations

import hmac
import secrets
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from ..config.runtime import (
    ALLOWED_ORIGINS,
    API_TOKEN,
)


class Principal(BaseModel):
    kind: Literal["user", "bearer"]
    csrf: str = ""


def start_navigation_session(request: Request) -> None:
    """Bootstrap only an owner's top-level navigation, as for the dashboard."""
    if (
        request.headers.get("sec-fetch-site") in {"none", "same-origin"}
        and request.headers.get("sec-fetch-dest") == "document"
        and not request.session.get("csrf")
    ):
        request.session["csrf"] = secrets.token_urlsafe(24)


def principal(request: Request) -> Principal:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], API_TOKEN):
        return Principal(kind="bearer")
    csrf = request.session.get("csrf")
    if not csrf:
        raise HTTPException(401, "unauthorized")
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("origin")
        if origin not in ALLOWED_ORIGINS:
            raise HTTPException(403, "bad origin")
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf):
            raise HTTPException(403, "bad csrf")
    return Principal(kind="user", csrf=csrf)


Auth = Annotated[Principal, Depends(principal)]


def user_only(p: Auth) -> Principal:
    if p.kind != "user":
        raise HTTPException(403, "user session required")
    return p


UserAuth = Annotated[Principal, Depends(user_only)]

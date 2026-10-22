"""Built dashboard assets and top-level navigation session bootstrap."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from ..config.runtime import (
    cfg,
)
from ..middleware.auth import start_navigation_session
from ..middleware.security import IMMUTABLE

router = APIRouter()
DIST = cfg.home / "frontend" / "dist"


@router.get("/{path:path}", include_in_schema=False, response_model=None)
def spa(path: str, request: Request) -> Response:
    if path.startswith("api/"):
        raise HTTPException(404, "not found")
    root = DIST.resolve()
    target = (DIST / path).resolve()
    found = target.is_relative_to(root) and target.is_file()
    if path.startswith("assets/"):
        # Vite names these by content hash: a given URL never changes, so the browser may keep it for good. A missing
        # one is a 404, never the HTML shell (which would then be cached and executed as a script or stylesheet).
        if not found:
            raise HTTPException(404, "not found")
        return FileResponse(target, headers={"Cache-Control": IMMUTABLE})
    if not found:
        target = DIST / "index.html"
        # A top-level navigation by the owner's own browser to any dashboard page (not only "/") starts the session,
        # so a bookmarked or reloaded deep link opens directly. Cross-site and scripted requests never carry these headers.
        start_navigation_session(request)
    if not target.is_file():
        return JSONResponse({"message": "JobPilot backend running. Build the frontend: pnpm build:frontend"})
    # index.html names the current hashed assets, so a stale copy would point at files a rebuild has removed.
    return FileResponse(target, headers={"Cache-Control": "no-cache"})

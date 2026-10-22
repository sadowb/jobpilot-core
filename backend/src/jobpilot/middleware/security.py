"""Host, request-size, response-header and cookie-secret protections."""

from __future__ import annotations

import os
import secrets
import stat
from typing import Any
from urllib.parse import unquote

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

from ..config.runtime import (
    ALLOWED_HOSTS,
    cfg,
)

MAX_BODY_BYTES = 4_500_000  # the largest legitimate body is a 3M-character sheet import

IMMUTABLE = "public, max-age=31536000, immutable"


def _session_secret(name: str = ".session-secret") -> str:
    """Read (or create, mode 0600) one random secret file under the data directory."""
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.data_dir / name
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r") as secret_file:
            metadata = os.fstat(secret_file.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError("Session secret must be a regular file") from None
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                os.fchmod(secret_file.fileno(), 0o600)
            secret = secret_file.read().strip()
            if len(secret) < 32:
                raise RuntimeError("Session secret file is invalid") from None
            return secret
    with os.fdopen(fd, "w") as secret_file:
        os.fchmod(secret_file.fileno(), 0o600)
        secret = secrets.token_urlsafe(48)
        secret_file.write(secret)
    return secret


class BodyTooLarge(HTTPException):  # an HTTPException so FastAPI's body parsing re-raises it as-is (not a generic 400)
    def __init__(self) -> None:
        super().__init__(413, "request too large")


class BodyLimit:
    """Counts streamed bytes, so a chunked body (no Content-Length) hits the same cap as a declared one."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        total = 0

        async def limited() -> Any:
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > MAX_BODY_BYTES:
                    raise BodyTooLarge
            return message

        await self.inner(scope, limited, send)


async def security(request: Request, call_next: Any) -> Response:
    if request.headers.get("host", "") not in ALLOWED_HOSTS:  # DNS-rebinding guard
        return _harden(JSONResponse({"error": "bad host"}, status_code=421))
    if "\x00" in unquote(request.url.path) or "\x00" in unquote(request.url.query):
        return _harden(JSONResponse({"error": "invalid characters in request"}, status_code=400))
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return _harden(JSONResponse({"error": "request too large"}, status_code=413))
    resp: Response = await call_next(request)
    return _harden(resp)


def _harden(resp: Response) -> Response:
    resp.headers["Content-Security-Policy"] = resp.headers.get("Content-Security-Policy") or (  # a route may narrow it (PDF)
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if resp.headers.get("Cache-Control") != IMMUTABLE:  # only set by spa() for content-hashed build files
        resp.headers["Cache-Control"] = "no-store"
    return resp

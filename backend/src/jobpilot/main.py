"""FastAPI assembly: middleware, feature routers, lifespan and dashboard assets."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .api.dashboard import router as static_router
from .api.documentation import router as documentation_router
from .api.v1.router import router
from .config.runtime import API_TOKEN, SESSION_TTL, application_service, cfg, db, live_events
from .middleware.errors import install_error_handlers
from .middleware.security import BodyLimit, _session_secret, security


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    assert db.pool is not None
    db.pool.open()
    tp = cfg.data_dir / ".api-token"
    fd = os.open(tp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as token_file:
        token_file.write(API_TOKEN)
    application_service.load_runtime_settings()
    # Maintenance sweeps (vault projection, browser reaper, agent-session GC) run only in the control worker, so the
    # vault keeps one writer process (AGENTS.md hard rule 6).
    try:
        await live_events.start()
        yield
    finally:
        try:
            await live_events.stop()
        finally:
            db.close()


app = FastAPI(title="JobPilot", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    session_cookie="jp_session",
    max_age=SESSION_TTL,
    same_site="strict",
    https_only=False,
    path="/",
)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
install_error_handlers(app)
app.add_middleware(BodyLimit)
app.middleware("http")(security)
app.include_router(router, prefix="/api/v1")
app.include_router(router, prefix="/api", include_in_schema=False)  # Existing clients keep working.
app.include_router(documentation_router)
app.include_router(static_router)  # Catch-all must come last.

if __name__ == "__main__":
    from .server import run

    run()

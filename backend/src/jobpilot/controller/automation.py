"""Automation HTTP controllers: validate input, call application operations, shape responses."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.background import BackgroundTask

from ..config import RuntimeSettings
from ..config.runtime import (
    ALLOWED_ORIGINS,
    application_service,
    dashboard_service,
    live_events,
)
from ..dto import responses
from ..dto.requests import (
    BoardUpdate,
    RecipeReview,
    RecipeSuggestion,
)
from ..middleware.auth import Auth, UserAuth

router = APIRouter(tags=["Automation"])


@router.get("/boards", response_model=list[responses.Board])
def boards(_: Auth) -> list[dict[str, Any]]:
    return dashboard_service.boards()


@router.get("/updates", response_model=responses.Updates)
def updates(_: Auth, limit: int = Query(30, ge=1, le=100), before: int | None = Query(None, ge=1)) -> dict[str, Any]:
    return application_service.updates(limit, before)


@router.get("/automation", response_model=responses.Automation)
def automation(_: Auth) -> dict[str, Any]:
    return application_service.automation_status()


@router.post("/boards/{board_id}", response_model=responses.Ok)
def board_update(board_id: str, body: BoardUpdate, _: UserAuth) -> dict[str, bool]:
    application_service.board_update(board_id, body.automation)
    return {"ok": True}


@router.post("/automation/{action}", response_model=responses.Automation)
def automation_action(action: Literal["start", "stop"], _: UserAuth) -> dict[str, Any]:
    application_service.set_automation(action == "start")
    return application_service.automation_status()


@router.get("/settings", response_model=RuntimeSettings)
def settings_get(_: Auth) -> RuntimeSettings:
    return application_service.runtime_settings()


@router.get("/recipe-suggestions", response_model=list[RecipeSuggestion])
def recipe_suggestions(
    _: UserAuth, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)
) -> list[dict[str, Any]]:
    return dashboard_service.recipe_suggestions(limit, offset)


@router.post("/recipe-suggestions/{suggestion_id}/review", response_model=responses.Ok)
def recipe_review(suggestion_id: int, body: RecipeReview, _: UserAuth) -> dict[str, bool]:
    try:
        application_service.review_recipe(suggestion_id, body.accept, body.text)
    except (ValueError, PermissionError) as error:
        raise HTTPException(409, str(error)) from error
    return {"ok": True}


@router.put("/settings", response_model=RuntimeSettings)
def settings_put(body: RuntimeSettings, _: UserAuth) -> RuntimeSettings:
    try:
        application_service.configure_runtime(body)
        return application_service.runtime_settings()
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/events", response_model=None)
async def events(request: Request, _: UserAuth) -> EventSourceResponse:
    # EventSource sends cookies same-origin, with no CSRF header on a GET.
    origin = request.headers.get("origin")
    if (origin is not None and origin not in ALLOWED_ORIGINS) or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "bad origin")
    headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    try:
        client = live_events.subscribe()
    except (ConnectionError, OverflowError):
        # Non-200 HTTP responses can permanently stop native EventSource retries. A finite
        # SSE response uses its standard EOF/retry behavior without allocating a subscription.
        return EventSourceResponse(
            iter([ServerSentEvent(comment="live updates unavailable", retry=5000)]),
            ping=15,
            send_timeout=10,
            headers=headers,
        )

    async def disconnected(_: Any) -> None:
        live_events.unsubscribe(client)

    # Last-Event-ID is intentionally not replayed. Native EventSource's onopen refetches
    # current state after every connection, including a PG listener interruption.
    return EventSourceResponse(
        live_events.stream(client),
        ping=15,
        send_timeout=10,
        headers=headers,
        client_close_handler_callable=disconnected,
        background=BackgroundTask(live_events.unsubscribe, client),
    )

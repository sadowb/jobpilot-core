"""Profile HTTP controllers: validate input, call application operations, shape responses."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..config.runtime import (
    application_service,
    dashboard_service,
    tracker_service,
)
from ..domain.canonical_answers import REGISTRY
from ..domain.countries import COUNTRIES
from ..dto import responses
from ..dto.requests import (
    AnswerRef,
    AnswerUpsert,
    FactConfirm,
    FactCreate,
    ProfileSection,
    ProfileSet,
)
from ..middleware.auth import Auth, UserAuth
from ..middleware.errors import _guard

router = APIRouter(tags=["Profile"])


@router.get("/answer-bank", response_model=list[responses.AnswerBankItem])
def answer_bank(_: Auth) -> list[dict[str, Any]]:
    return [{"key": c.key, "title": c.title, "group": c.group, "options": list(c.options)} for c in REGISTRY if not c.contextual]


@router.get("/countries", response_model=list[str])
def countries(_: Auth) -> list[str]:
    return list(COUNTRIES)


@router.get("/profile", response_model=list[responses.Profile])
def profile(_: Auth) -> list[dict[str, Any]]:
    return [
        {"key": entry.key, "value": entry.value, "source": entry.source, "confirmedAt": entry.confirmed_at}
        for entry in application_service.get_profile()
    ]


@router.get("/facts", response_model=list[responses.Fact])
def facts(_: Auth) -> list[dict[str, Any]]:
    return dashboard_service.facts()


@router.get("/answers", response_model=list[responses.Answer])
def answers(_: Auth) -> list[dict[str, Any]]:
    return dashboard_service.answers()


@router.put("/profile/sections/{section}", response_model=responses.ProfileSectionSaved)
def profile_section(section: str, body: ProfileSection, _: UserAuth) -> dict[str, Any]:
    if section not in ("education", "experience", "languages", "citizenships"):
        raise HTTPException(404, "unknown section")
    return {"entries": _guard(lambda: application_service.profile_section_set(section, body.entries))}


@router.delete("/profile/{key}", response_model=responses.Ok)
def profile_clear(key: str, _: UserAuth) -> dict[str, bool]:
    if not key.replace("_", "").isalnum() or len(key) > 60:
        raise HTTPException(400, "bad profile key")
    application_service.profile_clear(key)
    return {"ok": True}


@router.post("/profile", response_model=responses.Ok)
def profile_set(body: ProfileSet, _: UserAuth) -> dict[str, bool]:
    _guard(lambda: application_service.profile_set(body.key, body.value, "user (dashboard)"))
    return {"ok": True}


@router.post("/facts/{fact_id}/confirm", response_model=responses.Ok)
def fact_confirm(fact_id: str, body: FactConfirm, _: UserAuth) -> dict[str, bool]:
    application_service.fact_confirm(fact_id, body.confirmed, body.claim)
    return {"ok": True}


@router.post("/facts", response_model=responses.FactCreated)
def fact_create(body: FactCreate, _: UserAuth) -> dict[str, Any]:
    return _guard(lambda: tracker_service.fact_create(body.claim))  # type: ignore[no-any-return]


@router.delete("/facts/{fact_id}", response_model=responses.Ok)
def fact_delete(fact_id: str, _: UserAuth) -> dict[str, Any]:
    return _guard(lambda: tracker_service.fact_delete(fact_id))  # type: ignore[no-any-return]


@router.put("/answers", response_model=responses.Ok)
def answer_upsert(body: AnswerUpsert, _: UserAuth) -> dict[str, Any]:
    application_service.answer_set(body.questionKey, body.answer, body.scope, "you")
    return {"ok": True}


@router.delete("/answers", response_model=responses.Ok)
def answer_remove(body: AnswerRef, _: UserAuth) -> dict[str, Any]:
    return _guard(lambda: tracker_service.answer_delete(body.questionKey, body.scope))  # type: ignore[no-any-return]

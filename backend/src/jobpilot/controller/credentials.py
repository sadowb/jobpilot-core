"""Credential HTTP controllers (ADR 0031): metadata for any session, plaintext only for the owner's dashboard session."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response

from ..config.runtime import application_service
from ..dto import responses
from ..dto.requests import CredentialCreate, CredentialImport
from ..middleware.auth import Auth, UserAuth
from ..middleware.errors import _guard

router = APIRouter(tags=["Credentials"])

NO_STORE = {"Cache-Control": "no-store"}


@router.get("/credentials", response_model=list[responses.CredentialMeta])
def credentials(_: Auth) -> list[dict[str, Any]]:
    return application_service.credentials.list_all()


@router.post("/credentials", response_model=responses.CredentialMeta)
def credential_add(body: CredentialCreate, _: UserAuth) -> dict[str, Any]:
    return _guard(lambda: application_service.credentials.add_manual(body.origin, body.username, body.password))  # type: ignore[no-any-return]


@router.post("/credentials/import", response_model=responses.CredentialImportResult)
def credential_import(body: CredentialImport, _: UserAuth) -> dict[str, Any]:
    return _guard(lambda: application_service.credentials.import_csv(body.csv, scope=body.scope, commit=body.commit))  # type: ignore[no-any-return]


@router.post("/credentials/export")
def credential_export(_: UserAuth) -> Response:
    text = application_service.credentials.export_csv()
    return Response(
        text,
        media_type="text/csv",
        headers={**NO_STORE, "Content-Disposition": 'attachment; filename="jobpilot-passwords.csv"'},
    )


@router.post("/credentials/{credential_id}/reveal", response_model=responses.CredentialReveal)
def credential_reveal(credential_id: int, response: Response, _: UserAuth) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    return {"password": _guard(lambda: application_service.credentials.reveal(credential_id))}


@router.delete("/credentials/{credential_id}", response_model=responses.Ok)
def credential_retire(credential_id: int, _: UserAuth) -> dict[str, bool]:
    _guard(lambda: application_service.credentials.retire(credential_id))
    return {"ok": True}

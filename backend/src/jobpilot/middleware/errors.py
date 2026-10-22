"""Stable API error responses and safe conversion of application errors."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..config.runtime import (
    log,
)


async def http_error(_: Request, e: Exception) -> JSONResponse:
    assert isinstance(e, HTTPException)
    return JSONResponse({"error": str(e.detail)}, status_code=e.status_code)


async def validation_error(_: Request, e: Exception) -> JSONResponse:
    assert isinstance(e, RequestValidationError)
    first = e.errors()[0] if e.errors() else {"msg": "invalid request"}
    return JSONResponse(
        {"error": f"{'.'.join(str(x) for x in first.get('loc', [])[1:])}: {first.get('msg')}".strip(": ")}, status_code=422
    )


async def not_found(_: Request, e: Exception) -> JSONResponse:
    assert isinstance(e, LookupError)
    return JSONResponse({"error": str(e) or "not found"}, status_code=404)  # any route: unknown id is a 404, never a 500


async def not_allowed(_: Request, e: Exception) -> JSONResponse:
    assert isinstance(e, PermissionError)
    return JSONResponse({"error": str(e) or "not allowed"}, status_code=409)


async def unhandled(_: Request, e: Exception) -> JSONResponse:
    log.exception("request failed")
    return JSONResponse({"error": "internal error"}, status_code=500)  # never leak internals


def _guard(fn: Any) -> Any:
    try:
        return fn()
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(LookupError, not_found)
    app.add_exception_handler(PermissionError, not_allowed)
    app.add_exception_handler(Exception, unhandled)

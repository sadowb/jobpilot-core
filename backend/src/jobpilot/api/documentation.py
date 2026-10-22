"""Authenticated Swagger UI and OpenAPI schema, with all assets served locally."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from ..config.runtime import cfg
from ..middleware.auth import Auth, principal, start_navigation_session

router = APIRouter(include_in_schema=False)
ASSETS = {"swagger-ui-bundle.js", "swagger-ui.css"}


@router.get("/docs", response_class=HTMLResponse)
def swagger(request: Request) -> HTMLResponse:
    start_navigation_session(request)
    principal(request)
    return HTMLResponse(
        """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JobPilot API · Swagger UI</title>
<link rel="stylesheet" href="/docs/assets/swagger-ui.css"></head>
<body><div id="swagger-ui"></div>
<script src="/docs/assets/swagger-ui-bundle.js"></script>
<script src="/docs/init.js"></script></body></html>"""
    )


@router.get("/openapi.json")
def openapi(request: Request, _: Auth) -> dict[str, object]:
    return dict(request.app.openapi())


@router.get("/docs/init.js")
def swagger_init(_: Auth) -> FileResponse:
    return FileResponse(Path(__file__).with_name("swagger.js"), media_type="application/javascript")


@router.get("/docs/assets/{name}")
def swagger_asset(name: str, _: Auth) -> FileResponse:
    if name not in ASSETS:
        raise HTTPException(404, "unknown documentation asset")
    for directory in (cfg.home / "frontend/dist/api-docs", cfg.home / "frontend/node_modules/swagger-ui-dist"):
        path = directory / name
        if path.is_file():
            media_type = "application/javascript" if name.endswith(".js") else "text/css"
            return FileResponse(path, media_type=media_type)
    raise HTTPException(503, "Build the frontend to install the local API documentation assets")

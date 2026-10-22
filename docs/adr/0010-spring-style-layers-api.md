# ADR 0010 — Familiar backend layers and a versioned API explorer

Status: accepted (2026-10-11)

## Context

The owner prefers the controller → service → repository → entity structure familiar from Spring Boot. The earlier split still separated route mappings from controllers and left SQL in services. Swagger was disabled. These choices made the application difficult to learn.

## Decision

Use singular `controller/`, `service/`, `repository/`, `entity/`, `dto/`, `config/` and `middleware/` packages. Endpoint decorators belong directly above controller functions. `api/v1/router.py` registers feature controllers; `main.py` assembles FastAPI; `server.py` starts Uvicorn. The shared Services facade retains the existing public operations and transaction/context-manager boundaries. Feature services call named repositories sharing that Database instance. SQL, dynamic query construction, advisory lock connections and deliberately independent admission/pacing writes live in repositories. Pure rules remain in `domain/`; filesystem/browser adapters remain separate.

Entities describe database records without adding an ORM. Job contains the jobs table columns; ProfileEntry contains one profile row. Repositories use these types for complete records; partial SQL projections remain dictionaries. DTOs describe HTTP input/output. SQL migrations still own schema creation and changes; this reorganization requires no migration.

The canonical prefix is `/api/v1`. Existing `/api` routes invoke the same controllers and retain the same payload/authentication contracts, but are excluded from the public schema. The dashboard, SSE client and generated frontend types use v1. API operations are grouped by feature in OpenAPI.

Serve authenticated Swagger at `/docs` and its schema at `/openapi.json`. Both use the existing session/bearer checks. Top-level navigation to `/docs` bootstraps the owner's browser session using the same rule as the dashboard. Swagger assets and initialization are hosted locally; no CDN, remote schema validator, install telemetry or weaker script policy is needed. The frontend build copies the package's assets to `frontend/dist/api-docs`; the initialization script is packaged with Python. Swagger adds CSRF headers to owner-initiated writes; all approval and origin checks remain enforced by controllers/dependencies.

## Validation

Check every original operation's input/output schema against its v1 equivalent, ignoring version-derived titles/operation IDs and added tags. API regression tests cover aliases, documentation access, bearer approval rejection, CSRF and request validation. Isolated PostgreSQL checks exercise profile saves, rollback, deduplication, tracker updates/import enrichment/merge/archive/restore/purge and entity round trips. Ruff, strict mypy, frontend checks/build and dependency audits apply.

## Consequences

An engineer can follow one feature through consistent layer folders, with HTTP mapping visible in its controller. Existing clients remain compatible while new development uses v1. Swagger is available directly in the local browser session. Documentation assets must be included by the normal frontend build. Adding an entity does not automatically create a database table.

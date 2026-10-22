# ADR 0003 — FastAPI (Python) backend replaces Hono/Node

Status: accepted (2026-10-10)

## Context
The first scaffold used Node + Hono + Drizzle. The owner asked for FastAPI. The HTTP layer is a small part of the backend: services, the `jp` toolbox, workers and verifiers sit behind it. Splitting those across two languages would duplicate rules (ids, state machine, gates).

## Decision
The whole backend is Python 3.12: FastAPI + psycopg 3 (raw SQL) + pydantic v2. Job ids, canonical URLs and draft hashes were ported byte-for-byte (verified: same id `j_b7iore55`, same draft hash) so existing data keeps working. Migrations are plain SQL files applied by `jobpilot.migrate`. The frontend stays Vite/React and talks to the FastAPI HTTP API; the former `shared/` zod package is removed (API shape is defined by the backend's pydantic models).

## Consequences
+ One language for rules, API, CLI and workers; subprocess work (agent-browser, tectonic, claude) is natural.
+ Same security model: loopback, Host/Origin allowlist, CSRF, approvals only via the UI session route.
- Two toolchains (Python for backend, Node for frontend).

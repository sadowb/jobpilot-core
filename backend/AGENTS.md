# backend

Python 3.12, FastAPI, psycopg 3 (raw SQL, pooled), pydantic v2. Package `jobpilot` in `src/jobpilot`. Venv: `backend/.venv`.

- `domain/` — pure rules (ids, state machine, next-step, answer matching, CV rules). **No imports** of db, http, subprocess, pathlib IO or browser code. Talks to the outside only via `domain/ports.py` Protocols.
- `repository/database.py` / `migrate.py` — pooled Postgres access; forward-only SQL migrations in `migrations/*.sql` (tracked in `schema_migrations`).
- `service/application.py` — typed Services facade, the single write path for job state (HTTP and workers use it). Feature implementations live in `service/{jobs,forms,approvals,cv,profile,browser,automation,reporting,projection}.py`; transactions stay on facade methods.
- `main.py` — FastAPI assembly; `server.py` starts Uvicorn. `api/v1/router.py` registers controllers; endpoint decorators live in `controller/`; `dto/` holds Pydantic request/response contracts; `middleware/` holds authentication, body limits and errors. `repository/` owns all service SQL; controllers call application, dashboard or tracker services. `entity/` holds typed DB records; `dto/` holds HTTP contracts. `/api/v1/` is canonical; `/api/` remains a hidden compatibility alias. Protected Swagger and schema endpoints are `/docs` and `/openapi.json`, with local assets. Loopback only; Host allowlist, Origin + CSRF for the UI session, bearer for programmatic reads. **Approvals are created only by the user-session route** (`/api/approvals/submit`).
- `worker/{app,tasks,runner,runtime,client,names}.py` — Celery + Redis execution (ADR 0012). Job worker (prefork), control worker (threads for dispatch and sweeps) and beat scheduler, all Compose services, and FastAPI Celery client. Claims in PostgreSQL under lock via `dispatch()`, publishes job IDs over Redis broker lossy pipe; atomic start (`start_run`) with `FOR UPDATE SKIP LOCKED` lease + heartbeat; never holds a DB transaction while a job runs. `service/automation.py` owns persisted browser pacing, daily admission budgets and browser backpressure (`maxOpenBrowsers`).
- `boards/` — per-ATS adapters (`matches`, `canonicalize`, confirmation patterns, default automation policy).
- `browser/` — `neo.py` (Neo MCP client), `tabs.py` (tab binder), `reaper.py`, injected JS, deterministic verifiers.
- `projection/` — DB -> Obsidian vault (generated `Job.md`), content-addressed CV store + symlinks, single-writer git committer.

Commands (from repo root): `docker compose up -d`; checks: `backend/.venv/bin/ruff check backend/src`, `backend/.venv/bin/mypy backend/src`.

Read `docs/codebase-guide.md` for the request flow, SQL schema and transaction model. DB tables are defined by `backend/migrations/`, not ORM classes. SQL belongs in repositories; transaction/business rules belong in services. Repositories share the service's Database; do not open a separate connection for transactional writes. The explicitly independent pacing/admission queries retain their own short connections.

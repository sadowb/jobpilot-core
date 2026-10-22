# JobPilot backend

Python 3.12 + FastAPI + Pydantic + psycopg 3 + native PostgreSQL. The layout follows the controller, service, repository and entity model familiar from Spring Boot.

```text
backend/
  src/jobpilot/
    main.py                FastAPI application and middleware setup
    server.py              Uvicorn server entry point
    api/
      v1/router.py         Register version 1 controllers
      documentation.py     Authenticated Swagger UI and OpenAPI endpoints
      dashboard.py         Serve the React dashboard
    controller/            Endpoint decorators, HTTP inputs and responses
      jobs.py
      profile.py
      tracker.py
    service/               Business operations; calls repositories
      application.py       Shared transactional entry point for API, CLI and workers
      jobs.py
      profile.py
      tracker.py
    repository/            SQL reads/writes and PostgreSQL locks
      database.py          Connection pool and nested transactions
      jobs.py
      profile.py
      tracker.py
    entity/                Typed database records: Job, ProfileEntry
    dto/                   Pydantic HTTP request/response contracts
    config/                Environment settings and dependency wiring
    middleware/            Authentication, CSRF, body limits, error responses
    domain/                Pure application rules
    worker/                Separate scheduler process
    browser/, boards/      Job-site integrations
    projection/            Generated Obsidian files and immutable CV storage
    secrets/               Keychain integration
  migrations/              SQL table definitions and schema changes
  tests/                   API version and authentication regressions
  scripts/                 Contract export and maintenance/verification scripts
```

Read a feature vertically: `controller/profile.py` → `service/application.py` → `service/profile.py` → `repository/profile.py` → `entity/profile.py`. The application facade keeps transaction boundaries shared by HTTP, CLI and workers. Call its public methods for writes, rather than internal feature objects.

`entity/` contains typed database records, not an ORM. `dto/` contains the data sent over HTTP. Table creation and changes belong to `migrations/`; adding an entity class does not create a table. Repositories execute SQL on the same transaction-aware Database instance used by the service. Domain rules have no database/HTTP imports.

From the repository root:

```bash
pnpm install
pnpm build:frontend
bin/jp-migrate
bin/jp-server
```

- Dashboard: http://127.0.0.1:4100
- Swagger API explorer: http://127.0.0.1:4100/docs
- OpenAPI JSON: http://127.0.0.1:4100/openapi.json
- Versioned endpoints: `/api/v1/...`

Open `/docs` in your browser to establish the local session. Swagger lists endpoints by feature, including parameters, request bodies and response schemas. Its assets are local; the schema and requests use the existing authentication and CSRF protections. The frontend build copies Swagger assets into `frontend/dist/api-docs/`.

Existing `/api/...` URLs remain compatibility aliases, using the same controllers, contracts and authentication. The dashboard and newly generated types use `/api/v1/...`; Swagger shows the versioned routes once.

If the launchd server already owns port 4100, use it instead of launching another server. `bin/jp-worker` starts the separate scheduler when needed; dashboard Start/Stop controls whether it may admit work.

```bash
backend/.venv/bin/ruff check backend/src backend/scripts backend/tests
backend/.venv/bin/ruff format --check backend/src backend/scripts backend/tests
backend/.venv/bin/mypy backend/src backend/scripts backend/tests
backend/.venv/bin/python -m unittest discover -s backend/tests -v
```

Read [the codebase guide](../docs/codebase-guide.md) for a request walkthrough and database explanation. The architecture decision is [ADR 0010](../docs/adr/0010-spring-style-layers-api.md).

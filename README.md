# JobPilot

Parallel, token-cheap job-application system: PostgreSQL + Obsidian vault + local dashboard + a FastAPI backend and Haiku 5.5 workers that drive your BrowserOS neo browser over MCP. Runs as a Docker Compose stack.

- Architecture (current): [`docs/architecture.md`](docs/architecture.md); original plan: [`docs/original-plan.md`](docs/original-plan.md)
- Developer guide for coding agents: [`AGENTS.md`](AGENTS.md)
- Runtime agent guide: [`agent/NEO.md`](agent/NEO.md)

For a walkthrough of the frontend, backend and PostgreSQL, read [the codebase guide](docs/codebase-guide.md). Backend-specific entry points are listed in [backend/README.md](backend/README.md).

## Quick start (Docker Compose, no launchd)
Needs Docker Desktop running and BrowserOS Neo listening on port `NEO_PORT` (default 9010, MCP at `/mcp`).
```bash
cp .env.example .env                # fill in secrets; set CLAUDE_CODE_OAUTH_TOKEN (run `claude setup-token` yourself)
docker compose --profile build run --rm web-build   # build the dashboard once (frontend/dist)
docker compose up -d                # postgres, redis, migrate, server, worker, control, beat, backup
open http://127.0.0.1:4100          # dashboard; enable automation with UI Start
docker compose stop                 # stop everything (data stays in the pgdata volume)
```
Ports are published on 127.0.0.1 only. Containers reach Neo through a loopback relay (Neo rejects non-loopback Host headers); see [ADR 0019](docs/adr/0019-neo-browser-and-compose.md) for the model, residual risks and the Neo exposure note.

### Scale and operate
```bash
docker compose up -d --scale worker=2   # more worker containers; capacity is the sum of their pools
pnpm backup                              # DB dump + vault bundle now (daily by the backup service); restore: ADR 0022
```
`WORKER_CONCURRENCY` (default 4) sizes each worker's pool and caps admission; see [architecture](docs/architecture.md#concurrency-one-source-of-truth).

### Native development (no containers)
```bash
uv venv --python 3.12 backend/.venv
uv pip sync --python backend/.venv/bin/python backend/requirements-dev.lock
uv pip install --python backend/.venv/bin/python --no-deps -e backend
bin/jp-migrate && pnpm install && pnpm build:frontend
bin/jp-server & bin/jp-control & bin/jp-worker & bin/jp-beat   # needs your own Postgres and Redis (see .env.example)
```

Personal data (`data/`, `.env`) is gitignored.

Only the owner can create submit approvals. Final Submit is governed by agent instructions and server detection; the browser has no submit guard. Drafts and confirmations are agent-reported, not code-verified. An unapproved submission report is fenced against repeat submission, stops automation and surfaces in Needs You. See [ADR 0020](docs/adr/0020-neo-only.md) and [security](SECURITY.md).

Open `http://127.0.0.1:4100` in a normal browser. The signed 30-day dashboard cookie survives server restarts and reboots using the private `data/.session-secret` file.

Compose uses `restart: unless-stopped` for crash recovery. Apply migrations before workers (the `migrate` service does it).

Automation Start/Stop persists in PostgreSQL and survives API restarts. Celery + Redis execution (ADR 0012) separates concerns: the `control` service runs `dispatch` (claims in PostgreSQL under the `automation` row lock, then publishes `run_job` to Redis), node heartbeats, and browser/projection sweeps; the `worker` service preforks agent slots (`WORKER_CONCURRENCY`, default 4); the `beat` service runs a safety-net dispatch every 5 s and daily partition maintenance. Stop prevents new claims and releases jobs after the current step finishes. SIGTERM triggers Celery warm shutdown: touches `data/run/<node>.draining` and the runner releases `queued` at the step boundary (600 s drain timeout).

Workers drive your BrowserOS neo browser with one recorded tab per job (`browser_sessions.target_id`); the reaper only reconciles rows whose tab disappeared and never closes tabs it did not open. Filled forms waiting for approval keep their tab parked. When open browsers reach `maxOpenBrowsers` (default 8 = 4 agents + 4 parked), dispatch pauses preparation jobs until the user approves or dismisses some. Submit runs for approved jobs are always admitted. The per-domain gap is stored through `/api/settings`; there are no daily cost/token limits (ADR 0025). `/api/automation` exposes `workerOnline`, `queueOnline`, `browsersOpen`, `browserLimit`, `capacity`, `idleReason`, and a `monitoring` object (spend today/7 days/all time, active runs, queue depth, recent failures).

`backend/requirements.lock` is the hashed runtime set (what the Docker image installs); `backend/requirements-dev.lock` adds mypy, ruff and celery-types for development and CI and is constrained by it. Refresh both with `cd backend && uv pip compile pyproject.toml --generate-hashes --python-version 3.12 -o requirements.lock && uv pip compile pyproject.toml --group dev --generate-hashes --python-version 3.12 -c requirements.lock -o requirements-dev.lock`. See [security](SECURITY.md).

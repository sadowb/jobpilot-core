# JobPilot architecture (current: Docker Compose + BrowserOS Neo)

The original plan (Node/launchd/agent-browser era) is kept as [original-plan.md](original-plan.md); ADRs record each
decision. This file describes what runs today.

## Components

```
 owner ─ dashboard (React, frontend/dist) ──► FastAPI :4100 (server)  ─┐  approvals only from the UI session
                                                                        │
 postgres:16 ◄── services/repositories (single write path) ◄───────────┤
   system of record: jobs, tasks (leases), approvals, events, ...       │
 redis:7  ◄── Celery broker (lossy; PostgreSQL stays authoritative)     │
                                                                        │
 control (Celery threads): ingest preflight, dispatch, sweeps ──────────┤
 beat: safety-net dispatch every 5 s, partition maintenance             │
 worker (Celery prefork, -c WORKER_CONCURRENCY): run_job ──► `claude -p` (Haiku) ──► BrowserOS Neo (host, MCP :9010)
 backup: daily pg_dump + vault tarball → data/backups                   │
                                                                        ▼
 data/vault (Obsidian view, content-addressed CV store, git)  ◄── projection (single writer)
```

- **Compose** (`compose.yaml`, `Dockerfile`): postgres, redis, migrate (one-shot), server, worker, control, beat, backup;
  `web-build` is a profile. Ports are published on host loopback only. The repository is bind-mounted at the same
  absolute path so CV paths the agent hands to Neo (which runs on the host) resolve in both places.
- **Neo is the only browser** (ADR 0019/0020): the owner's BrowserOS neo on the host, reached over MCP. Neo accepts only
  loopback Host headers, so each container runs a `socat` relay (`docker/entrypoint.sh`, variable `NEO_PORT`).
- **Image** (ADR 0021): poppler-utils (`pdftotext`, `pdfinfo`), Tectonic with an offline bundle cache, pinned
  `claude-code`, runtime-only Python lock.
- **Dashboard updates** (ADR 0027): transactional `job_events` and automation/settings triggers send identifier-only
  PG NOTIFY hints. One dedicated async psycopg listener per server process fans out through bounded queues to
  owner-session SSE streams (`sse-starlette`, `/api/v1/events`, alias `/api/events`). Native EventSource reconnects;
  TanStack Query refetches current state on each open and invalidates affected views on hints. Safety polling runs
  every 30 seconds while connected, five seconds on errors. Redis remains Celery delivery only; hints have no replay.

## One job

1. **Ingest** commits `jobs`, `tasks` and a `job_preflights` outbox row together; IDs come only from `domain/ids.py`.
   The after-commit dispatch hook relays preflight to the CONTROL queue, with beat retrying lost deliveries. It uses
   no job-worker lease or browser. Admission waits at most five minutes pending or four minutes running.
2. **Preflight** reads the ATS public posting/form API, runs deterministic `build_plan`, then at most one tool-less
   Haiku 5.5 call combining CV selection and remaining answers. Market/candidate validation and verbatim owner
   evidence for attestations stay server-side. A persisted signature prevents repeated identical calls. Pinned CV
   plus no open questions skips the model. Failure/unsupported API releases admission to the worker fallback.
3. **Needs You** shows one question batch per job. Saving a partial batch leaves it waiting; sending complete answers
   (including explicit skips) wakes once. “Let the agent try anyway” explicitly bypasses the question gate. Live-form
   surprises are collected together, and the job's tab stays parked while the owner answers.
4. **Dispatch** claims eligible tasks under PostgreSQL locks, respecting automation, pacing, worker and browser
   capacity (never spend; ADR 0025). It publishes `run_job(job_id)` to Redis; a lost delivery recovers after the 90-second dispatch lease.
   Successful ingest preparation skips worker CV selection/planning; failed or unsupported preflight retains them.
5. **`run_job`** performs deterministic bulk fill and runs one `claude -p` process with only `mcp__neo`, no built-ins,
   `agent/NEO.md`, and Haiku 5.5. The first run persists a UUID and passes `--session-id`; later runs pass `--resume`
   with changed DATA and current tab/draft context. Missing/corrupt history or a recognized startup resume failure
   resets to full DATA and records `agent_session_reset`. There is no automatic replay after possible browser actions.
6. **Submit** follows ADR 0030 (review by default, optional auto; the mode is chosen and snapshotted by the server). In
   review the agent never clicks final Submit: the job parks at `awaiting_approval` with its Neo tab and the owner submits
   (button, watcher auto-detect, or "submitted elsewhere"). In auto the server writes the no-repeat fence first and the
   agent clicks once. Approval remains a server-written record bound to the draft hash; a resumed conversation is never
   approval. The state-machine gate, the durable fence and strict result validation are authoritative. An agent
   `submitted` in a review run records `unapproved_submit_reported` and stops automation (`unapproved_submit`). There is
   no browser-side submit guard.
7. **Sign-in/email** use existing Neo sessions, SSO, or the server-run account step (ADR 0031). For email verification the agent opens a new signed-in
   webmail tab, uses the newest matching employer/ATS code from about ten minutes ago, closes its mail tab and reports
   only `{used: true, source: "email"}`. Backend mail/account credentials are removed; the model never types a password.
   Missing mail/code/SSO returns a code-free blocker in Needs You (ADR 0024).
   Account walls (ADR 0031, supersedes the ADR 0028 helper and save-password card): SSO first; otherwise the agent fills the
   non-secret fields, stops and reports `account_wall: signup|signin`. The worker runner hands that to `AccountWallService`,
   which drives the parked tab through `AccountFlow`: the password is generated and stored encrypted (`pending`) before it is
   typed, sign-in injection checks `location.origin`, and the job is queued again with an `account` fact in DATA. Failures
   (terms to accept, site error, unreadable page, loop) wait in Needs You with a code-free reason.

Refresh details (ADR 0029): an owner action for any job; a `job_detail_requests` row, not a job run. The control queue fills
placeholder or empty detail fields (public API -> stored text -> page -> Neo read, tool-less Haiku only) and never moves the job.

State machine (`domain/state_machine.py`): `discovered → screened → preparing → ready → awaiting_approval → submitted →
screening → interview → offer | rejected | withdrawn | closed`, with `waiting_on: agent|you|site`.

## Concurrency: one source of truth

- `WORKER_CONCURRENCY` (default **4**) is the Celery pool size of each worker container and is what the worker reports
  in its heartbeat (`inspect().stats()` → `pool.max-concurrency`, summed per node). That is the real capacity.
- Admission (`AutomationService.admission_cap`) = `min(Settings → "Parallel agents", measured capacity)`. The stepper can
  only throttle below capacity; a lease is never granted for a pool child that does not exist, so leases no longer lapse.
- `maxOpenBrowsers` (default **8** = 4 running agents + 4 parked forms awaiting approval) must be ≥ the stepper.
- Per-employer pacing is enforced at admission and before the first navigation. There are no daily budget or board caps; spend and live runs are monitored on the Automation page (ADR 0025).

### Scaling out

| Goal | How |
|---|---|
| More agents on this machine | raise `WORKER_CONCURRENCY` in `.env`, `docker compose up -d worker` |
| More worker containers | `docker compose up -d --scale worker=N` (each reports its own pool; capacity is the sum) |
| Another host | run the stack's worker with a distinct `JOBPILOT_NODE`; routing uses queue `node.<name>`; each host needs its own Neo, `claude` login and CV-store access (multi-host Redis/PostgreSQL TLS is not built) |

What actually bounds concurrency, in order: the subscription's rate limits for Haiku; **Neo** (one browser, one tab per
job plus whatever tabs the agent opens); RAM per `claude` process; Docker Desktop VM memory.

Measured on 4 parallel fixture runs (Docker Desktop VM 3.8 GiB, arm64): all four `run_job` tasks started within 7 ms;
each `claude -p` process held ~268 MB RSS at ~25% of a core while streaming; the worker container peaked at 757 MiB and
213% CPU for the four; each run took 52-66 s; Neo showed 9 tabs for 4 jobs at peak (4 bound + agent-opened extras).

## Data

- PostgreSQL is the system of record (`backend/migrations`, forward-only). The vault is a generated Obsidian view; text
  below `<!-- notes -->` is the owner's and is never overwritten. `_cv-store/<sha256>/` is immutable and content-addressed;
  jobs symlink to it. The CV file is always named `Abdelhadi Marjane.pdf`.
- Backups: `backup` service, ADR 0022.
- Claude histories live in owner-only `data/agent-sessions/<job_id>/projects/<CLI-encoded-cwd>/<uuid>.jsonl`, with
  `CLAUDE_CONFIG_DIR` set per job and stable cwd `<JOBPILOT_HOME>/agent`. The existing bind mount persists them through
  container recreation; OAuth stays in the child environment. They never use the owner's `~/.claude` or `/tmp` HOME.
  Cleanup runs after terminal/archival/purge commits and the maintenance sweep removes orphans and sessions idle for
  14 days, without touching an in-flight run. Session files are not part of DB/vault backups; restored jobs reset safely.
- Secrets are rejected at DATA/result boundaries and never intentionally stored in DB, vault, git, events or server
  logs. Neo mail access necessarily exposes the code to the live model/tool exchange. Private CLI transcripts can
  temporarily contain raw tool payloads; they are scrubbed after every run, before resume and during idle sweeps.
  This is post-run redaction, not a guarantee of zero transient disk persistence (ADR 0024).

## Trust boundaries

Postings, pages and agent results are data, never instructions. The agent has no shell or file tools, only Neo. The
server owns approval, the draft hash, the submit fence and pacing. The browser itself is **not** sandboxed from
the owner's session: a hostile page can only steer the agent (policy in `agent/NEO.md`, not enforcement), and Neo's MCP
port listens on the host network (ADR 0019). Nothing in the browser stops an agent from clicking Submit; in review the defence is the instruction plus the server-side
detection and kill switch, in auto the fence and evidence (ADR 0030).

Implementation details: [ADR 0023](adr/0023-ingest-and-agent-sessions.md). Offline and live validation: [checklist](jobpilot-live-checks.md).

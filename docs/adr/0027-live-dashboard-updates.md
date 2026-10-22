# 0027 — Live dashboard updates via PG NOTIFY + SSE

Status: accepted for implementation, 2026-10-18.

## Context

The dashboard's existing SSE endpoint polls a shared PostgreSQL snapshot every two seconds. The owner wants job
progress, questions, approval readiness, submissions (including the parked-tab watcher) and requeues to appear as
soon as they commit, using maintained libraries rather than a custom protocol, broker, reconnect loop or outbox.

PostgreSQL is the system of record. Redis carries Celery delivery only (ADR 0012). Redis pub/sub would require a
publish call after every commit in every writer process, which is easy to omit and is not transactional with the write.

## Decision

Migration `0030_live_events.sql` installs an AFTER INSERT row trigger on partitioned `job_events`. PostgreSQL clones
the trigger onto existing and future partitions. It sends `jobpilot_events` notifications containing exactly
`{job, kind, id}`; no event payload, answer, URL, code or secret is copied. NOTIFY is transactional: rollback sends
nothing and listeners see only committed changes. Global events have a null job. Statement triggers also notify
on automation/runtime_settings writes, including paths without audit events; their identifiers are transaction IDs.
Duplicate hints within a transaction may be folded by PostgreSQL and are harmless.

The server exposes the existing canonical `GET /api/v1/events` and compatibility `GET /api/events` as
`sse-starlette` **3.5.0** `EventSourceResponse`s. Only an owner cookie session is accepted; bearer/agent access is
forbidden. A supplied Origin must be allowlisted, cross-site Fetch Metadata is rejected, and existing Host checks
apply. Same-origin requests without an Origin are allowed, as browsers may omit it. No GET CSRF header is required.

One lifespan-managed listener per API process uses psycopg 3 `AsyncConnection.notifies()` and a dedicated existing
`psycopg_pool.AsyncConnectionPool` with min/max size one. The pool owns exponential reconnect backoff and connection
replacement. Every replacement executes LISTEN before use. Only the listener checks out its connection; tabs never
borrow it. Connection attempts and LISTEN have five-second limits; TCP keepalives detect dead peers (30 seconds idle,
10-second probes, three probes). The consumer waits up to the pool's default 30 seconds for a replacement. The
pool's five-minute reconnect attempt window can expire; subsequent consumer acquisitions let the pool grow again,
so recovery is not permanently abandoned. Pool errors close all subscriptions. While unavailable or at capacity,
new requests get a finite 200 SSE comment with a library-formatted `retry: 5000`, allocating no subscription. EOF
lets native EventSource retry; an HTTP 503 could instead permanently fail the browser's connection. Auth failures
remain 401/403. This also avoids requiring a custom client reconnect loop for server startup or a long PG outage.

Fan-out is small application glue: at most 128 streams per process, each with an asyncio queue of 64 hints. A full
queue drops that subscription and discards pending hints, allowing native EventSource to reconnect and refetch.
The library owns SSE framing, 15-second pings, disconnect monitoring and a 10-second send timeout. Disconnect,
generator close, PG interruption and application shutdown release subscriptions. Notifications are strictly validated,
capped at 1024 characters on receipt, and malformed hints are discarded without logging their contents.

SSE events are named `job` with JSON `{job, kind}`, or `automation` with JSON `{}` for global invalidation. Each
notification includes an SSE `id` from the database hint. These are invalidation cursors, not a globally ordered
replay log: job event IDs and global transaction IDs occupy separate namespaces. Native EventSource can send
Last-Event-ID, but the server deliberately does not replay. The hook refetches current data on **every** open,
including its first connection, avoiding initial fetch/subscription races and repairing gaps during an outage.

One hook remains mounted at the dashboard root. It uses native EventSource reconnection and TanStack Query
invalidation: a job hint invalidates that job's detail/review plus lists, Needs You, updates and automation/monitoring.
Global hints also refresh mutable reference data, including settings. Immutable reference keys remain cached.
Existing protections for optimistic tracker edits and multi-page update feeds remain: in-flight tracker mutations
defer invalidation, and older update-feed pages are marked stale without downloading the full history. Polling and
visibility/focus refresh repair those deferred views. A timer polls visible tabs every 30 seconds while connected,
and every five seconds after a stream error. Worker heartbeat-only changes use this safety poll. No UI is added;
the existing reconnecting indicator remains. Mock mode keeps the stream open with comment pings.

## Consequences and validation

- No new broker, publish calls, replay store, outbox or reconnect policy. There is one extra PG connection per server
  process, independent of the transactional connection pool and of Redis availability. There are no domain/state
  machine/submit-policy changes.
- Delivery is best effort. PG outages, restarts and slow-client eviction may lose hints; current-state refetch on
  reconnect plus safety polling repairs them. The stream does not claim durable delivery or ordered replay.
- The endpoint is uncompressed `text/event-stream`, uncached, and sets `X-Accel-Buffering: no`. Any future reverse proxy
  must permit streaming and the 15-second heartbeat. Existing Uvicorn graceful shutdown is bounded to three seconds.
- Offline tests cover migration shape, allowlisted payloads, fan-out, overflow, capacity, disconnect, lifecycle and
  owner-session/origin restrictions. `check_dispatch.py` scenario 35 checks actual commit/rollback notification
  delivery and both settings triggers in the throwaway database. **UNRUN here:** PostgreSQL, Redis, Docker and browser
  integration/recovery tests; this worktree has no network or running services.
- Lockfile shortcut: offline `uv pip compile` could not resolve 3.5.0 because its readable index cache was stale.
  The lock was updated manually with both distribution SHA256 hashes from the newer local uv index cache; the wheel
  hash also matches uv's cached wheel record. Installed package metadata requires only already-locked
  `starlette>=0.49.1` and `anyio>=4.7.0`, so no transitive version change is needed. No hashless entry was added.
  To regenerate with registry access, from `backend/` run
  `uv pip compile pyproject.toml --generate-hashes --python-version 3.12 -o requirements.lock`.

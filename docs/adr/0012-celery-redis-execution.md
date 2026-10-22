# 0012 — Celery + Redis job execution

Status: accepted by user decision, 2026-10-11 (contract frozen in migration 0016 and `worker/names.py`; implementation in later commits).

## Context

Execution today is a hand-rolled pool: `worker/main.py` polls the `automation` flag and `worker/scheduler.py` runs N threads that each poll PostgreSQL for a lease. It works on one host and cannot span several. The owner wants the standard shape — producer → broker → workers → results in PostgreSQL — with one job link = one task = one Haiku agent with its own browser, three agents on this Mac to start, and more hosts later by pointing additional workers at the same broker and database.

## Decision

**Celery with a Redis broker. PostgreSQL stays the system of record.** Redis carries only delivery; every durable fact (job state, lease ownership, approvals, pacing, budgets, events, costs) remains in PostgreSQL.

### Tasks (`worker/tasks.py`)

| Task | Queue | Role |
|---|---|---|
| `dispatch()` | `control` | The only producer of work. One transaction under the existing `automation` row lock (same lock as Start/Stop/configure) runs the claim SQL while `in-flight < workerConcurrency` and open browsers `< maxOpenBrowsers`, marks each row `leased` (`leased_by = <task id>`, `leased_node`, 90 s dispatch lease), then after commit publishes `run_job`. A failed publish releases the lease; dispatch does not charge an attempt (see Admission). Triggered after commit by the API (ingest, approve, Start), by a finishing `run_job`, and by beat every 5 s as the safety net. |
| `run_job(job_id)` | `jobs` or `node.<name>` | Atomic start (`started_at IS NULL AND leased_by = <task id>`; a duplicate or stale delivery updates 0 rows and exits), lease extended to 600 s, existing job loop, then `finish_run` (release plus a `task_finished` event) and `dispatch.delay()`. |
| `node_ping()` | `control` | Node heartbeat only (body run every 5 s by the control worker's heartbeat thread): two `worker_processes` rows per node, see *Liveness*. Browser / projection sweeps stay in the existing `sweep_loop` thread of the control worker. |
| `ensure_partitions()` | `control` | Daily `job_events` partitions, replacing the timer in `worker/main.py`. |

`dispatch` is a transactional-outbox relay: the `tasks` rows are the outbox and Redis is a lossy pipe. A message lost anywhere is re-published when the 90 s dispatch lease expires.

### Liveness

`publish_worker(name, status, node=node)` writes two rows per node every 5 s:

| Row | Status JSON | Meaning |
|---|---|---|
| `{node}:control` | `{"role":"control","node":N,"offline":<true when the control worker did not get its own ping back through Redis>}` | The row is written by the control worker's heartbeat thread even while Redis is down (PostgreSQL still works), so `offline` carries the broker state: **`queueOnline`** = fresh and not offline. |
| `{node}:jobs` | `{"role":"jobs","node":N,"slots":S,"offline":<true when the job worker did not answer a Celery remote-control ping>}` | **`workerOnline`** (fresh and not offline) and **`capacity`** (sum of `slots`). An `offline: true` write keeps the previous healthy time in `status.okAt`, so one missed ping ends *alive* but starts the takeover clock instead of ending *recent*. |

A row is fresh for 20 s (`HEARTBEAT_FRESH_SECONDS`). Celery remote control therefore stays **enabled** on the job worker (`worker_enable_remote_control` is not turned off): the ping is how the control worker learns that the prefork pool is alive. Started / finished / failed on the dashboard are counted from today's (UTC) `task_finished` events, not from in-process counters.

Migration `0018_drop_retired_worker_rows.sql` deletes retired thread-pool rows
whose names match `^worker-[0-9]+$`. The deletion is idempotent. All heartbeat
readers in automation and reporting exclude those names regardless of their role,
node or freshness, so they cannot contribute capacity, queue/browser liveness or
node takeover decisions if reintroduced.

### Admission (one implementation for Celery and `jp next`)

`dispatch_claim` and `claim()` share one admission path under the `automation` row lock: automation flag (off = read-only capture of already-applied jobs only), daily budget, attempts, board caps, then `workerConcurrency` (live leases cluster-wide, dispatched-but-unstarted ones included) and `maxOpenBrowsers` (open sessions plus running leases, cluster-wide; counted per job so a parked form and its run are one browser). A job that needs no new browser is exempt from the browser cap: an approved or clicked submit run, or one whose open session the run will reuse, i.e. a session on a node that is online and therefore receives the run. NULL-node rows and this node's rows without a target are legacy: they occupy no tab capacity and exempt no preparation run (ADR 0014). Remote rows remain the owning node's responsibility; this node neither probes nor discards them. The same predicate is applied by `start_run`, so dispatch and start never disagree. A session on a node past the takeover window occupies nothing and exempts nothing: it is not counted toward the cap and its job takes a new browser like any other. The cap is browser-only; the board's daily preparation quota still applies to approved submit runs, as before.

The job's **attempt** is charged by `start_run` (or by `claim()`), never at dispatch: a lease no worker ever picked up (job workers down, control and beat up) expires and is re-dispatched without burning the five attempts that would block a job no agent ever ran. `start_run` also requires an unexpired lease (`visible_at > now()`): a delivery that outlived its 90 s dispatch lease is refused, because dispatch no longer counts it and may have admitted a replacement, and starting both would exceed `workerConcurrency`. The count-then-start of the node browser cap runs under a per-node transaction advisory lock, so concurrent prefork children cannot all pass the same count. The agent subprocess receives the worker's effective `JOBPILOT_NODE` explicitly, because the variable may exist only in the worker's environment.

The board's daily *preparation* unit is taken by a successful `start_run` (or by `claim()`, which runs in-process), never at dispatch, so a failed publish or a bounced start cannot leak quota. Dispatch may therefore admit slightly more than the quota has left; the surplus starts bounce back to `queued`, attempt refunded. `start_run` rechecks the cap for the **node** it landed on, because parked forms may have filled it since dispatch: a preparation run with no session on that node is released `queued` with the attempt refunded and `start_run` returns false. `configure_runtime` rejects `maxOpenBrowsers < workerConcurrency` (409), since every running agent holds a browser.

Admission neither reads nor writes `domain_pacing` (ADR 0006 correction, 2026-10-12). The first browser action of each admitted job reserves its turn through `pace_browser`, sharing the same persisted queue with ongoing actions. Admissions alone add no pacing debt.

Shared ATS hosts pace per employer board (`host/tenant`), as defined by ADR 0006's per-employer correction and migration 0019. For a positive `domainGapSeconds`, the shared-host rate is at most `workerConcurrency / domainGapSeconds`: independent employers gain parallelism while one IP can send more requests to the ATS vendor. Daily board caps and budgets are unchanged.

### Delivery semantics

- Messages carry a job id and nothing else. Approvals, secrets, form values and instructions never transit Redis; `gate_submit` / `record_submit_clicked` still decide every submit from PostgreSQL.
- `task_acks_late = False`, `worker_prefetch_multiplier = 1`, and the Redis `visibility_timeout` redelivery is never relied on. Recovery is PostgreSQL lease expiry; duplicate deliveries are neutralised by the atomic start.
- `task_ignore_result = True`: nothing calls `.get()`, and a SQLAlchemy result backend would add a second ORM and tables duplicating `job_events`. Each run is recorded exactly once per task id by `finish_run` as `task_finished` (task id, node, outcome, seconds, refunded, released), also when the agent already released the lease with `jp done`; `released` says whether `finish_run` itself closed the lease. The first `finish_run` after an early release records `released=false`; an early release alone is never grounds to suppress that event. A transaction-scoped per-task lock serializes finishes, and only an existing `task_finished` event for that job and task id suppresses a genuine duplicate. Repeating `finish_run` preserves the original event and task row, so retries do not inflate dashboard counters. `run_job` calls `finish_run` exactly once and retries once with outcome `queued` if the done-gate raises; `leased_by` is the Celery task id and is the agent subprocess's `JP_WORKER`, so lease heartbeats work unchanged.
- Global `task_soft_time_limit` / `task_time_limit` are `None`: a `run_job` may fill an entire application. All dispatch/control tasks retain explicit 60 s soft/hard bounds. Job workers recycle children with `--max-tasks-per-child=50`.
- JSON serializer only.

### Long direct runs (owner decision, 2026-10-12)

"It shouldn't have a limit in run; let the agent by itself do the required things; these are long tasks, that is why I used Celery."

`browserRunTimeoutSeconds=0` and `browserBudgetUsd=0` are the new defaults; zero omits the subprocess deadline and CLI `--max-budget-usd`. Explicit non-zero values retain the former limits (timeout 30–540 s, USD up to 100). Existing persisted overrides remain effective. There is no CLI token/turn cap in direct mode. Toolbox action limits remain unchanged and its CLI also accepts zero budget/timeout.

While Claude runs, the same subprocess polling loop calls `Services.heartbeat` every 15 s, well below the 600 s lease. Each renewal uses its own short autocommit connection (5 s connect/statement bounds), with no transaction held during the run. It never renews while draining. Lost/expired ownership or a failed DB check terminates the subprocess; no attempt is refunded after the model starts. The monitor ends in process cleanup, so no background heartbeat can keep a dead run leased. Dispatch can recover after beats cease and the lease expires.

`agentStallSeconds` defaults to 900, zero disables it. Direct CLI output is `stream-json --verbose --include-partial-messages`, as supported by the installed `claude --help`. Only real stdout bytes reset the watchdog, including partial model output; our DB heartbeats and stderr do not. Intermediate events are discarded with a 2 MB per-event bound; only the final `type=result` envelope is retained for usage/error classification and its `result` text is passed unchanged to `parse_result`. No raw streaming events are stored/logged. Silence triggers `agent_stalled`, terminates the process group, and queues preparation with the existing attempt charge. Authorized submit termination/stall retains the durable click fence and becomes submission uncertain, never retried.

With unlimited default settings, the stall watchdog is the only automatic inactivity stop. Stop/drain ends active runs; lease loss fails closed. Daily USD/token caps stop **new admissions**, not already admitted work. Normal CLI failures and strict result validation still apply; optional non-zero per-run limits are owner-selected stops.

Unlimited budget reservations use `cvBudgetUsd` (default USD 0.25) as a finite **admission allowance**, plus the existing `runTokenReservation` (default 100,000). This is neither a run money cap nor a token cap. Claims conservatively check `max(browserBudgetUsd, cvBudgetUsd)` and calls reserve under the existing global budget lock. When daily enforcement is enabled, spent plus held plus allowance must fit both daily caps. Real reported usage replaces the reservation in the ledger even if it exceeds the allowance, then blocks further admissions as appropriate; one or several in-flight unlimited runs can overshoot the daily amounts. Missing usage settles once with the allowance/token estimate, explicitly labeled unknown. UTC-day rollover and the existing daily-budget enable switch remain unchanged. A run lasting across UTC rollover reports its real usage on the completion day.

### Browsers

Each agent opens its own session (`jp-<jobId>`), closed when the job ends. Exception kept from ADR 0011: a filled form waiting for approval keeps its browser while the agent slot is freed. Those parked browsers hold Chrome memory without holding a slot, so `dispatch` stops admitting new preparation jobs while open browsers `>= maxOpenBrowsers` (default 6 = 3 agents + 3 parked). This is backpressure only; nothing is evicted, and submit runs for approved drafts are always admitted. On approval, `dispatch` routes that job's `run_job` to `node.<name>` of the host that holds the browser. `browser_sessions.node` is set when a session opens; sweeps, `browser_is_open`, close and the orphan reaper act only on their own node's sessions (a NULL node is a legacy session any node may close). `awaiting_approval` retention and `_defer_close` are unchanged.

### Node identity

`JOBPILOT_NODE` (default `mac`, `^[a-z0-9][a-z0-9-]{0,31}$` because it is embedded in a queue name). Migration `0016_worker_nodes.sql` adds `tasks.leased_node`, `tasks.started_at`, `browser_sessions.node`, `worker_processes.node`. Browser sweeps and the reaper touch only their own node's sessions. While the node holding a parked browser is offline for less than 300 s (`NODE_TAKEOVER_SECONDS`) its job is **held**, not dispatched, so no duplicate messages pile up in a queue nobody reads. Past that, the job goes to the shared `jobs` queue and `Actions.submit` uses its existing guarded refill plus draft-hash check.

### Processes (launchd, native, no Docker)

| Agent | Role |
|---|---|
| `com.jobpilot.redis` | Homebrew Redis on `127.0.0.1:6380`, `requirepass` generated into `.env`, persistence off, `maxmemory 64mb` |
| `com.jobpilot.worker` | `celery worker -Q jobs,node.$NODE -P prefork -c $JOBPILOT_NODE_SLOTS` |
| `com.jobpilot.control` | `celery worker -Q control -P threads -c 4` |
| `com.jobpilot.beat` | `celery beat`, `dispatch` every 5 s |

Stop flips the PostgreSQL flag. SIGTERM triggers a Celery warm shutdown and touches `data/run/<node>.draining`. Direct subprocess monitoring checks drain every poll (0.2 s selector wait) and the automation flag every second, subject to bounded DB checks; it terminates Claude and its process group with SIGTERM, then SIGKILL after up to 5 s, and releases preparation jobs `queued`. Authorized submit runs go to the owner as submission uncertain. Toolbox runs keep their step-boundary drain behavior.

### Contract frozen by this commit

`worker/names.py` (task, queue and lease-time constants), `config/settings.py` (`jobpilot_node`, `jobpilot_node_slots`, `jobpilot_pool`, `redis_*`, `max_open_browsers`, default concurrency 3; `RuntimeSettings.maxOpenBrowsers`, a live setting), migration 0016, the `Services` methods `dispatch_claim`, `dispatch_failed`, `start_run`, `finish_run` (typed stubs) and `request_dispatch` / `dispatch_hook`, and the `Automation` response fields `browsersOpen`, `browserLimit`, `capacity`, `queueOnline`.

## Alternatives considered

- **Harden the PostgreSQL-only queue** (current model plus per-host workers). No new component and no lossy pipe, but multi-host workers still poll, there is no push dispatch, and the supervisor/pool code stays hand-rolled. Rejected by the owner in favour of the standard broker shape.
- **Celery with the SQLAlchemy/PostgreSQL broker.** Removes Redis but is experimental in Celery, polls, and adds a second ORM. Rejected.
- **Redis result backend / `acks_late`.** Both duplicate state PostgreSQL already holds and make Redis authoritative for something it may lose. Rejected.
- **RQ / Dramatiq / arq.** Lighter, but weaker routing (per-node queues), time limits and beat support for the same operational cost.

## Consequences

- Redis is intentionally non-durable (persistence off): a restart loses queued messages, and PostgreSQL leases recover them within 90 s plus the next beat tick.
- A Redis outage never fails a PostgreSQL write: `request_dispatch` logs and swallows broker errors; running jobs finish; dispatch resumes when Redis returns.
- The prefork pool **spawns** its children on macOS (billiard 4.3 `context.py:371-376`, bpo-33725) and forks on Linux, so no fork-safety workaround (`OBJC_DISABLE_INITIALIZE_FORK_SAFETY`) is needed or set. `worker/runtime.py` composes services lazily per pid, which covers both start methods; explicit control-task time limits and `--max-tasks-per-child` are unaffected. Celery 5.6.3 fills its trace cache in a child only when `FORKED_BY_MULTIPROCESSING` is set (`concurrency/prefork.py:67`), so a spawned child fails in `fast_trace_task` (`app/trace.py:760`); `worker/app.py` primes it from `worker_process_init` against the module app (the `FORKED_BY_MULTIPROCESSING` route also flips `USING_EXECV`, `app/base.py:541`, process-wide, and was rejected). Both start methods are exercised live. `JOBPILOT_POOL=threads` is the fallback; subprocess monitoring works in either pool, while Celery control-task hard limits require prefork.
- Multi-host is designed in, not deployed. It needs Redis and PostgreSQL over a private network with TLS, a Claude login per host, Keychain secrets and CV-store sync per host.
- New runtime dependencies: `celery[redis]` (plus `celery-types` in dev). The lock file is regenerated with hashes.
- `worker/scheduler.py` and `worker/main.py` are removed once `runner.py` and `tasks.py` replace them (git history keeps them).

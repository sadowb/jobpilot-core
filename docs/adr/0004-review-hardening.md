# ADR 0004: fence submissions and workers; bootstrap human sessions explicitly

Date: 2026-10-10. Status: accepted.

## Context

The Opus review found that a job could be submitted twice after an uncertain click, worker processes could invoke human commands, and individually committed SQL operations left partial state. Local processes could also mint dashboard sessions without an explicit bootstrap credential.

## Decisions

- A persisted `submit_clicked` event is a permanent fence for that job. Recording the event locks the job row and rechecks the gate before the browser click. Work orders then allow confirmation only. Failed confirmation hands the job to the user; neither a resume nor a new approval clears the fence.
- Worker subprocesses receive `JP_ROLE=agent`, `JP_JOB`, and `JP_WORKER`. The CLI enforces the command catalogue's agent flag and job scope; agent `next` never claims. Heartbeat/release require the lease owner and an unexpired lease. Five expired/requeued attempts require human intervention; explicit human resume resets attempts. Each worker generation owns its stop event, and start waits for previous threads to finish.
- Use psycopg transactions on one context-local connection for ingestion, transitions, approvals and application recording. Nested operations use savepoints. Project jobs after the outer commit; discard queued projections on rollback. The database remains authoritative if a later filesystem projection fails.
- Ready-state facts come from persisted verification results for the current draft, including an attachment check. A form with no file field requires manual review; it cannot pass automated verification.
- An authenticated user may attest to an application submitted manually, walking pre-submit transitions without creating an approval row. This exception cannot enable a browser submit: the live submit gate still requires a matching server-written approval. Post-submit status changes have a separate user-only route.
- Session bootstrap decision superseded by [ADR 0010a](0010a-open-dashboard.md): direct browser navigation establishes a signed 30-day session using fetch metadata, with a persistent signing secret. Bearer credentials cannot bootstrap human sessions. Access logging remains disabled.
- Isolate Claude runs with installed CLI flags: `--safe-mode`, empty `--setting-sources`, `--strict-mcp-config`, `--no-chrome`, disabled slash commands/session persistence, and an explicit tool set. Browser workers get Bash with the `jp` allowlist; CV calls get no tools. Safe mode preserves normal authentication, unlike `--bare`, which excludes OAuth/keychain authentication. Inject the runtime guide explicitly.
- Serialize vault note writes/renames with a filesystem lock across processes. Find the existing job directory by immutable ID suffix, move it on metadata changes, and preserve CV link destinations. Refuse already duplicated directories for manual reconciliation. Fence posting snapshots as inert text, remove notes markers from postings, and preserve notes after the last generated marker.
- Reject dangerous TeX commands in tailor regions and compile with Tectonic `--untrusted`. Compilation and tailoring outputs have scoped temporary-directory lifetimes.

## Consequences

No migration or frontend change is required. Restart the API to activate the backend changes, then open the new bootstrap URL. A lost/expired browser session requires restarting the server for another one-time token. The environment role is an application-level restriction backed by the worker's Claude tool allowlist; it is not an OS sandbox. Uncertain submissions and ambiguous pre-existing vault duplicates require a human decision. API/status operations still use the domain state machine.

# 0023. Ingest preparation and one browser conversation per job

Status: accepted (owner request, 2026-10-18)

Ingest creates a `job_preflights` outbox row in the job transaction. After commit, dispatch relays it to the control
queue. Duplicate deliveries cannot start twice. Browser admission waits for completion or expiry (five minutes
pending, four minutes running); outages and unsupported APIs fall back to worker preparation. No browser slot is used.

Public posting/form data feeds deterministic `build_plan`, then at most one tool-free Haiku 5.5 completion for both
CV choice and remaining answers. Candidate/version/market checks and verbatim owner evidence for attestations remain
server-owned. File uploads remain the browser's responsibility. A signature is recorded before the call. At most 60 questions reach the model; overflow remains in the
owner's batch. Invalid CV choices fall back, without a second ingest model call. Existing CV choices are preserved.

Required unknowns appear in one card per application. Send answers stores the batch in one transaction and resumes
once, only when required answers are resolved or explicitly skipped. The owner can explicitly let the agent try
anyway. Live forms collect all accessible unknowns together and preserve the parked question tab.

Each browser run starts one `claude -p` process. The first stores a server-minted UUID in `jobs.agent_session_id`
and passes `--session-id`; subsequent runs pass `--resume`. Per-job `CLAUDE_CONFIG_DIR` directories live beneath
`data/agent-sessions/` (0700, files 0600), on the existing persistent repo bind mount. Cwd is always
`<JOBPILOT_HOME>/agent`. OAuth stays in `CLAUDE_CODE_OAUTH_TOKEN`; neither the owner's Claude directory nor `/tmp`
is used for browser histories. The CLI's project-path component is discovered instead of reimplementing its encoding.

Only hashes of supplied DATA values are stored in `agent_session_context`. Resumed prompts carry changes and current
tab/CV/approved-draft context; removed material is explicitly withdrawn. Missing/corrupt transcripts reset with an
`agent_session_reset` event. A recognized resume startup failure permits one fresh full-DATA process; other errors
are not immediately replayed because a browser action may already have occurred. Existing submit recovery/fencing
still applies. Transcripts have no approval authority.

Terminal transitions, archive and purge schedule cleanup after commit. The existing sweep removes unmatched jobs
and histories idle for 14 days, using the job's advisory lock to skip running processes. Tool payloads and reasoning
are redacted before future reuse; see ADR 0024 for the transient-persistence limit. Lost history costs a full prompt,
not a lost application. Migrations 0023/0024 add the outbox, bypass flag, session UUID and DATA hashes; no schema drops.

Offline checks use filesystem/CLI/DB doubles. Local CLI 2.1.296 binary strings confirmed the flags, config-dir lookup
and resume-failure diagnostics; the image pins 2.1.302. Its live transcript layout, redacted-history compatibility,
OAuth and recreation behavior still require the [live checklist](../jobpilot-live-checks.md).

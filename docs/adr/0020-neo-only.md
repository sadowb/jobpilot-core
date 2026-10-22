# 0020. Neo is the only browser: removal of the older browser stacks

Status: accepted (owner decision, 2026-10-17); its submit-run and approval-gate parts are superseded by ADR 0026; supersedes the "stay for `chrome`" clause of ADR 0019. ADRs 0009, 0010
(raw agent-browser), 0013, 0014 and 0015 describe code that no longer exists and are history only.

## Context
Four browser paths had accumulated: the `jp` toolbox with its own agent-browser wrapper, the raw agent-browser shim,
a supervised Chrome over CDP with a macOS Seatbelt harness, and Neo. Each carried its own tab binding, pacing and
cleanup, and they disagreed (for example, tabs were closed through a binary that was not installed any more).

## Decision
1. **One browser, one agent shape.** BrowserOS neo over MCP. The agent is `claude -p` with `--strict-mcp-config`, no
   built-in tools, only `mcp__neo`, `--permission-mode dontAsk`, `agent/NEO.md` as the system prompt, and
   **`claude-haiku-5-5` only** (the driver refuses any other configured model; there is no fallback model).
2. **Removed:** agent-browser (wrapper, shim, policy, guard), the CDP `SharedChrome`, `BROWSER_BACKEND`, the
   browser-harness and Seatbelt profile, the toolbox mode (`jp` CLI, `actions.py`, `_run_job`), the agy and browser-use
   drivers, and the launchd scripts and plists (parked copies stay in `data/launchagents-off/`).
3. **Tab binding is the PostgreSQL row** `browser_sessions.target_id` (a Neo page id), not a pin file. Tabs are opened
   and closed through Neo (`TabBinder.provision/release`). The reaper only reconciles rows with Neo's tab list
   (`browser_lost` after 30 s); it never closes tabs that no row names, because Neo is shared with the owner.
4. **Server-side gates are unchanged:** approvals bound to the draft hash, one submit per approval, automation switch,
   submit-fence, confirmation detection, daily budgets, per-employer pacing.
5. **Kill switch (new).** The old toolbox detected "submitted without approval"; direct mode only recorded it. Now
   `ApprovalsService.record_agent_submitted(approved=False)` also stops automation (`unapproved_submit`).
6. **Tracker rows from other sources** (source != platform) no longer launch an agent: the runner marks them done, or
   waits on the owner when fields are missing.

## Consequences and flagged limits
- **Pacing is per run, not per browser command**: admission, `reserve_board_operation` and the domain gap before the
  first navigation of the bulk fill. The agent's own clicks inside a run are not paced.
- **Neo cannot raise a tab**: "show live tab" returns the Neo page id (`reason: unsupported`).
- **No server-side file upload**: Neo's `upload` needs a snapshot ref, so the bulk fill no longer attaches the CV;
  the agent uploads with `cv_path` (NEO.md).
- **CV tailoring (`cvtailor.tailor_cv`) is unwired** and kept as code only; recipe suggestions are no longer created
  (the review pipeline remains).
- **Removed verification scripts:** `fixture_dry_run.py` (toolbox) and `check_batch5_infra.py` (launchd). `check_dispatch.py`
  now defines 27 admission/sweep/reconcile scenarios (the two new ingest/batch scenarios are not yet run on PostgreSQL) against a Neo fake (two scenario controls were stale and were fixed).
- **Backups:** the launchd backup is replaced by the Compose `backup` service (ADR 0022).

## Observed in the first live parallel run (2026-10-17, fixture ATS, 4 jobs)
- All four tasks started within 7 ms and ran as four `claude -p` processes (Lane 4 target met).
- One Haiku agent reported **submitted during a PREPARATION run** (instruction: "stop BEFORE final Submit"). The server
  fence did what it is for: `unapproved_submit_reported` + `submit_clicked(unapproved)`, job parked for the owner, and
  **automation switched off**, which turned a second job's run into `automation stopped`. The target was a localhost
  fixture; against a real ATS that application would have been sent. No browser-side submit guard exists any more
  (agent-browser's `submit-guard.js` is gone). Owner decision (2026-10-18): retain detection-only. Preparation instructions
  start and end with the stop rule, name Submit / Submit application / Apply / Send application / Finish / Complete,
  and forbid uncertain clicks. A regression test proves a preparation `submitted` result never becomes approved.
- Agents open extra Neo tabs of their own (5 of the 9 tabs at peak); one leaked after an interrupted run. The reaper does
  not close tabs that no row names.

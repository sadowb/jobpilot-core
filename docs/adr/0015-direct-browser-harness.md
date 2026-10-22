# ADR 0015 — Direct Haiku browser operation under a macOS host sandbox

Status: accepted. Owner aim: 2026-10-12.

The owner supplies every job link personally. The existing `jp` and agent-browser path fails on ordinary
custom selects and typeaheads. Haiku needs full browser operation to complete applications, while Python
written by a model exposed to untrusted pages must be contained on the host.

Persist `agentMode: direct | toolbox` with runtime settings; default direct. Changes require stopped
automation and drained leases. Preserve the toolbox path, including read-only external tracker capture.
Each direct job task invokes the owner's authenticated `claude` CLI once, using `claude-haiku-5-5` and the
configured effort, optional budget/timeout and usage ledger. No API key or browser-use LLM agent is involved.
The owner decided on 2026-10-12 that filling a whole application is a long Celery task with no per-run cap by default. `browserBudgetUsd=0` and `browserRunTimeoutSeconds=0` mean unlimited: omit `--max-budget-usd` and the subprocess timeout. Non-zero limits retain prior behavior; existing stored overrides are honored. Celery job soft/hard limits are `None`; control tasks remain bounded. `browserMaxInteractiveActions` remains toolbox-only.

Direct process polling renews the owned 600 s lease every 15 s through a short independent autocommit heartbeat. Drain is checked every poll and Stop every second. A lost lease or failed DB check terminates Claude, as does Stop/drain (SIGTERM to its process group, SIGKILL after up to 5 s); there is no continuing heartbeat after process cleanup. Preparation returns `queued` with the started attempt charged. Submit-authorized interruptions become submission uncertain with the existing no-repeat fence.

`agentStallSeconds=900` is a dead-process watchdog, zero disables it. The installed `claude --help` supports streaming JSON and partial messages. `--output-format stream-json --verbose --include-partial-messages` lets stdout bytes indicate agent activity; DB beats and stderr do not reset the watchdog. Intermediate events are discarded and never logged/stored; only the final `type=result` envelope feeds usage classification and exactly its final `result` text feeds `parse_result`. Silence emits `agent_stalled` and stops the process with the same preparation/submit accounting. With unlimited defaults this is the only automatic inactivity stop; Stop/drain and lease loss also end runs, while daily caps stop new admissions. Normal errors/validation remain enforced.

Unlimited runs reserve the finite `cvBudgetUsd` admission allowance (default USD 0.25) and `runTokenReservation` under the global lock. Actual reported usage still settles in the ledger. Missing usage charges the reserved estimate as unknown; active calls can exceed the allowance and daily limits, which gate future admissions instead of killing an active application. See ADR 0012 for rollover and admission details. These fields, including the watchdog, require stopped automation and drained leases before changes.

The agent's only tool is one MCP tool, `mcp__harness__run`, served by `worker/harness_mcp.py` (see "Transport"
below). It has no shell and no built-in tool; other MCP/config sources, browser integration and session
persistence are disabled.

The server provisions/bookkeeps a job tab in shared Chrome and resolves the selected immutable CV object.
`agent/DIRECT.md` and an explicitly delimited JSON DATA block supply the job/link/target/CV path, profile,
confirmed facts, global and job answers. Secret-shaped keys and recognized credential values are refused
or excluded. Page content and all model output remain untrusted data, never instructions. The agent never
creates accounts or types passwords/codes; login, CAPTCHA, MFA and unclear consent go to the owner.
For option fields it must choose only an exact semantic match to the owner's value or ask.

`bin/jp-harness` fails closed without `/usr/bin/sandbox-exec` or `infra/harness.sb`. The deny-default profile
permits runtime/library reads, reads/writes below `BH_HOME`, and read-only access to exactly the selected CV.
Only the resolved Python interpreter can execute (the harness daemon is `python -m browser_harness.daemon`).
Fork/exec descendants inherit the policy, including detached daemon processes. No Security/Keychain mach
lookup is permitted. Home Keychains, SSH, Claude/config directories, repository `.env`, and other vault,
PostgreSQL and backup files have no allow rule. Python networking is restricted to the configured
`127.0.0.1:<CHROME_DEBUG_PORT>` and this run's Unix socket. Chrome itself performs web traffic.

Claude receives only OS/login essentials and job/harness variables, no DSN, Redis URL, proxy or secrets.
The launcher scrubs the environment again and gives Python HOME/TMPDIR/workspace inside `BH_HOME`.
`BU_NAME=jp-<jobId>`, `BU_CDP_URL` is loopback, and `BH_HOME=<data>/run/harness/<node>`. A fresh short
`BH_RUNTIME_DIR` under that directory per run prevents reuse of an unsandboxed or differently scoped
daemon. Unix socket paths >=104 bytes fail closed. Cleanup requests daemon shutdown through sandboxed
IPC without trusting writable PID files; the shared-Chrome reaper handles extra untracked tabs. Telemetry,
recordings and domain skills are disabled. Backend logging excludes prompts and raw results even in debug mode.

Pydantic validates one strict, extra-forbidden, size-bounded final JSON object. Invalid/missing JSON is a
failed step, retried by existing task attempt accounting, never success. `filled` writes `job_forms` with
agent-sourced values and a server-computed hash bound to the CV version. `check.verification=agent-reported`
and inventory provenance distinguish reported completion from deterministic checks. State-machine gates
explicitly admit this evidence for review without setting code-verification booleans. Drafts become
awaiting_approval and retain their tab. `needs_answers` stores missing question keys/options for Needs You;
saved answers resume work. `blocked` hands the reason to the owner.

Only `Services.approve_submit` writes approvals. A submit run requires an unconsumed approval matching the
current hash, auto board policy, board cap and a present parked tab. A lost tab invalidates the draft and
requires another review. Commit `submit_clicked` before model execution and consume approval after a valid
`submitted` report. Failures/timeouts/stop/drain/stalls/lease loss after authorization hand off as uncertain; no second click is scheduled.
Confirmation kinds `agent-reported` and `unapproved-reported` preserve evidence truthfully in applications.
An unapproved submitted report or recognized post-run confirmation emits `unapproved_submit_reported`,
records submitted-as-reported with a no-repeat fence through an explicit state-machine report exception,
and surfaces in Needs You. This exception never authorizes a click or creates an approval.

**Enforced:** Python host containment, server-side result validation, state transitions, approval creation,
repeat-click accounting, admission/concurrency/daily budgets/board caps and recorded-tab backpressure.
**Instruction plus detection:** final Submit inside the browser. Direct mode has no browser-level submit
block, per-action pacing hook, tab restriction or URL allowlist. It exposes all helpers/raw CDP/any tab.
Extra tabs can transiently exceed the recorded browser cap until reaped. The browser/CDP capabilities
(including cookies, arbitrary navigation, file uploads and downloads) are outside the Python filesystem/network
sandbox. This is not a browser data-loss prevention boundary; trusted Chrome and local tool binaries remain
assumptions. Secret filters cannot prove that arbitrary page/free-text prose contains no secret.

Live validation must verify harness startup/list_tabs and daemon inheritance under this profile; Python reads
of `~/.ssh`, repo `.env`, Keychains and unrelated vault files, external HTTP and non-Python exec must fail.
Real applications also require live validation. The MCP transport was verified live (see "Transport"); the
rest is unverified in the managed development sandbox. The dispatch check now includes 24 scenarios, including long-run lease recovery; the orchestrator runs it. CLI streaming flags were verified from installed help and process monitoring was tested with real local fake subprocesses; authenticated Claude streaming cadence/final envelope under the harness, live SIGTERM/Stop through Celery, and real application behavior still require live validation.
Direct mode uses the existing selected CV; an unprepared tailored CV requires owner preparation rather than
a second Claude run in the same task.

## CV track selection in direct mode

The sandbox profile needs exactly one CV path before the agent starts, but a freshly ingested link has no
`cv_track_id` (toolbox mode sets it as a side effect of `set_meta`, which in direct mode only runs after the
run). `DirectService.ensure_track` therefore runs once per preparation run, before `cv_path`: it ensures the job
tab, loads the job link into it over CDP (http/https only), waits at most 20 s for a complete, non-empty
document, and scores the page text against the active tracks' keywords with the same `suggest_track` /
`market_for` logic as `set_meta`. The result is written only if `cv_track_id` is still NULL (an owner choice or
a concurrent selection wins) and recorded as `cv_track_selected` (actor `system`, source `page-keywords`).
Page text is untrusted: capped at 200k characters, used only as input to keyword scoring, never persisted or
placed in a prompt. No usable track asks the owner (`wait you`, run `blocked`); an unloaded page requeues with a
30 s delay under the normal attempt accounting; Chrome offline backs off like any browser outage. Submit runs
never select a track, and archived tracks are excluded by the candidate query.

## Transport: one MCP tool instead of Bash + heredoc

The first transport gave the agent `Bash(<launcher> *)` with Python on a quoted heredoc. The Claude CLI's static
Bash permission matcher inspects the heredoc body and denies some valid calls (dict literals with apostrophes,
`json.dumps` with `$`/backticks inside strings); form-filling code always contains such shapes, so the
heuristic cannot be instructed around, and a denial surfaced as a misleading "blocked" result.

The agent now has no shell. `worker/harness_mcp.py` is a hand-written stdio JSON-RPC 2.0 server (standard
library only, started as `python -I`) exposing one tool, `run(python: str)`. It feeds the source to
`bin/jp-harness` on stdin, so the sandbox profile, `env -i` scrub, CDP-only network and the
no-unsandboxed-fallback rule are unchanged, and returns stdout+stderr capped at 60,000 characters with an explicit
truncation marker (`isError` on non-zero exit). There is no per-call wall-clock limit; the stall watchdog and
Stop/drain kill the Claude process group, which contains the server and the launcher.

Argv (direct mode only; toolbox argv is unchanged): `--tools ""` (no built-in tools), `--strict-mcp-config
--mcp-config <inline JSON naming only this server>`, `--allowedTools mcp__harness__run`, `--disallowedTools Bash
Read Edit Write WebFetch WebSearch`, `--setting-sources ""`, `--permission-mode dontAsk`, `--no-chrome`,
`--disable-slash-commands`, `--no-session-persistence`, stream-json flags. `--safe-mode` is dropped: it also
disables every MCP server, including an explicitly configured one (observed: "MCP tool not found"). The server's
environment is exactly the harness variables plus the launcher path, passed in the MCP config; no `.env`, database
URL or Claude settings. The live init event lists `tools: [mcp__harness__run]`, no skills, no slash commands, and
no CLAUDE.md/AGENTS.md/auto-memory content reaches the model under `--setting-sources ""`.

`run_direct` refuses any model other than `claude-haiku-5-5`. A final envelope with non-empty
`permission_denials` is never applied: the run fails through the invalid-result path with
`agent_run_failed {reason: permission_denied, count}` (count only; denied input is never stored) and is
requeued under the normal attempt limit. Live results: four calls with previously denied shapes ran with zero
denials; the sandbox probe denied `/etc/hosts`, project directory listing, `/bin/echo`, external HTTP, port 4100
and `/tmp` writes, and allowed writes under `BH_RUNTIME_DIR`; killing the Claude process group left no server or
launcher process (only the sandboxed harness daemon, which `run_direct` stops in its `finally` as before).

## Parked question tabs and resume runs

A `needs_answers` result used to call `wait(you)`, whose `_defer_close` queued `browser_closed`: the filled page was
thrown away on every question and the follow-up run refilled from scratch. The tab now follows the same exemption
class as an `awaiting_approval` tab (ADR 0011, amended by 0014):

- **Parked** means `waiting_on='you'` and the job's form has `missing` entries and a `reported: true` inventory
  (`PARKED_FOR_ANSWERS` in `repository/browser.py`). `BrowserService._defer_close` does not request a close, and
  both sweep queries (`sweep_browsers`, `close_browser` locked lookup) skip it.
- **Counted, not queued.** The session row still counts against `maxOpenBrowsers` (backpressure only), but the
  task is `blocked`, not `leased`: it holds no worker slot, and parking cannot starve admission.
- **Still reaped** by the existing rules: `close_requested`, `archived_at` (archive/purge), a status outside the
  pre-submit order (done/rejected/...), a job that is no longer `waiting_on='you'`, a Chrome restart, and the
  reaper's tab reconciliation. Nothing is evicted by the cap.
- **Resume DATA.** `DirectService.prepare` reads `browser_session_present` *before* `ensure_tab`. If a tab already
  existed and the form has reported fields, a preparation run gets `"resume": true` and `"previous_fields"`
  (label, kind, required, options, and the earlier value when one was reported; secret-looking labels dropped)
  alongside the answers already in `owner_data`, and the instruction says RESUME RUN. The agent inspects the parked
  tab, reads values back, fills only what is missing or wrong, and refills from scratch only if the page is gone.
  A fresh tab, or an empty form record, yields the unchanged first-run DATA. Submit runs never carry resume data:
  they act on the approved, unchanged draft.

Residual risk, unchanged in kind from approval tabs: the parked page is untrusted state that may have drifted
(session timeout, site-side reset). The agent is told to verify rather than trust `previous_fields`, and the
result still passes strict validation and draft-hash binding before any approval can exist.

## Submit evidence and the SUBMIT RUN procedure

Two real submit runs (j_ffoesexu, j_uiiynejx; job-boards.greenhouse.io) ended "submission uncertain" in 27 and 37 s
while the owner saw no code prompt and marked both submitted by hand. Their events show the agent returned a valid
result that was not `submitted` (the reason is the non-submitted branch of `apply`); nothing recorded what the page
showed, and the post-run confirmation detector did not run for authorized submit runs at all.

**Reproduction.** Throwaway schema and Chrome, local fixture ATS, the real runner and Haiku 5.5, prepare then approve
then submit. Baseline, `?widget=1` (custom select, typeahead, consent overlay), `?slow=8`, a Greenhouse-style SPA
that changes the URL after 2.5 s, a verification-code page and a validation-error page all behaved correctly, with
the old and with the new instruction. The fixture does not recreate the live Greenhouse failure, so its cause is not
established; what the runs did show:

- Haiku sometimes narrates before or fences its final object: seen twice in about 25 runs (a sentence before the
  object, and a fenced block after a sentence), plus one further `invalid_json` whose raw text was not captured. The
  strict parser discards such a finished result; in a submit run it becomes "uncertain".
- The old instruction was silent about waiting for the outcome, error text and code prompts; agents compensated
  inconsistently, and a run that returned before a slow confirmation could not be told apart from a failure.
- The server detector only ran on preparation runs.

**Changes.**

- *Server-side capture, independent of the model.* After every submit run (confirmed, blocked, invalid result,
  timeout, interruption, lost lease) and before anything can close the tab, `SubmitEvidenceService.capture` reads the
  job tab over CDP (`SharedChrome.capture`: URL, title, first 2,000 visible characters, PNG). It is read-only: no
  activation, navigation or input. The URL loses its query string and fragment (they carry tokens and one-time
  codes), text is one printable line with secret-shaped values replaced by `[withheld]`, and the screenshot is an
  owner-only file (0600 in a 0700 directory) under `data/evidence/<job>/`, newest five kept, never in git or the
  vault projection. `job_forms.submit_evidence` holds URL, title, excerpt, text SHA-256 and the file name; the
  `submit_evidence` event carries everything except the excerpt. Failure to capture is an event, never a changed
  outcome.
- *Observed confirmation.* When an authorized run does not report `submitted`, the server polls the tab (up to 30 s,
  every 3 s; one look when draining, stalled or timed out; none after a lost lease) for the board's confirmation
  patterns. Only text absent from the page before the run counts, so a posting that says "thank you for applying"
  cannot match. A hit is recorded as submitted with `submit_confirmation_observed`; no hit leaves the uncertain
  handoff, now with evidence. The common patterns already match Greenhouse's "Thank you for applying"; no
  site-specific pattern was added without evidence.
- *Display only.* `GET /jobs/{id}/submit-evidence/screenshot` serves the file named in that job's own stored record
  (a generated name validated against a fixed pattern and resolved under the job's directory); the request supplies
  no path. The page text is untrusted: the dashboard renders it as plain text, and nothing here feeds a prompt, a
  state transition or a log line.
- *Agent procedure.* `agent/DIRECT.md` gives the SUBMIT RUN as numbered steps: switch to the parked tab, compare only
  what matters (file inputs, reformatted phone numbers, option text versus value are not differences), click once,
  poll URL and text for up to ~60 s, then report `submitted` with a verbatim confirmation quote or `blocked` with the
  exact error text, field or code prompt shown. The server keeps up to 600 characters of that note in the wait
  reason.
- *One recovery in the result parser.* `parse_result` accepts exactly one top-level JSON object inside at most 2,000
  characters of prose or a code fence; zero or several objects, duplicate keys and every validation rule are
  unchanged. This reverses "no fenced recovery" deliberately: the recovered object is validated like a clean one and
  the model gains no authority, but strictness is a defensible choice and the change is one function.

Residual risk: a page that accepts the click but shows nothing the agent, the patterns or the capture can classify
still ends as "uncertain"; the owner then decides from the URL, text and screenshot instead of from nothing.
Verification-code prompts are surfaced, never solved.

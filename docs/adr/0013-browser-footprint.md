# 0013 — Browser footprint: shared Chrome and saved state

Status: superseded by [0014](0014-shared-chrome-tabs.md) (recommendation reversed by the owner; the measurements stand)

## Context

ADR 0012 gives each job its own `agent-browser` session (`jp-<jobId>`): one Rust daemon plus one Chrome process tree. ADR 0011 keeps that browser open while a filled form waits for approval, so parked browsers hold memory without holding an agent slot. `maxOpenBrowsers` (default 6) bounds the count but not the cost. This spike asks (1) whether isolated per-job contexts can live in one shared Chrome, (2) whether filled-form state can be saved and restored so a parked browser could be closed, (3) what a session actually costs, and (4) whether per-job isolation and policy survive sharing. Read-only: no repo file other than this ADR changed; nothing was submitted.

Environment: agent-browser 0.38.2 (Rust CLI, no source shipped; `strings` shows it can call `Target.createBrowserContext`), Chrome 154.0.8037.98 headless, macOS arm64, fixture ATS `backend/fixtures/form_server.py`, loopback only. Port 4777 was already held by a fixture process I did not start (pid 15374, left running); I used `FIXTURE_PORT=4797`. Spike sessions were named `spike-*`, never `jp-*`.

## Findings

### 1. Memory per session

Method: `ps -axo pid=,ppid=,rss=` summed over the session's daemon plus every descendant (`scratchpad/tree_rss.py`), and `footprint -p <pids>` (macOS physical footprint, the Activity Monitor figure). Raw RSS double-counts pages Chrome shares between its helpers and drifted 10-20% while pages settled (first read 870 MB, 20 s later 671 MB), so footprint is the figure to size on. The fixture is a tiny static page: real ATS pages cost more in every mode; the fixed per-Chrome cost is what sharing removes.

| Mode (page: fixture form) | N | RSS MB | Footprint MB | Procs |
|---|---|---|---|---|
| Owned Chrome per session (today) `--session spike-N open` | 1 | 671 | 380 | 11 |
| | 2 | 1587 | 842 | 23 |
| | 3 | 2518 (2371 after 20 s) | 1288 (1182 after 20 s) | 36 |
| Shared Chrome, no sessions | 0 | 622 | 275 | 9 |
| Shared Chrome, one tab per session, same default context (`--cdp 9333 --pin-tab open`) | 1 / 2 / 3 | 765 / 838 / 914 | 312 / 339 / 375 | 10 / 12 / 14 |
| Shared Chrome, one `window new` context per session | 0 | 709 | 299 | 7 |
| | 1 / 2 / 3 | 700 / 839 / 1109 | 330 / 454 / 552 (507 after 20 s) | 8 / 13 / 15 |

- Owned: about 395 MB footprint and 790 MB RSS per session (+462 MB then +446 MB footprint for sessions 2 and 3, daemon 6-7 MB of that). Each tree is 11-13 processes: daemon, Chrome, GPU, network, storage, utility and about six renderers for a single page.
- Shared Chrome costs 275-300 MB once. Each additional tab adds about 35 MB footprint; each additional context about 85 MB. At N=3 that is 375 MB (tabs) or 507-552 MB (contexts) against 1182-1288 MB owned, a 55-70% reduction; the gap grows linearly with N (N=6 parked: about 2.4 GB owned, about 0.8 GB with contexts, extrapolated, not measured).
- A first context run left four stray `about:blank` tabs (`window new` plus attach) and read 416 / 577 / 710 MB; closing them (`/json/close/<id>`) gave 521 MB. Stray tabs are a leak source.

### 2. Can one Chrome host isolated per-job contexts?

Partly. `--cdp <port>` attaches many sessions to one Chrome (`--pin-tab` binds each to its own tab). Plain tabs share the default browser context. `window new` is the only context mechanism the CLI exposes: it creates a new browser context, but you must call it per session and the CLI cannot dispose it.

| Check (two or three sessions on one Chrome) | Result |
|---|---|
| localStorage and cookie, tab mode | LEAKS. Set in session 1, read by session 2 on the same origin. `sessionStorage` did not leak (per tab). |
| localStorage and cookie, context mode | Isolated. w1/w2/w3 each read back only their own value. |
| `tab list` / `tab t<N>` | LEAKS in both modes. Lists every tab in the browser; `tab t5` from w1 then read w2's cookie and localStorage. `--pin-tab` did not stop an explicit switch. |
| `tab new <url>` in a context session | Escapes the context: the new tab saw the default context's localStorage. |
| `--init-script submit-guard.js` | Per session, not global: guard true on the session that passed it, false on the others; also applied to that session's `tab new` tabs. Changing it restarts the daemon and renumbers tab ids. |
| `--action-policy` | Per daemon: `cookies` denied (`cookies_get` not in allow list) on the session with the policy, allowed on the one without; `state save` denied. |
| `--allowed-domains` | Refused with `--cdp` ("WebRTC containment cannot be installed..."), also via env var, and refused with `--restore` / `--state`. Works in owned mode: 127.0.0.1 allowed, `localhost` blocked. JobPilot does not pass it today (ADR 0010), so this is no regression, but it forecloses it. |
| `close` in CDP mode | Detaches only; the tab and its context stayed open in Chrome (3 pages before and after). Something other than the CLI must close targets. |
| Chrome dies | Every attached session fails with "CDP connection failed"; no silent fallback to an owned Chrome. All parked forms are lost together. |
| Reusing a name that had run owned | `spike-1` then failed `tab_gone` against a stale `~/.agent-browser/<name>.target` pin; fresh names worked. Not diagnosed further. |

Owned mode isolation (own temp `--user-data-dir` per session) was observed from the launch flags and the tab list, not re-tested for leaks.

### 3. Saved state: what comes back

`state save <path>` (or `--restore`, auto-saved on close and every 30 s) writes only `cookies` and, per origin, `localStorage` and `sessionStorage`. I filled the fixture (`?widget=1`: text, email, radio, custom combobox, file input) plus an in-memory JS variable, saved, closed, and reopened with `--state` and with `--restore`:

| Restored | Not restored |
|---|---|
| cookies (`sid`), localStorage, sessionStorage | text and email values, radio state, custom combobox text and hidden value (shows "Select..."), file input (0 files), in-memory SPA state, scroll, wizard step reached by clicks |

- Files are plaintext JSON, mode 0644 (`ls -l`), in `~/.agent-browser/sessions/` for `--restore`. `AGENT_BROWSER_ENCRYPTION_KEY` (AES-256-GCM) is documented but I did not test it. Cookies on disk need the same handling as SECURITY.md rule 4 (secrets never reach DB, vault, git, logs) and a delete step; saved auth also stops `--allowed-domains` working.
- `agent/browser-policy.json` has no `state_save` / `state_load`, and `Browser._base` pins that same policy for orchestrator calls, so `state save` from code is denied today. The shim already forbids the `state` verb and `--state` / `--restore` flags for the agent, so adding both to the policy would not widen agent power.
- Idle shutdown: the daemon closes browser and exits after 1 h without commands (`--idle-timeout`, default `1h`). Confirmed with `--idle-timeout 10s`: daemon and Chrome were gone after 16 s. The repo sets no idle timeout, so a parked approval browser that nobody pokes for an hour is already lost, and a `browser_sessions` row may then say open. Not checked: whether `sweep_browsers` reconciles that. README-only, not measured: Chrome Memory Saver can discard background tabs and a reload resets unsaved state.

### 4. How this fits `Actions.submit`

`submit` (`actions.py:590`) already treats a closed browser as recoverable: `gate_submit`, then if not `browser_is_open` it calls `fill()` (`_walk` with `replay=True`: invalidate form, reopen `apply_url`, `_fill_page` per page, `_click_next` between pages), then `verify()` and compares `draftHash` to the approved hash; drift moves the job to `preparing` and `wait(you)`. Field values therefore come from the PostgreSQL plan and are re-proven, not trusted from the browser. Saved cookies/localStorage would add only session continuity (logged-in ATS, anti-CSRF or draft tokens). They cannot replace the refill, and nothing in the state file can be trusted for the hash.

## Options

- **A. Status quo plus sizing.** One Chrome per job; size `maxOpenBrowsers` from measured footprint (about 0.4 GB per session, 6 browsers about 2.4 GB). No new control plane, no isolation change.
- **B. Shared Chrome, tab per job.** Cheapest (about 35 MB) but cookies and localStorage are shared. Rejected: violates per-job isolation and ADR 0010's trust model.
- **C. Shared Chrome, context per job (`window new`).** About 85 MB per job, storage isolated, per-session init-script and policy hold. Needs code JobPilot does not have: a supervised Chrome (loopback debug port, fresh `--user-data-dir` per boot because the default context persisted localStorage across restarts), raw CDP (`Target.disposeBrowserContext`, `closeTarget`) because `close` leaks, shim denial of `tab` and `window` (cross-job read and context escape), and acceptance of one crash taking every parked form and in-flight run.
- **D. Park = save state, close, refill on approve.** Zero memory while parked; builds on the existing guarded path. Costs a refill per approval (pacing, action cap, wall time), cannot restore custom widgets or uploads from state, and a failed restore sends the job to human review, the outcome ADR 0011 set out to avoid.

## Recommendation

Do not adopt a shared Chrome now; take A, and treat D as a bounded, opt-in escape hatch.

1. Keep ADR 0011/0012 retention and backpressure; document the measured 0.4 GB footprint per session in the `maxOpenBrowsers` setting description and the dashboard capacity text.
2. Decide explicitly how parked browsers meet the 1 h idle shutdown: either pass a longer `--idle-timeout` from `Browser._base` for approval sessions, or reconcile `browser_sessions` against `session list` and let `submit` take its existing refill path. Today it is undefined.
3. Only if memory proves binding, add D as a TTL eviction of the oldest parked browser (close, mark `evicted`, refill on approve through `Actions.submit` unchanged), with the approval card saying the form will be rebuilt. Do not persist cookies unless a site needs it; if so, encrypted, 0600, deleted on submit or archive.
4. Reopen C only when parked browsers routinely exceed about 10, as its own ADR with the supervisor, CDP cleanup and shim changes above.

## Consequences / open questions

- No behaviour changes from this ADR. A and D require small code; C is a new subsystem.
- Not verified: real ATS pages (Workday, Greenhouse) under either mode; Chrome stability with 6+ contexts over hours; `AGENT_BROWSER_ENCRYPTION_KEY`; `Target.disposeBrowserContext` behaviour; whether `browser_is_open` ever reconciles after a daemon idle exit; Linux hosts for the multi-node plan (macOS `footprint` figures do not transfer, RSS is the portable metric).
- The 4777 fixture belongs to another process; one read-only GET was made against it (idle-timeout test). Spike leftovers: `~/.agent-browser/spike-*.{config,target}` and `~/.agent-browser/sessions/spike-s3-spike-s3.json` (plaintext fake cookie); I did not delete them. All spike daemons and Chromes were closed and verified with `session list` and `ps`.

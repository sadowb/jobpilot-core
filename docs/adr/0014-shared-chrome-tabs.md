# 0014 — One shared Chrome per node, one tab per job

Status: accepted (owner directive 2026-10-11: "do multiple tabs and each one takes a tab, not multiple browsers")

Supersedes the recommendation of [0013](0013-browser-footprint.md) (one Chrome per job). Amends [0011](0011-approval-browser-retention.md): a parked form is a parked **tab**.

## Context

ADR 0013 measured one Chrome tree per job at about 395 MB (fixture page). Real ATS pages are heavier: measured read-only loads of the owner's five real links in one headless Chrome (single run, macOS footprint, ±150 MB) cost about 1.25 GB for the first tab plus about 210 MB per extra bare tab, or about 420 MB per extra tab when each tab has its own browser context; one Chrome per job would be 6-8 GB for five jobs, which this 8 GB Mac cannot carry. The fixture numbers in 0013 understate real pages by about 5x and the isolation premium of a context is about 200 MB per job, not 50.

## Decision

Each node runs **one supervised Chrome** (`bin/jp-chrome`, launchd `com.jobpilot.chrome`, loopback debug port `CHROME_DEBUG_PORT`, default 9333, fresh profile wiped on every start). Every job's `agent-browser` session `jp-<jobId>` is bound to **one tab** of it. `maxOpenBrowsers` keeps its name and now counts tabs.

### Binding (`browser/tabs.py`)

agent-browser 0.38.2 attaches with `--cdp <port> --pin-tab`. JobPilot creates the tab itself over CDP (`SharedChrome.open_tab`, `about:blank`) and writes the tool's per-session pin file (`~/.agent-browser/jp-<job>.target`) before the session's first command, so the daemon attaches to exactly that tab. Spike results (B0):

- A tab created through the tool, or adopted with `tab <id>` after attach, did not reliably get `--init-script` (the submit guard) installed; a tab bound through the pin file before attach does, in both default and isolated contexts. So the pin-file route is the only one used.
- The row (`browser_sessions.target_id`, `context_id`, migration 0017) is written **before** the tool's first command, so the reaper's keep-set contains the tab from the moment it exists.
- After every open `window.__jpSubmitGuardInstalled === true` is asserted; otherwise the tab is closed, the row dropped and the open reported failed (fail closed). This also catches a future agent-browser changing the pin-file format.
- `close` only detaches (the daemon exits, the tab stays); JobPilot then closes the tab (and disposes its context) and removes the pin. A stale pin to a closed tab makes every command fail with `tab_gone`; `Actions.open` drops the row and mints one new tab.
- The daemon idling out (`--idle-timeout`) no longer ends anything: the tab and its form live in Chrome and the next command re-attaches through the pin. No retention or idle setting is needed. A parked form lives until Chrome restarts, the owner acts, or the job is archived; nothing is evicted by the cap (the cap is backpressure only). After a Chrome restart guarded refill and the draft-hash check of `Actions.submit` still apply; refill revokes the approval and requires fresh owner approval (ADR 0011).

### Reuse and replay (2026-10-12)

`Actions.open` checks session liveness while holding the browser lock and reuses a
live tab without navigation or resetting the form cursor. Internal wizard replay
explicitly passes `replay=True` and invalidates the draft before navigating to the
recorded URL. Opening a replacement tab also invalidates the stored draft. Both
follow ADR 0011's approval revocation and preparation/verification lifecycle;
the live draft-hash check and submit-click fence remain unchanged.

### Isolation: `browser_tab_isolation`

- `shared` (default): tabs in the default browser context. Same-site tabs share a renderer process, cookies and origin storage, **exactly like a person with several tabs open**. Cross-origin isolation is the browser's same-origin policy. Footprint ~210 MB per extra tab on real ATS pages.
- `context`: one isolated browser context per tab (`disposeOnDetach: false`; disposed on close). Two jobs on the same ATS origin cannot overwrite each other's cookies or localStorage. About 200 MB more per job.

Trade-off, stated plainly: in `shared` mode two jobs on the same origin share cookies and localStorage. Before the application form is submitted no JobPilot rule depends on origin storage, and an ATS keeps the draft form in the page, but a board that stores a per-application draft or an account session in origin storage can cross-contaminate two applications on the same tenant. If a board is found to do so, pin that board to `context` or cap it to one open tab per origin (not implemented; this ADR records the lever). B4 verifies two forms of the same Greenhouse origin side by side.

### Agent boundary

The debug port is **unauthenticated loopback**: whoever connects controls every tab. The agent has no path to it:

- Its Bash allowlist is `jp *` and the browser shim only (`worker/claude.py`). The shim refuses `--cdp`, `--pin-tab`, `--auto-connect`, `connect`, `eval`, `cookies`, `storage`, `state`, `network`, `dashboard`, and now **`tab` and `window`** (`tab list`/`tab <id>` read and drive every job's tab, `tab new`/`window new` leave the pinned tab or its context; ADR 0013 §2). `agent/browser-policy.json` no longer allows `tab_new`, `tab_list`, `tab_switch`; nothing in code paths uses them.
- Navigating the worker's tab to `http://127.0.0.1:<debug port>/json/...` would list every tab's URL and title (read only); the shim refuses a loopback URL on the configured debug port. A page-supplied redirect or link to it can still render the listing into the tab (page content is untrusted data, and the listing carries no credentials, only URLs and titles); executing anything needs a WebSocket, which Chrome rejects when the request carries an `Origin` header (page scripts always do) and the agent has no client for.
- The profile is wiped on every Chrome start, so cookies and storage live no longer than the process; `cookies`/`state` stay denied by the action policy.
- Chrome flags keep tabs alive for headed use (`--disable-background-timer-throttling`, `--disable-renderer-backgrounding`, `--disable-backgrounding-occluded-windows`, `MemorySaver`/`TabDiscarding` off). Worker and agent code never calls `Target.activateTarget` or brings a tab to the front: that would steal focus from the owner while they watch or type.

### Health, backpressure, reconciliation

- The control worker's heartbeat publishes `{node}:chrome` (`offline` when `SharedChrome.alive()` fails). Dispatch admits nothing while job workers are up and no such node has a reachable Chrome; `start_run` and `jp next` hand a job back (`start_deferred`, attempt not charged) instead of failing it. The dashboard shows `idleReason` "shared browser offline" and a Browser chip (`browserOnline`).
- The reaper judges tab-backed rows against Chrome's own tab list: a tab missing for `TAB_LOST_GRACE_SECONDS` (30 s) drops its row with `browser_lost` (Chrome crash or restart: one event per session of the node). An unowned page is closed only after staying unowned for `TAB_ORPHAN_GRACE_SECONDS` (120 s, longer than the 90 s tool timeout), so a tab created moments ago cannot be reaped before its row commits. Non-page targets (extensions, browser UI) and the launcher's `about:blank` keeper tab are never touched. Tab-backed rows and orphan tabs are not judged while Chrome is unreachable.
- Legacy rows (`node IS NULL`, or this node's rows with `target_id IS NULL`) cannot represent a tab in this mode. Reconciliation and cleanup drop them immediately, even on the first pass, in the same transaction as a `browser_lost` event with reason `legacy session`; they occupy no tab capacity and confer no session-reuse exemption. Rows on another named node belong to that node: this node never probes, judges or deletes them, and keeps counting/routing/holding them under the existing liveness and takeover rules. Before use, local sessions are also checked against `SharedChrome.tabs()`: a missing tab is dropped immediately, so submit takes guarded refill. A tab missing before or during post-run inspection emits `post_run_check_skipped` with only `{"reason":"tab gone"}`. Without a submit click, guarded refill remains recoverable; after `submit_clicked`, loss is an uncertain submission and waits for the owner without another submit. Session-loss retries keep their charged attempt, so the five-attempt limit ends repeated paid retries; an offline-browser run is refunded only if no model operation started in that run. A present but uninspectable tab still fails closed. Close or drain per-job browsers before switching a node to tabs: a daemon started with its own Chrome is relaunched when it first sees `--cdp`.

## Consequences

- One Chrome tree per node: footprint is the Chrome base plus per-tab cost, bounded by `maxOpenBrowsers`; B4 re-measures with filled forms, three running and three parked.
- A Chrome crash loses every parked form of the node at once (0013 §2 already noted this for sharing). Drafts recover through guarded refill and fresh owner approval; nothing is submitted without the draft-hash match.
- Popups: with `tab`/`window` denied the agent cannot switch to a page's `target=_blank` popup; apply flows that depend on one end as `waiting on you`. Acceptable for the supported boards (Greenhouse, Lever, Ashby embed in-page).
- Safari/iOS and `--allowed-domains` remain out of reach (0013: refused with `--cdp`); JobPilot does not use `--allowed-domains` (ADR 0010).

## Open items

- The dashboard's review action could focus the job's parked tab in the shared Chrome (owner-initiated only) instead of starting `review.py`'s separate Chrome. `review.py` is unchanged in this phase.
- Per-origin tab cap or per-board `context` pin, if a board is found to keep per-application state in origin storage.
- `bin/jp doctor` self-test of the pin-file route against the installed agent-browser version (the open-time guard assertion is the runtime safeguard).

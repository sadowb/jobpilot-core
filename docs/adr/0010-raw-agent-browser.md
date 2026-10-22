# ADR 0010: raw `agent-browser` for the worker; Haiku only

Date: 2026-10-10. Status: accepted by the owner. Supersedes ADR 0009 (browser hands).

## Context
ADR 0009 re-implemented `agent-browser` inside `jp` as ~25 fixed verbs restricted to the job's host, plus a
Haiku -> retry -> Sonnet escalation ladder. The owner wants the real CLI with its native syntax on any site,
and Haiku only. The design principle is unchanged: the agent is free in HOW it drives the page; code alone
judges the RESULT (deterministic verify -> draft hash -> UI approval -> gated submit -> confirmation).

## Decision
- `bin/agent-browser` shim (python module `jobpilot.browser.shim`) is first on the worker PATH and is the only
  `agent-browser` the worker may run (`--allowedTools Bash(<home>/bin/jp *)`, `Bash(<home>/bin/agent-browser *)`).
- Pinned flags the agent cannot change: `--session jp-<job>`, `--action-policy agent/browser-policy.json`,
  `--init-script agent/submit-guard.js`, `--content-boundaries`, `--max-output 20000`. No `--allowed-domains`.
- Policy schema (probed on agent-browser 0.38.2): `{"default":"allow|deny","allow":[...],"deny":[...]}` over
  internal action names (`evaluate`, `cookies_get`, `storage_get`, `state_save`, `requests`, `route`,
  `download`, `upload`, `navigate`, `auth_list`, ...). We use `default: deny` with an explicit allow-list.
- Bookkeeping lives in `jp internal browser-event|browser-cv|browser-blocked` (hidden, shim-parent-checked):
  lease/automation check, pacing, per-job action cap, `browser_cmd` event, invalidation of the verified draft
  on mutating verbs, CV-path check for `upload`.
- `jp browser bind` stores the real apply URL after redirects (migration 0010 `jobs.apply_url`) and re-runs
  dedup; `jp browser adopt` (also done by `jp verify`) records live non-secret values as `source=agent`.
- Haiku only: one run + one same-model retry, then `wait(you)`. Removed: Sonnet escalation, goal-run mode,
  their settings and UI.
- Post-run detection: confirmation pattern present without a `submit_clicked` event => job blocked
  "SUBMITTED WITHOUT APPROVAL", automation disabled, shown red.

## Consequences
- Fewer moving parts and native agent-browser capability for odd UIs, at the cost of two accepted residual
  risks (SECURITY.md): page-level submit guard, and a filled password readable from the DOM.
- The 25-verb wrapper and its replay machinery are removed from `main`; recoverable at git tag `pre-realign`.
  Migration 0008 columns stay (unused).
- Scale: one Chrome per job; roughly 8-16 parallel on a Mac.

## Planned dropdown assertions (2026-10-12)

A planned custom choice is preserved during live-value adoption. The agent cannot
replace that assertion with a different displayed option and obtain a verified
draft. Custom-choice fill/readback uses `choose_option` with exact normalized
matching (plus existing country normalization/dial-code equivalence), with no
word-overlap or prefix matching. School and city planning also refuses approximate
option mappings. Search text still present in an autocomplete input is pending,
not a committed selection, even if the menu has closed.

When no initial option matches, code types the planned value into an editable
combobox and waits at most three seconds for stable suggestions. Only an exact
match can be picked. A miss clears the search/selection and verifies emptiness,
stores bounded suggestions and the rejected answer in the existing inventory
JSON, adds the field to missing, revokes verification/approval and hands off to
the owner. Any initially offered Other/not-listed option is retained. A failed
clear is a failed fill requiring review. Verification also explicitly refuses any
remaining missing field, including an optional choice whose planned value failed.

Needs You shows the rejected answer and form suggestions. The owner correction
is forced to job scope in the service, even if a client requests global scope;
it never changes the owner's university/profile. Replanning and re-inventorying
preserve the rejected-value evidence until a different owner answer applies.
Runtime instructions forbid accepting a different suggestion by hand.

The raw shim's metadata has refs and verbs, but no resolved combobox identity or
focused-field mapping for `press`/`find`/nested option clicks. A precise pre-click
refusal would need additional DOM resolution and browser calls, so this change
uses the existing adoption and verification hooks. It does not claim to prevent
all raw page edits; those edits cannot satisfy the original planned assertion.
Approval remains server-written and bound to the verified draft hash. Repository
SQL adds only an update of existing inventory/missing JSON; no DDL or migration.

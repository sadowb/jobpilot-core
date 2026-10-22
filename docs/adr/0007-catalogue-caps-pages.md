# ADR 0007 — scoped catalogue, board admission caps and bounded form pages

Date: 2026-10-10. Status: accepted.

The fixed toolbox must expose useful checks without giving the browser model
answers, navigation selectors or authority to approve/complete unfinished work.
Thousands of parallel jobs also need admission limits before submission.

## Decision

- `status`, `cv check`, `cv use`, `answer match`, `done` and `browser next-page`
  use the existing agent-role allowlist and mandatory `JP_JOB` scope. Status is
  one JSON line. Answer matching returns only field keys and saved answer IDs,
  using the same scope/option precedence as planning. Plans remain code-built.
- `cv check` examines the job's actual TeX/PDF, checks their content address
  against its registered version, runs existing Overleaf lint and `pdfinfo`, and
  requires one page and confirmed fact-ID citations on content lines inside
  TAILOR regions. Missing regions fail rather than passing vacuously. It does not
  establish that a cited claim semantically follows from a fact. These checks
  also feed draft verification. `cv use` requires base mode, a registered track
  whose default mode is base, a non-manual board and a pre-ready job. CV changes
  invalidate fill/verification; the switch records an event and uses projection
  CV links/queues. No tool creates an approval.
- `done` requires the pure next-step gate to be `idle` and an unexpired lease
  owned by the calling worker. `release … done` shares the gate. Human handoff,
  pending approval and uncertain confirmation are not completion.
- Migration `0006_catalogue_caps_pages.sql` adds nonnegative per-board caps and
  UTC-day counters. Defaults: 200 preparation admissions, 10,000 browser calls,
  20 account creations. Each claim reserves one preparation admission, including
  retries. Admission conservatively stops the board when any of these caps is
  exhausted, even if a particular new job might not need another account.
  Browser calls and new account creation additionally reserve their own counters
  atomically before execution on independent connections. Unknown/failed work
  keeps its charge. Existing account reuse does not consume account creation.
  Zero disables new admission; counters reset by selecting the new UTC day.
  Current jobs can finish preparation already admitted, subject to actual browser
  and account limits. The approved final click and confirmation-only claims/readbacks after a submit
  fence bypass these board caps, preserving completion without another click. Cost/token budgets
  retain their existing rules. Board-cap exhaustion is exposed in `idleReason`
  when no uncapped candidate or active lease exists. Board updates require stopped
  automation and drained leases; GET/POST `/api/boards` expose additive cap fields.
- Form fields carry zero-based `pageIndex` and a page namespace in their keys.
  The DB stores the cursor and whether a final page was found. `next-page` checks
  the filled current page, rechecks identity/inventory atomically with navigation,
  clicks only a unique visible enabled button explicitly typed `button` with an
  exact Next/Continue name, waits for changed stable fields and appends the page.
  Duplicate pages and more than 20 pages are refused. Decorated, ambiguous,
  disabled, submit-type or unmapped navigation requires human review.
- Fill and verification replay all known pages from the recorded job URL. Every
  page's mapping, required fields, planned-value readback, validation and CV
  attachment are checked; every non-secret field's observed value (including
  optional defaults) participates in the hash alongside all inventories, plan
  values and the CV content address. Credentials are excluded. Replay must match
  each saved page exactly and end at a unique visible enabled submit control.
  Unsupported widgets, empty review pages or resume-only wizards require handoff.
  Workday is forced manual in adapter, Services and existing board rows, since
  its account-dependent wizard cannot be safely rebuilt here.
- Normal fill, page navigation, verification and confirmation run in scheduler
  code without a model call. Model help remains for capture, CV tailoring and
  failed planned fills. Browser operations heartbeat only the owned worker lease
  during long replay; quota denial requeues without charging a retry.

## Lifecycle corrections (2026-10-12)

Account creation serializes lookup, quota reservation, credential creation and
account insertion with the existing session advisory-lock helper, namespace 4,
keyed by the JSON pair `(origin, username)`. Lookup is repeated inside this lock;
concurrent jobs reuse one credential and charge one account-creation unit. An
insertion failure removes the newly created credential and returns a fixed error
without exposing database or credential diagnostics. Failed admitted work still
keeps its quota charge. Account queries remain in the profile repository.

Selecting the already-linked base CV is a no-op. A real CV change revokes any
unconsumed submit approval, returns ready/approval-waiting jobs to preparation via
the state machine and resumes them. Plan and normalized answer no-ops preserve
verification; changed plans/answers require verification and new approval (ADR
0011). Wizard Next invalidates before clicking, including failed navigation;
explicit replay follows the same lifecycle. Each agent edit increments the draft
revision even if verification was already invalid.

## Consequences

Earlier approvals/hashes cannot authorize the new draft representation. Existing
forms must be inventoried and verified again; the new completion flag defaults
false. Base CVs without cited TAILOR regions will fail the new check and need
user-confirmed annotation/registration. No personal files were inspected or
rewritten for this change. Page replay intentionally trades browser calls for
deterministic evidence and can hand off forms that cannot restart at page zero.

Reveal-on-demand accounts remain skipped: OS authentication requires a macOS
LocalAuthentication helper, unavailable without a custom native tool. Existing
secret-reference injection is unchanged; no unauthenticated reveal is added.

Apply migration 0006 before restarting server/workers. SQL concurrency, actual
ATS replay and PDF-tool execution require orchestrator validation outside the
managed sandbox. No unit-test suites or live submissions accompany this change.

## Custom choices and profile dates (2026-10-12)

Inventory probes each custom combobox in one bounded browser call using the
existing menu opener, captures at most 60 option labels of at most 120 characters,
then dismisses without selecting and compares readback. Overlong labels are
omitted rather than truncated into a different selectable answer. An editable
control with no initial options is marked searchable; a failed capture remains
answerable as text. Actual value drift or an unclosed menu requires handoff.
Choice labels and suggestions remain untrusted text, rendered inertly in Needs
You. Static choices render a select; choices without options render text with an
explanation of matching and possible handoff. DTO additions and generated API
contracts carry `searchable` and `unmatchedValue`.

Captured custom options/searchability and failure suggestions are enrichment,
not the structural page signature: replay still checks selectors, kinds, labels
and required flags without reopening every dropdown. The full inventory remains
in the draft hash. Filling always validates against the live menu and verification
checks the committed shown value. No observed suggestion can authorize a submit.

Education start/end years and the end month are derived in the pure domain from
the existing structured education section, including profiles saved before this
change. The month is its full name (2026-12 → December), then matched against the
site's captured options. Discipline maps to major. Location (City) uses the
profile's city, state and country when all exist; otherwise it uses the known city
without inventing missing parts. Missing or year-only dates never invent months.
No schema migration is needed.

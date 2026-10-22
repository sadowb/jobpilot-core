# Workday automation + credential vault plan (rev 2, 2026-10-19) — superseded: A is ADR 0031, B1-B3 are ADR 0032; B0/B4 (live account + run) need the owner

Two workstreams. **A (credential vault + server-side verification codes)** is needed for any board that wants an
account, so it comes first and also fixes the unreliable "click Save on the password prompt" step. **B (Workday)** then
uses it. Workday stays **off by default**, review mode only, parked at `awaiting_approval`.

## 0. Boundaries (who may do what)

- I can build and test everything, including account creation, sign-in and verification codes on the **local fixture ATS**
  (`backend/fixtures/form_server.py`, localhost). Test credentials on a local dev host are fine.
- I will **not** create a real Workday account or type/enter passwords there myself. Real accounts are created by the
  product (the dashboard's Start button, i.e. your action) or by you by hand. The recon of Workday steps 2-5 therefore
  reads pages through a Neo session that is already signed in, and never enters credentials.
- **This plan reverses part of AGENTS.md hard rule 4 ("never store passwords") and the core property of ADR 0028.**
  It needs your explicit yes (section 6) and a new ADR 0031.

## 1. Workstream A — credential vault

### 1.1 Why not hashing
A hash cannot be replayed into a login form. The server must get the password back, so it uses **authenticated
encryption (AES-256-GCM)**, not hashing. (Hashing would only fit a password *we* verify, e.g. a dashboard login; there is none.)

### 1.2 Storage
- New table `site_credentials` (migration `0033`): `id`, `origin`, `username`, `ciphertext`, `nonce`, `key_id`,
  `state` (`pending|active|failed|retired`), `source` (`generated|imported`), `created_at`, `last_used_at`,
  `last_verified_at`, `UNIQUE(origin, username)`. The old unused `accounts` / `account_approvals` tables are left alone.
- AES-GCM with a random 96-bit nonce per row; **AAD = `origin|username|id|key_id`** so a ciphertext cannot be moved to
  another row. New dependency: `cryptography` (not in `requirements.lock` today; pin + pip-audit in CI).
- **Key custody:** a 32-byte random key in `data/.vault-key`, mode 0600, created with `O_EXCL|O_NOFOLLOW` exactly like
  `.session-secret` (`middleware/security.py:_session_secret`). It is **not** in Postgres, git, `.env`, or the DB dump.
  `key_id` supports rotation. Losing the key loses stored passwords; recovery is "reset password by email".
  Alternatives rejected: macOS Keychain (not reachable from Docker containers), a startup passphrase (unattended workers
  could not decrypt).
- Backup: `docker/backup.sh` must keep excluding `.vault-key` (to verify), and a doc line tells you to back the key up
  separately from the DB dumps.

### 1.3 Access rules
- Decryption exists in one place, `CredentialVault.use()`, called only by the server-side sign-in injector. No API ever
  returns plaintext to the model, logs, events or job results (existing `password_key_refused` guard stays).
- **For you:** dashboard page "Accounts" lists origin, username, state, last used. **Reveal** and **Export** are
  user-session only, rate limited, `Cache-Control: no-store`, write a `credential_revealed` event without the value, and
  the reveal auto-hides after 30 s. You can always see/copy a password; the agent never can.

### 1.4 Google Password Manager compatibility
No public API exists for Google Password Manager, so it is file-based both ways:
- **Import:** upload the CSV exported from Google Password Manager (Chrome: Settings > Passwords > Export). Expected
  columns `name,url,username,password,note` (to verify against a real export). Parsed in memory, filtered to origins you
  select (default: known ATS / tenant hosts of your jobs), encrypted, the file never stored, response is counts only.
  Generic mapping lets Bitwarden/1Password CSVs work too.
- **Export:** a CSV in the same format, so Chrome/Google can import it back. Plaintext file, so it sits behind the
  Reveal rules above plus a confirmation step.
- The browser's own "Save password?" bubble stays a harmless secondary; **nothing waits on it any more**, so the
  Needs You "Click Save" card (`AccountService.pause_for_save`) becomes optional/removed.

### 1.5 Account creation without losing accounts (write-ahead)
Today the password exists only in the page, so a flaky Save bubble means a lost account. New flow:
1. Agent fills name/email, ticks terms, then **stops before the create-account click** and reports
   `account: {stage: "ready_to_create"}` (new field, still no password key allowed).
2. **Server step, no model:** `AccountService.create_credentials(job)` runs an in-page helper through Neo `evaluate`.
   The helper generates the password with `crypto.getRandomValues`, fills the visible password fields, and **returns the
   password to the server**. Neo does not store tool *results* (ADR 0028 spike), so it is not in Neo's log, and the model
   is not in the loop.
3. Server encrypts and writes the row as `pending` **before** anything is submitted, then clicks the create-account
   button (an account button is not the application submit).
4. On success (next page shows verify/sign-in, or sign-in works) the row becomes `active`; on rejection (password policy)
   it becomes `failed` and the helper reruns with a different symbol set; only the accepted password is kept.
5. The agent resumes for email verification and sign-in. **No owner click in the loop.**

### 1.6 Sign-in from the vault
Server-side injector (`fill_sign_in(selectors, username, password)` via `evaluate`). Existing Neo sessions are
tried first (no password needed). If a site has an account but no stored password, the agent follows the site's
"Forgot password" email link, and step 1.5 captures the new password.

### 1.7 Verification codes and links, read by the server, not the model
`MailCodeReader` drives the Gmail session already open in Neo (no mail credentials, as architecture.md requires):
open a new tab, search `newer_than:1h from:(allowlist)`, open the newest matching message, extract the code or link
with deterministic patterns, validate sender domain (employer/ATS plus the vendor's own sender, e.g. Workday; exact
domains confirmed in recon), age (<= 10 min) and link host, use it, close only its own tab. The model never sees the
code. If extraction fails it falls back to today's agent run (ADR 0024). A `MailSource` interface lets tests use a fake
local inbox. Gmail UI changes are the main fragility.

### 1.8 The one new exposure (needs your acceptance)
Injecting a stored password into a sign-in form sends it as an *argument* to Neo's `evaluate`, and Neo logs tool
arguments in plaintext (`~/.browserclaw/browserclaw.sqlite`, `tool_dispatches.args_json`). So the password is
decrypted in server memory, passes over loopback MCP, and lands in that local file. That is weaker than ADR 0028 (never
left the page) and weaker than Chrome's own Keychain-encrypted store. Mitigations: sessions first (injection only when
the session expired or a tenant is new), rotation on suspicion, and an **optional** host-side scrub of those log rows
(touches another app's database, so off unless you ask). Creation (1.5) does not have this exposure.

## 2. Workstream B — Workday

Unchanged from rev 1 except the account step now uses A. Summary:
- **Live-verified through Neo, read-only:** public CXS API (`/wday/cxs/{tenant}/{site}/jobs`, `.../job/{path}`) works
  anonymously with title, description, location, `includeResumeParsing`, `questionnaireId`; posting page has
  `adventureButton`; `/apply` offers `autofillWithResume`, `applyManually`, `useMyLastApplication`;
  `.../apply/applyManually` renders a **6-step wizard** (Create Account/Sign In, My Information, My Experience,
  Application Questions, Voluntary Disclosures, Review).
- **Unverified (Phase 0):** field ids and widgets in steps 2-5, verification mail sender, confirmation text, session expiry.
- **Policy:** replace the five hardcodes with `boards.max_policy`; Workday default `manual`, may be set to `assisted`
  (review only), **never `auto`**. `SubmitModeService` already forces review when the policy is not `auto`.
- **Prefetch:** CXS fetcher in `posting_fetch.py` (no model call). **Entry:** `apply_url = <posting>/apply/applyManually`;
  never "Autofill with Resume".
- **Wizard:** `agent/boards/workday.md` recipe; `Save and Continue`/`Next` are not final; stop on the step named Review.
  Optional `browser/workday.py` (data-automation-id helpers) only if Haiku proves unreliable on custom widgets.
- Safety nets reused: `audit_filled`, question gate (EEO/legal answers only from saved answers), `unapproved_submit` kill
  switch, `submit_clicked` fence.

## 3. Phases

| # | Work | Exit criterion |
|---|---|---|
| A1 | ADR 0031 + AGENTS rule 4 amendment (after your yes); migration `0033`; `CredentialVault` (AES-GCM, key file, AAD); unit tests incl. tamper/AAD-swap/wrong-key | tests green; no plaintext in DB dump |
| A2 | Dashboard Accounts: list, Reveal, Export, CSV import (Google/Bitwarden formats), audit events | import/reveal/export covered by API tests |
| A3 | Server-side create (1.5) + sign-in injector (1.6) + `ready_to_create` result field; fixture ATS gets sign-up, verify-mail and sign-in pages | fixture run: account created, verified, signed in, **zero owner clicks**, no password in events/logs |
| A4 | `MailCodeReader` + `MailSource` + fake inbox; fallback to ADR 0024 | fixture run gets code without the model seeing it |
| B0 | Workday recon through a signed-in Neo session (you create the account, via A3 or by hand); stops at Review | section 2 unverified list resolved in `docs/workday-recon.md` |
| B1 | `max_policy` plumbing, remove 4 hardcodes, migration `0034` | default manual; `assisted` ok; `auto` refused; forced review test |
| B2 | CXS prefetch + canonical tenant/site parsing + fixtures | metadata with zero model calls |
| B3 | Workday recipe, confirmation patterns, Workday-like wizard fixture (combobox, segmented date, repeating section, Save and Continue) | fixture run reaches `awaiting_approval` filled, Submit untouched |
| B4 | Live run on a posting you choose, review mode | you inspect the parked tab; software never submits |
| docs | ADR 0032 (Workday supersedes the sentence in 0007), architecture.md, AGENTS.md | docs match code |

Order: A1 -> A2 -> A3 -> A4 -> B1 -> B2 -> B3 -> (you create the account) B0 -> B4. A and B1/B2 are independent and can overlap.

## 4. Tests (all offline unless stated)
Vault crypto (round trip, AAD swap, tampered ciphertext, missing/short key file, key rotation); CSV parsers (Google,
Bitwarden, malformed, oversized, formula-injection cells); reveal/export auth and audit; result schema still refuses any
password key; write-ahead order (row `pending` before the click, `failed` on policy rejection reuses nothing); mail
reader (sender allowlist, age, link host, fake inbox); policy (`workday` default manual, `assisted` allowed, `auto`
refused); existing 694 tests stay green. Live (needs you): Gmail extraction and one Workday tenant.

## 5. Risks
Employer terms may forbid automated applications (opt-in per board, your call); Gmail DOM drift; Workday tenant
variance in steps 4-5; password-policy rejection loops (bounded retries); key-file loss; the section 1.8 exposure.

## 6. Decisions I need from you
1. Accept reversing "never store passwords" with the encrypted vault (A1).
2. Accept the section 1.8 Neo-log exposure on sign-in (vs. keeping passwords out of the server entirely and living with
   the unreliable Save bubble).
3. Key file in `data/.vault-key` (recommended) vs. something stricter.
4. Whether the Needs You "Click Save" card is removed (recommended) or kept as an optional hint.

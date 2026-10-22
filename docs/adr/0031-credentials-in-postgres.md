# 0031. Job-site logins are stored in PostgreSQL, encrypted at rest

Status: accepted (owner decision, 2026-10-19). Amends AGENTS.md hard rule 4 and reverses the "no stored password"
property of ADR 0028 (which stays in force for SSO-first and for what the model may see).

## Context
ADR 0028 kept every generated password inside the page and relied on Chromium's "Save password?" bubble plus an owner
click. In practice that is unreliable: when the bubble does not appear or is missed, the account exists and its password is
gone. Owner request: the software must store the passwords it creates, in PostgreSQL, simple for the owner and the agent.

Hashing (bcrypt/argon2, OWASP Password Storage) is for verifying a password you receive. Here the server must **replay**
the password into a login form, so a one-way hash is unusable. OWASP Cryptographic Storage says to encrypt only when the
value must be recovered, with an authenticated mode, a CSPRNG key kept apart from the data, never hard-coded and never in
an environment variable or git. That is the design below.

## Decision
1. **Table `site_credentials`** (migration `0033`): `origin`, `username`, `ciphertext bytea`, `nonce bytea` (12 bytes),
   `key_id smallint`, `state` (`pending|active|failed|retired`), `source` (`generated|imported|manual`), timestamps.
   `UNIQUE(origin, username)`. One account per tenant origin, e.g. `https://nvidia.wd5.myworkdayjobs.com`. No plaintext
   password column exists.
2. **AES-256-GCM** (`domain/secretbox.py`, `cryptography` library): random 96-bit nonce per seal, AAD =
   `origin|username`, so a ciphertext copied onto another row fails authentication. Any tamper, wrong key, unknown
   `key_id` or bad nonce raises `ValueError`; nothing is ever returned in a degraded form.
3. **Key custody** (`config/vault_key.py`): 32 random bytes, hex, in `data/.vault-key`, mode 0600, created atomically
   (private temp file, `os.link` into place, so concurrent containers agree on one key), opened with `O_NOFOLLOW`,
   permissions re-tightened on read, length and encoding validated. `data/` is gitignored and bind-mounted into the
   containers. `docker/backup.sh` bundles only the database dump and `data/vault`, so **neither git nor a backup bundle
   contains the key**; a stolen dump or bundle alone yields only ciphertext.
4. **Rotation.** `key_id` selects the key; `SecretBox` holds several, seals with the current one and opens with any.
   A rotation tool (re-seal all rows under a new id) is a follow-up; the schema and box already support it.
5. **Write-ahead.** `CredentialService.reserve` stores a generated password as `pending` BEFORE it is submitted to a site.
   `activate` follows a verified/successful sign-in; `fail` follows a rejection; a retry regenerates (default symbols, then
   `-_.`). An `active` row is never overwritten by `reserve` or by a CSV import.
6. **Where plaintext may flow.** Only (a) `password_for_signin`, for server code that types it into a page, and (b) the
   owner's dashboard session via `reveal`/`export`, both POST + CSRF + `UserAuth`, `Cache-Control: no-store`, audited by
   events (`credential_revealed`, `credential_exported`) that never carry the value. Listing returns metadata only and is
   open to bearer tokens. The model never receives a stored password in DATA, prompts, results or events; the result
   schema still refuses any password-named key.
7. **Google Password Manager / other managers.** No API exists, so it is file-based: CSV import (preview first,
   default scope = known ATS hosts and hosts of your jobs; existing logins are skipped; file parsed in memory and never
   stored) and CSV export in Chrome's column order (`name,url,username,password,note`).

## Consequences and accepted risk
- A database dump, `data/pgdata` or a backup bundle on its own reveals no password. An attacker needs the dump **and**
  the key file, i.e. code execution as the owner on the host. FileVault remains the control for the host itself.
- **Losing `data/.vault-key` makes every stored password unrecoverable** (restoring a backup onto a new machine has the same
  effect, because bundles deliberately exclude the key). Recovery is each site's forgot-password flow. Copy the key into the
  owner's password manager if that risk matters.
- Single-key shortcut: the key encrypts rows directly (no per-row DEK wrapped by a KEK, no HSM/KMS). That is the usual
  envelope-encryption step up for multi-user systems; for one owner on one host it adds moving parts without a different
  trust boundary. `key_id` leaves room to add it later.
- Injecting a stored password into a page is an argument of Neo's `evaluate`, and Neo logs tool arguments in plaintext
  (`~/.browserclaw/browserclaw.sqlite`, `tool_dispatches.args_json`, per ADR 0028's spike). That exposure exists on every
  sign-in that uses a stored password and is outside this ADR's control.
## Account creation and sign-in injection
`service/account_flow.py` (`AccountFlow`) drives a tab through `NeoBrowser.evaluate` with the page scripts in
`browser/signin.py`. Every script refuses to run unless `location.origin` equals the credential's origin (top frame only),
so a redirect or wrong tab never receives the password. `create_account` writes the `pending` row first, fills email,
password and confirmation with native setters plus read-back, clicks the form's own submit button, then polls the page.
Rules: only a visible site error marks a row `failed` (a password-policy error first retries once with `-_.` symbols); an
unreadable page leaves it `pending`; required unticked terms checkboxes are never ticked (`consent_required`); a row turns
`active` only when a later sign-in leaves the form with no error. Verified against a local fixture ATS
(`backend/tests/ats_fixture.py`): scripted-browser unit tests always run, and `JOBPILOT_LIVE_NEO=1` runs the same flows in
real Neo (sign-up, sign-in, policy retry, verification page, consent stop, wrong password, wrong-origin tab).

### Run pipeline (replaces the ADR 0028 helper and save card)
The agent no longer generates or fills passwords. At a sign-up or sign-in form it fills non-secret fields, stops and returns
`blocked` with `account_wall: "signup"|"signin"` (schema: only with `blocked`). `worker/runner.py` passes that to
`service/account_wall.py`, which reads the parked tab's `location.origin`, never creates a second account for an origin that
already has a stored login (signs in instead), uses the profile email, runs `AccountFlow`, records `account_wall` /
`account_created` events, releases the auto-mode fence (an account step is never the application submit), and queues the
agent again (`account_ready`, attempt refunded). DATA gains `login_hosts` (hosts that have a usable login, so the agent recognises a sign-in page the server can handle, also after a redirect from a company site to its ATS). Anything but created / verify_email / signed_in
puts the job in Needs You with a code-free reason; more than 4 walls per job also stops. Removed: `AccountService`,
`POST /jobs/{id}/password-prompt`, the 10-minute expiry tick, the dashboard card and the NEO.md helper. Old
`password_save_*` events remain in history.

Not part of this ADR (follow-ups): email-code entry by the server (the agent still reads codes from Gmail, ADR 0024); a
dashboard Accounts page; the key-rotation tool; a live end-to-end run on a real employer site.

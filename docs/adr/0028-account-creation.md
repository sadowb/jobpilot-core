# 0028. Account creation with a generated password the agent never sees

Status: accepted (owner decision, 2026-10-18). Amends ADR 0024 (sign-in) and AGENTS.md hard rule 4. **The in-page password helper and the Needs You save-password card are superseded by ADR 0031** (server-run account step, encrypted store); the model-never-sees-a-password property stays.

## Context
Many employer sites require an account before the application form. Until now the agent had to stop with `blocked` and
the owner created the account by hand. Constraints: the model must never hold, type, read or report a password; the
server stores no credential (hard rule 4); Neo is the only browser.

Phase 1 spike (read-only, no account created) found:
- Neo exposes no credential tools. Its MCP tools are act, snapshot, run, evaluate, read, grep, navigate, tabs, upload,
  download, screenshot, request_human_help and similar; `page.cdp` is a restricted CDP. Chromium's password manager is
  not reachable through any of them.
- Chromium's native "Save password?" bubble is browser UI: no Neo path (MCP, run SDK, CDP) can click it, and there is no
  preference that saves silently.
- Neo logs every tool call's arguments in plaintext (`~/.browserclaw/browserclaw.sqlite`, table `tool_dispatches`,
  `args_json`); results are not stored.

## Decision
1. **SSO first.** "Continue with Google" is preferred. No password exists; the run reports `account.method = "sso"`.
2. **Otherwise one verbatim `run` helper** in `agent/NEO.md` generates a 20-character password inside the page with
   `crypto.getRandomValues` (upper, lower, digit, symbol guaranteed, shuffled) and writes it into the visible password
   inputs through the native value setter plus `input`/`change` events. It returns only `{filled, fields, len}`. The
   password is a local variable of the in-page expression: it is not in the helper's source, arguments or result, so it
   is not in the model context, the Claude CLI session, Neo's `tool_dispatches` log, or any server record. It refuses
   unless there are two visible password inputs or one with `autocomplete=new-password`.
3. **Chromium saves it, the owner clicks Save.** After the create-account click the run returns `blocked` with
   `account = {created, method}`. The server records `account_created` (method only), writes a
   `password_save_prompt` event and parks the job on the Needs You card "Click Save on the password prompt in Neo"
   (buttons: Find agent tab, "I saved it", "Skip: I can reset by email"; no password text anywhere). Either button (`POST /jobs/{id}/password-prompt`,
   user session only, idempotent) writes `password_save_resolved` and resumes the job. After 10 minutes the existing park-watch
   beat task (no new schedule) resolves it as `timeout` and resumes, so a forgotten card never stalls the queue.
4. **Verification and sign-in.** Gmail may be used for a code or an activation link only when it comes from the
   employer or its ATS (ADR 0024 otherwise unchanged). The resumed run may sign in with the browser's saved-password
   autofill for the sign-in password field of the account just created, and nowhere else; with no saved entry it
   returns `blocked`. Review and auto instructions both state that account buttons are not the application submit; the
   stop-before-Submit and one-click rules are kept at both ends of the instruction (ADR 0026). In auto mode the server
   releases the `submit_clicked` fence before parking on the save card, because an account button is not the submit.
5. **Server defenses.** The result schema accepts `account` with only `created` and `method` (`extra=forbid`), and
   rejects any key containing passw/passcode/pwd/passphrase at any depth (`password_key_refused`). Existing label and
   value filters stay. Owner answers whose label looks like a password are refused. The `account_created` event holds
   the method only. On the generated path the reported email field must equal the profile email, else the job parks
   for the owner.

## Residual risk (accepted by the owner)
The server cannot make the model unable to type a password. If the model ignores `NEO.md` and uses `act` fill/type
with a password it has invented, that argument lands in Neo's `tool_dispatches.args_json` (and in Neo recordings if
enabled) and in the transient Claude CLI session file (scrubbed after the run by `AgentSessions.sanitize`). The
password would also exist only as the model's own invention, for a throwaway job-site account. The defenses above
reject a reported password and never store one, but they cannot erase Neo's log. Owner accepted this on 2026-10-18.
Other limits: the email check trusts the agent's report of the form; the save prompt is the owner's click, so
"Done" is trusted; Chromium may not offer a save on every form (the account then has no stored password and the
owner resets it on the site).

## Alternatives rejected
- Neo password-manager API: not reachable through MCP or CDP.
- Clicking the native bubble with OS-level automation: outside the agent boundary, fragile, widens trust.
- Server-generated and server-stored passwords: breaks hard rule 4.
- Letting the model generate and type: leaks into logs on every use, not only on deviation.

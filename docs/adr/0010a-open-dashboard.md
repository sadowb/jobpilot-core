# ADR 0010a — Open the local dashboard directly

Status: accepted (2026-10-10). Supersedes the session bootstrap decision in ADR 0004. Number 0010 remains reserved for step 2.

## Context

The owner needs to open `http://127.0.0.1:4100` without handling credentials. The one-time startup credential and in-memory sessions prevented ordinary navigation and lost authentication after each restart. A manually backgrounded server did not survive reboot.

The owner account is already trusted: its processes can read `.env` and write to PostgreSQL directly. A startup credential therefore added no meaningful boundary against local processes. Web attackers remain constrained by the Host allowlist, Origin plus CSRF checks, SameSite=Strict cookies and CSP. Workers have a scrubbed environment and only the fixed `Bash(jp *)` toolbox, not curl.

## Decision

Use Starlette SessionMiddleware with a signed HttpOnly `jp_session` cookie, 30-day max age, SameSite=Strict, path `/`, and no Secure flag on loopback HTTP. Persist the signing secret in mode-0600 `data/.session-secret`, created with exclusive, no-follow open and 48 random bytes encoded using `secrets.token_urlsafe`. Repair existing file permissions before reading; never log its contents. Session data contains the CSRF token.

Mint a session only on `/` when the Host has passed the existing allowlist and browser fetch metadata says `Sec-Fetch-Site: none` or `same-origin` plus `Sec-Fetch-Dest: document`. Missing metadata and cross-site or same-site navigation do not mint. Page JavaScript cannot forge those headers; local tools can, which is consistent with the trusted-account boundary. `GET /api/session` never mints and returns 401 without a session, retaining the Origin check when supplied. Bearer authentication, human-only approvals, non-GET/HEAD Origin/CSRF enforcement, CSP and API-token creation stay unchanged.

On a session 401, the frontend navigates to `/` once, guarded by sessionStorage. If storage cannot persist the guard, it skips automatic navigation. Continued failure shows instructions to open the normal browser. Successful authentication clears the guard.

Install the server as the user's `com.jobpilot.server` launchd agent using the same plistlib template rendering, private logs, lint and bootout/bootstrap workflow as the worker. RunAtLoad and KeepAlive restore the server at login following reboot and after crashes. The orchestrator must stop the old server on port 4100 before loading it. `infra/JobPilot.command` opens a Chrome app window with a default-browser fallback.

## Consequences

- Normal navigation opens Today; existing cookies remain valid across restarts and reboots while within their 30-day lifetime and while the signing secret is retained.
- No manual credential handling or session dictionary is needed. Removing/replacing the signing secret invalidates existing cookies.
- Browsers without the required fetch metadata cannot establish a session. Anonymous curl and cross-site requests do not acquire one.
- The launchd agent runs in the login account's GUI domain; it starts after login, not before login.
- Activation and live browser, restart, HTTP rejection and bearer checks belong to the orchestrator; this change is verified statically without a test suite.

# 0019. BrowserOS neo as the job browser; Docker Compose instead of launchd

Status: accepted (owner decision, 2026-10-17)

## Context
JobPilot drove a supervised Chrome over raw CDP, ran agents through a macOS-only Seatbelt harness, and ran its eight
services as launchd agents that restarted at every login. That tied the system to one OS, left a second Chrome competing
with the owner's browser, and made it impossible to run the stack anywhere else.

## Decision
1. **Browsing goes through BrowserOS neo's MCP endpoint** (`NEO_MCP_URL`, default `http://127.0.0.1:9010/mcp`).
   - Server side: `browser/neo.py` implements the operations the services already use (`open_tab`, `tabs`,
     `page_text`, `page_state`, `capture`, `navigate`, `evaluate`, `close_tab`) over MCP, behind `browser/engine.py`
     (`BROWSER_BACKEND=neo|chrome`, default neo). `SharedChrome` and the harness stay for `chrome` until the owner
     approves removing them.
   - Agent side: the `claude` CLI is unchanged and gets the whole Neo server (`--allowedTools mcp__neo`) plus
     `agent/NEO.md`. It still has no shell and no built-in tools.
2. **Compose replaces launchd** (`compose.yaml`, `Dockerfile`): postgres, redis, migrate, server, worker, control, beat.
   Ports are published on host loopback only. The repository is bind-mounted at the same absolute path so CV paths the
   agent hands to Neo (which runs on the host) resolve there too.
3. **Board data comes from public read APIs first** (Greenhouse, Lever, Ashby, Workable, SmartRecruiters), the browser is
   for filling and submitting.

## Consequences and residual risk
- Neo is the owner's signed-in browser and the agent has all of its tools. A hostile posting could try to steer the agent
  to other pages or accounts. Mitigations that remain: untrusted-data rules, no built-in tools, **the server-side submit
  fence and approval gate are unchanged** (approvals exist only from the dashboard session, one submit per approval,
  server-side confirmation detection), per-job tab binding in the instructions (policy, not enforcement).
- Neo has no tab-activate call, so "show the live tab" cannot raise the tab; `focus` reports it as unsupported.
- `attach_file` by CSS selector is not available through Neo (its `upload` takes a snapshot ref); the agent uploads.
- Neo's MCP endpoint listens on all interfaces without authentication. Bind it to loopback or add a token in Neo itself;
  the server only accepts a loopback or `host.docker.internal` endpoint.
- The worker container needs `CLAUDE_CODE_OAUTH_TOKEN` (no keychain in a container).

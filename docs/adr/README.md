# ADR index

Status is copied from each file's header; "chain" notes come from those headers. Numbers 0009, 0010 and 0011 are each used
by two files (history of parallel work); both files of a number are listed.

Read first for the current system: `../architecture.md`, `../../AGENTS.md`, then ADR 0012 (execution), 0019/0020 (Neo,
Compose), 0023 (ingest + sessions), **0030 (submit policy)**.

| ADR | Topic | Status / chain |
|---|---|---|
| 0001 | PostgreSQL system of record, vault as view | accepted |
| 0002 | Agents use a fixed `jp` toolbox | accepted (agent surface later replaced by Neo MCP, 0019/0020) |
| 0003 | FastAPI backend | accepted |
| 0004 | Review hardening: fenced submissions/workers | accepted; session bootstrap part superseded by 0010a |
| 0005 | Audited writes, projection, runtime adapters | accepted |
| 0006 | Worker process, durable admission | accepted; unknown-output reservation superseded by 0008; daily caps superseded by 0025 |
| 0007 | Catalogue, board caps, bounded form pages | see file; board caps removed by 0025; Workday forced manual, superseded by 0032 |
| 0008 | Worker trust, maintenance, pacing identity | accepted |
| 0009 (a) | Browser hands | see file (agent-browser era) |
| 0009 (b) | Feature UI and layered FastAPI | superseded by 0010 (spring-style) |
| 0010 (a) | Raw `agent-browser` for the worker | see file (agent-browser era; browser stacks removed by 0020) |
| 0010 (b) | Backend layers + versioned API | accepted |
| 0010a | Open local dashboard | accepted; supersedes session bootstrap of 0004 |
| 0011 (a) | Preserve browser sessions during approval | accepted (parked tab concept lives on in 0026) |
| 0011 (b) | Read-only email verification reader | superseded by 0024 |
| 0012 | Celery + Redis execution | accepted (**live**) |
| 0013 | Browser footprint | superseded by 0014 |
| 0014 | One shared Chrome, one tab per job | accepted (concept kept with Neo) |
| 0015 | Direct Haiku browser operation under host sandbox | accepted |
| 0016 | Agent guidance and tab binding | accepted |
| 0017 | Bulk profile fill before model | accepted |
| 0018 | Reasoned CV selection | accepted |
| 0019 | BrowserOS Neo + Docker Compose | accepted (**live**) |
| 0020 | Neo is the only browser | accepted; its submit-run/approval-gate parts superseded by 0026 |
| 0021 | Runtime image tools | accepted |
| 0022 | Compose-native backup | accepted |
| 0023 | Ingest preparation, one conversation per job | accepted (**live**) |
| 0024 | Neo sessions for sign-in and email | accepted; supersedes 0011 (b) |
| 0025 | Daily usage is monitoring only | accepted; supersedes daily budgets of 0006/0012 |
| 0026 | Owner submits (review mode) | accepted, **amended**; consolidated in 0030 |
| 0027 | Live dashboard updates (PG NOTIFY + SSE) | accepted |
| 0028 | Account creation, password never seen by model | accepted; amends 0024 and AGENTS.md rule 4; helper and save-password card superseded by 0031 |
| 0029 | Refresh details for any job | accepted |
| 0030 | Submit policy as it stands | accepted (**current submit policy**) |
| 0031 | Job-site logins stored in PostgreSQL, AES-256-GCM encrypted (`site_credentials`) | accepted; amends AGENTS.md rule 4, reverses the no-stored-password property of 0028 |
| 0032 | Board policy is the owner's (Workday unlocked), CXS prefetch, entry hooks, live board recipes | accepted; supersedes the forced-manual sentence of 0007 |

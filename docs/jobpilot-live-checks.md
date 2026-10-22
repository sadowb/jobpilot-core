# Validation for parts A–E

## Local validation — 2026-10-18

Merged `feat/neo-codex` into `feat/compose-neo` by fast-forward at `acb9506`. Verified a DB/vault backup,
built the runtime image and dashboard, applied `0023_ingest_preflight.sql` and `0024_agent_sessions.sql`,
and recreated server, worker, control and beat. The obsolete prepare-queue container was stopped;
the lane4 worktree was left untouched. Automation remains paused.

- Frontend typecheck, build, ESLint, Prettier and question-card renderer checks pass. Regenerated the
  API types to match the backend defaults and literal Haiku model contract.
- 500 backend unittest tests pass; Ruff lint/format and strict mypy pass. Fixed the static type at
  the runtime-settings boundary without changing model selection or runtime validation.
- PostgreSQL dispatch: 27/27 scenarios pass. Scenario 22 now marks its ingest preflight complete,
  matching the other admission fixtures, before testing browser pacing.
- `backend/scripts/check_agent_sessions_db.py` passes against disposable databases: UUID reuse,
  missing/corrupt transcript recovery, context reset, sanitization, permissions, sweep and terminal
  cleanup. It also verifies migration 0024 with an existing nullable UUID column from the older
  `0023_agent_session.sql`; the migration now preserves that column.
- Running-stack checks: queue, job workers and Neo are online; Accounts routes are absent.
- Live pinned CLI 2.1.302, using Haiku 5.5 and a private config directory: created a transcript with
  directory mode 0700 and transcript mode 0600. Sanitization removed synthetic tool inputs and left
  no ancillary files. A blank Neo test tab opened and closed successfully through the server client.
  The model run returned `usage_limit` through `run_failure`; the resume attempt also failed.
  No reset-time metadata was captured. Successful model-driven tool exchanges, redacted-history
  resume/compaction, restart-and-resume, and fixture email verification are still unverified.
- Fresh real-job dashboard ingestion reached `screened`, fetched the posting and 34 questions from
  Greenhouse without a browser, then recorded `ingest_preflight_failed` (`ClaudeRunError`) and took
  the fallback path. No employer form was filled or submitted; the queued job remains paused.

The transient raw-output risk remains: post-run sanitization cannot guarantee that sensitive tool
output never reaches private disk. The tests used synthetic data and no real email codes.
No real application was submitted. Complete the remaining model-dependent checklist after CLI
capacity is available; database UUID reuse alone is not proof of successful CLI resume.

## Original offline report

This implementation was checked offline. PostgreSQL, Docker, Neo, live Haiku, frontend dependencies and the pinned
container CLI were unavailable. No containers or database were started, no network used, no push attempted. Changes
are uncommitted because this sandbox cannot create the linked worktree's Git index lock.

## Offline results

500 unit tests passed. Ruff lint passed; Ruff format check passed (147 files); strict mypy passed (117 source files).
`git diff --check` and lint of `scripts/check_dispatch.py` passed. `lite/` is unchanged. Frontend TypeScript dependency
resolution failed with MODULE_NOT_FOUND, so tsc/build/renderer checks were not run. PostgreSQL scenarios were not run.

## Offline commands

From `backend/`, using the main checkout's venv:

```sh
PYTHONPATH=src backend/.venv/bin/python -m unittest discover -s tests
backend/.venv/bin/ruff check src tests
backend/.venv/bin/ruff format --check src tests
backend/.venv/bin/mypy src
```

Tests cover combined CV/answer completion, market and evidence checks, signature reuse, question overflow, stale
owner edits, control failures/expiry, send-once/required gates, bounded HTTP reads, session UUID/argv/config/cwd/OAuth,
startup fallback and non-replay, filesystem isolation/redaction/cleanup/sweep, retired API contracts, verification
metadata/secret rejection, and the preparation-submitted automation kill switch.

## Human checks, with infrastructure available

Use an isolated test deployment and fixture applications/mail. From the repository root:

```sh
pnpm install --frozen-lockfile
(cd frontend && pnpm exec tsc --noEmit && node scripts/check-choice-cards.mjs)
pnpm build:frontend
docker compose build server
docker compose stop server worker control beat
docker compose run --rm migrate
docker compose up -d --force-recreate server worker control beat
docker compose exec -T -e JOBPILOT_HOME=/tmp/jobpilot-dispatch-check -e JOBPILOT_VAULT=/tmp/jobpilot-dispatch-check/vault -e JOBPILOT_NODE=mac control python backend/scripts/check_dispatch.py
```

`check_dispatch.py` creates/drops only `jp_check_<random>` and now defines 27 scenarios, including preflight expiry,
partial/complete batches, repeated send and explicit try-anyway. It needs PostgreSQL CREATE DATABASE permission;
its Neo interface is fake. Do not count it as a live browser test.

In the dashboard, ingest a fixture posting with multiple required unknowns. Confirm one control preflight, at most
one combined model call, no browser attempt until answers are resolved/skipped, one card and one send. Repeat Send;
verify only one resume event/run. Try a partial batch and the explicit try-anyway path. Verify an unsupported API and
an expired/failed preflight reach worker preparation. On a live fixture form, reveal several unknowns and verify
one result/card and one parked tab. Confirm worker preparation does not repeat successful ingest planning.

Run a preparation, answer its questions, and approve the resulting draft. Verify the same UUID is used with
`--session-id` then `--resume`, deltas omit unchanged owner material, and the submit gate/hash/fence remain effective.
Between waiting runs, recreate the worker and verify resume still works:

```sh
docker compose up -d --force-recreate worker
docker compose logs --since 10m worker control
```

With the fixture idle, remove/corrupt only its private transcript and resume: expect a fresh UUID/full DATA and
`agent_session_reset`. Test a CLI startup resume failure separately; never simulate it after a real submit. Verify
terminal/archive/purge cleanup and idle/orphan sweeping, and inspect permissions without printing transcript contents.
Confirm OAuth works with the job-specific config dir, no history appears in the owner's Claude app, and the pinned
CLI accepts the redacted JSONL format through tool exchanges and compaction.

Use fixture webmail already signed into Neo. Verify newest matching code within ten minutes, ignored unrelated/stale
mail, code typed into the original application, and the new mail tab closed on success/failure. Result/events may
contain only the verification metadata. Missing code or signed-out mail must produce a clear Needs You blocker.
Inspect the private CLI directory after completion and after crash recovery for unsanitized ancillary files. Do not
print real codes or send real applications for these checks. Confirm Accounts routes are absent and preparation
instructions name all final-submit buttons. The automated unapproved-submit test exercises detection without a click.

## Deliberate limits

- One ingest model attempt; at most 24 candidate CVs and 60 questions per prompt. Question overflow remains in the
  owner batch. File attachments remain the browser/upload path. Unsupported API, candidate failures and outages use
  the existing worker fallback.
- Control uses cooperative 210-second checks, a 180-second model timeout, bounded HTTP body reads and 20-second PDF
  extraction. PostgreSQL admission expires after four minutes running even after process loss. Celery thread-pool
  time limits are not an OS-level kill guarantee; blocked DNS/database/OS calls can outlast cooperative checks.
- Immediate fresh-session replay is limited to recognized CLI startup errors; ambiguous post-action failures keep
  existing fenced recovery. CLI lookup diagnostics were checked in local 2.1.296; container 2.1.302 is unverified.
- Transcript cleanup is post-run redaction. Raw tool output may transiently reach private disk, especially on a hard
  crash; it is scrubbed before resume and by the sweep. Zero transient persistence is not guaranteed (ADR 0024).
- Frontend types were updated by hand; typecheck, renderer checks and regenerated-schema comparison remain unrun.
- Account schema/quota columns remain unused; there is no destructive migration. No browser-side submit guard was
  added, as the owner chose detection-only.

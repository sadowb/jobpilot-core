# 0025. Daily usage is monitoring, never admission control

Status: accepted (owner decision, 2026-10-18); supersedes the daily budget and per-board daily caps of ADR 0006/0012
and the "daily budgets" server duty listed in ADR 0020.

A daily USD/token ceiling blocked all dispatch once recorded token usage passed the limit (about 2.2 M tokens against
a 2 M limit), stalling every job for the rest of the UTC day. The owner wants spend visible, not enforced.

## Decision
- Remove every daily admission cap: the USD and token budgets, the per-run token reservation, the `budget_reservations`
  and `board_daily_usage` tables, the `boards.max_*_per_day` columns, their settings, claim-SQL checks, blocked/idle
  reasons and Needs You/Settings UI. Migration `0025_remove_daily_limits.sql` drops them (forward-only).
- Recovery: the migration requeues pre-submit, unarchived jobs whose `blocked_reason` is a recognised daily-limit
  reason (`waiting_on='agent'`, `blocked_reason` cleared, a `run` task queued, a `daily_limits_removed` event). Attempts,
  approvals, draft hashes, submit fences and job status are untouched; the state machine stays authoritative. Apply it
  with old workers stopped and leases drained, before the new code runs.
- Keep (physical or safety limits, not daily caps): per-domain pacing, concurrency/slots/`maxOpenBrowsers`, attempts < 5,
  per-run limits (`--max-budget-usd` per Claude run, timeouts, leases), and cost/token recording per run.
- Add monitoring (`monitoring` object on `GET /api/automation`, rendered in the Automation page): spend, tokens, runs
  and average cost per job for today (UTC), last 7 days and all time; live active runs with phase, elapsed time and
  node; queue depth; recent failed runs with their last error. Computed in `service/reporting.py` from
  `cost_ledger`/tasks only; the frontend applies no rules. Failure text is shown as inert text.

## Consequences
Spend can no longer pause automation: a runaway loop is bounded only by per-run caps, attempts < 5 and pacing, so the
owner must watch the panel and use Stop. Dispatch never reads spend, and tests assert that.

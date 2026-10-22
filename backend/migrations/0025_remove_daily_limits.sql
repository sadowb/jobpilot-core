-- Owner decision (ADR 0025): daily usage is monitoring, never admission control.
-- Apply with the old workers stopped and leases drained before deploying new code.
-- Only revive pre-submit, unarchived jobs with a recognized daily-limit reason.
-- Preserve attempts, approval hashes, submit fences and job status; the domain state machine remains authoritative.
WITH unblocked AS (
    UPDATE jobs j SET waiting_on='agent', blocked_reason=NULL, updated_at=now()
    WHERE j.archived_at IS NULL
      AND j.status IN ('discovered','screened','preparing','ready','awaiting_approval')
      AND j.blocked_reason ~* '^(automation error: )?daily ((cost|token) budget exhausted|board (cap reached|(preparation|browsing|account_creation) cap reached))'
      AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.job_id=j.id AND t.state='leased' AND t.visible_at>now())
    RETURNING j.id
), queued AS (
    INSERT INTO tasks(job_id,step,state,visible_at)
    SELECT id,'run','queued',now() FROM unblocked
    ON CONFLICT(job_id,step) DO UPDATE SET state='queued', visible_at=now(), leased_by=NULL,
        leased_node=NULL, started_at=NULL, leased_at=NULL, heartbeat_at=NULL
    RETURNING job_id
)
INSERT INTO job_events(job_id,kind,actor,payload)
SELECT job_id,'daily_limits_removed','system','{"reason":"daily admission limits retired"}'::jsonb FROM queued;
--> statement-breakpoint
UPDATE runtime_settings SET value=value - ARRAY['dailyBudgetEnabled','dailyBudgetUsd','dailyBudgetTokens','runTokenReservation'],
    updated_at=now() WHERE singleton;
--> statement-breakpoint
DROP TABLE budget_reservations;
--> statement-breakpoint
DROP TABLE board_daily_usage;
--> statement-breakpoint
ALTER TABLE boards DROP COLUMN max_per_day, DROP COLUMN max_prepare_per_day,
    DROP COLUMN max_browser_per_day, DROP COLUMN max_account_per_day;
--> statement-breakpoint
-- Supports per-job monitoring without scanning the entire ledger.
CREATE INDEX cost_ledger_job_idx ON cost_ledger(job_id);

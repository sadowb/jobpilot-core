-- ADR 0026: the agent never submits. A job approved under the old flow sits at awaiting_approval waiting on the agent
-- with an unconsumed approval; hand it to the owner (the claim query no longer admits awaiting_approval) and close its
-- queued task. Idempotent; touches no status, submit fence or consumed approval.
WITH leftover AS (
    UPDATE jobs SET waiting_on='you',
        blocked_reason='review the filled application in Neo, submit it yourself, then confirm here'
    WHERE status='awaiting_approval' AND waiting_on='agent'
    RETURNING id
)
UPDATE tasks SET state='done', leased_by=NULL
WHERE job_id IN (SELECT id FROM leftover) AND state IN ('blocked','queued');

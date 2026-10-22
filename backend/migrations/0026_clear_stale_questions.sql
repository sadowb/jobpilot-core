-- Reconcile for the stale Needs You question card: a job past preparation has no open question.
-- Clears stale `job_forms.missing` of such jobs (e.g. one submitted during a "try anyway" run) so Needs You stops
-- rendering them as question cards. Idempotent; touches no status, task, approval or submit fence.
UPDATE job_forms f SET missing='[]'::jsonb, updated_at=now()
FROM jobs j
WHERE j.id=f.job_id
  AND j.status NOT IN ('discovered','screened','preparing','ready')
  AND f.missing IS DISTINCT FROM '[]'::jsonb;

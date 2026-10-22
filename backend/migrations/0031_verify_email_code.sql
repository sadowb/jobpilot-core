-- ADR 0026 (amended again): after the OWNER's submit click, a site may ask for an emailed security code. One short agent
-- run (kind verify_email_code) may read it from Gmail and type it into the same parked tab. `verify_requested_at` is the
-- one-shot request (set by the watcher or the owner's button); the claim lets exactly that job through awaiting_approval
-- until `verify_done_at` is set by the run's terminal outcome.
ALTER TABLE job_forms ADD COLUMN IF NOT EXISTS verify_requested_at timestamptz;
ALTER TABLE job_forms ADD COLUMN IF NOT EXISTS verify_done_at timestamptz;

-- ADR 0026 (amended): while a filled application is parked for the owner, a cheap watcher compares the tab with the
-- baseline stored when it parked. `park_signal` is what it saw when it could not decide on its own.
ALTER TABLE job_forms ADD COLUMN IF NOT EXISTS park_baseline jsonb;
ALTER TABLE job_forms ADD COLUMN IF NOT EXISTS park_signal text CHECK (park_signal IN ('looks_submitted', 'tab_closed'));

-- When the current lease started. heartbeat_at moves on every browser action, so it cannot show how long an agent has
-- been on a job; the dashboard's elapsed time and "slow" flag need the start of the lease.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS leased_at timestamptz;

-- Soft delete for applications: hidden from the tracker, recoverable, purge is a separate explicit step.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS archived_at timestamptz;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS jobs_archived_idx ON jobs (archived_at) WHERE archived_at IS NOT NULL;

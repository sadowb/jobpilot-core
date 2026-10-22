-- Tracker fields: the owner's own application spreadsheet, kept in the system of record.
-- source: platform (JobPilot applied), manual (owner applied, URL logged), imported (spreadsheet row).
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'platform',
    ADD COLUMN IF NOT EXISTS job_level text,
    ADD COLUMN IF NOT EXISTS employment_type text,
    ADD COLUMN IF NOT EXISTS submitted_via text,
    ADD COLUMN IF NOT EXISTS resume_label text,
    ADD COLUMN IF NOT EXISTS date_contacted date,
    ADD COLUMN IF NOT EXISTS interview_on date,
    ADD COLUMN IF NOT EXISTS follow_up_on date,
    ADD COLUMN IF NOT EXISTS user_status text,
    ADD COLUMN IF NOT EXISTS notes text;
--> statement-breakpoint
ALTER TABLE companies ADD COLUMN IF NOT EXISTS description text;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS jobs_source_idx ON jobs (source);

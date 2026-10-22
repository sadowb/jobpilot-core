-- Server-captured state of the job tab after the last submit run (final URL without query, title, bounded text
-- excerpt, screenshot file name). Display data for the owner; it never feeds a prompt or a decision.
ALTER TABLE job_forms ADD COLUMN IF NOT EXISTS submit_evidence jsonb;

-- Global configuration/account changes share the append-only audit stream.
ALTER TABLE job_events ALTER COLUMN job_id DROP NOT NULL;
--> statement-breakpoint
CREATE TABLE runtime_settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    value jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE projection_dirty (
    job_id text PRIMARY KEY REFERENCES jobs(id),
    generation bigint NOT NULL DEFAULT 1,
    requested_at timestamptz NOT NULL DEFAULT now()
);
--> statement-breakpoint
CREATE FUNCTION queue_job_projection() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO projection_dirty(job_id) VALUES (NEW.id)
    ON CONFLICT (job_id) DO UPDATE SET generation=projection_dirty.generation+1, requested_at=now();
    RETURN NEW;
END $$;
--> statement-breakpoint
CREATE TRIGGER jobs_projection AFTER INSERT OR UPDATE ON jobs
FOR EACH ROW EXECUTE FUNCTION queue_job_projection();
--> statement-breakpoint
CREATE FUNCTION queue_application_projection() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO projection_dirty(job_id) VALUES (NEW.job_id)
    ON CONFLICT (job_id) DO UPDATE SET generation=projection_dirty.generation+1, requested_at=now();
    RETURN NEW;
END $$;
--> statement-breakpoint
CREATE TRIGGER applications_projection AFTER INSERT OR UPDATE ON applications
FOR EACH ROW EXECUTE FUNCTION queue_application_projection();
--> statement-breakpoint
INSERT INTO projection_dirty(job_id) SELECT id FROM jobs;
--> statement-breakpoint
CREATE TABLE cv_commit_jobs (
    job_id text PRIMARY KEY REFERENCES jobs(id),
    include_pdf boolean NOT NULL DEFAULT false,
    generation bigint NOT NULL DEFAULT 1
);
--> statement-breakpoint
CREATE TABLE cv_commit_versions (
    version_id bigint PRIMARY KEY REFERENCES cv_versions(id),
    include_pdf boolean NOT NULL DEFAULT false,
    generation bigint NOT NULL DEFAULT 1
);
--> statement-breakpoint
CREATE TABLE browser_sessions (
    job_id text PRIMARY KEY REFERENCES jobs(id),
    opened_at timestamptz NOT NULL DEFAULT now(),
    close_requested boolean NOT NULL DEFAULT false
);
--> statement-breakpoint
CREATE INDEX job_events_kind_job_latest ON job_events(job_id, kind, id DESC);
--> statement-breakpoint
CREATE INDEX jobs_needs_you_page ON jobs(updated_at DESC, id) WHERE waiting_on='you';
--> statement-breakpoint
-- Repair existing CV history through the same durable single-writer queues.
INSERT INTO cv_commit_versions(version_id,include_pdf)
SELECT v.id, v.tailored_for_job_id IS NULL OR EXISTS(SELECT 1 FROM cv_tracks t WHERE t.current_version_id=v.id)
FROM cv_versions v;
--> statement-breakpoint
INSERT INTO cv_commit_jobs(job_id,include_pdf)
SELECT j.id, EXISTS(SELECT 1 FROM applications a WHERE a.job_id=j.id)
    OR EXISTS(SELECT 1 FROM job_events e WHERE e.job_id=j.id AND e.kind='submit_clicked')
FROM jobs j WHERE j.cv_version_id IS NOT NULL;

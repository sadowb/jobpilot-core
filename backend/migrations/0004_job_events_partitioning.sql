-- Monthly range partitioning of the append-only audit log (job_events), keyed on ts.
-- Existing rows are preserved; a DEFAULT partition catches anything outside created months.
ALTER SEQUENCE job_events_id_seq OWNED BY NONE;
--> statement-breakpoint
ALTER TABLE job_events RENAME TO job_events_old;
--> statement-breakpoint
ALTER INDEX job_events_pkey RENAME TO job_events_old_pkey;
--> statement-breakpoint
ALTER INDEX job_events_job_idx RENAME TO job_events_old_job_idx;
--> statement-breakpoint
ALTER INDEX job_events_kind_job_latest RENAME TO job_events_old_kind_job_latest;
--> statement-breakpoint
ALTER TABLE job_events_old RENAME CONSTRAINT job_events_job_id_jobs_id_fk TO job_events_old_job_id_fk;
--> statement-breakpoint
CREATE TABLE job_events (
    id bigint NOT NULL DEFAULT nextval('job_events_id_seq'),
    job_id text REFERENCES jobs(id),
    ts timestamptz NOT NULL DEFAULT now(),
    kind text NOT NULL,
    actor text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);
--> statement-breakpoint
ALTER SEQUENCE job_events_id_seq OWNED BY job_events.id;
--> statement-breakpoint
CREATE TABLE job_events_default PARTITION OF job_events DEFAULT;
--> statement-breakpoint
CREATE INDEX job_events_job_idx ON job_events (job_id, ts);
--> statement-breakpoint
CREATE INDEX job_events_kind_job_latest ON job_events (job_id, kind, id DESC);
--> statement-breakpoint
-- Idempotent: creates monthly partitions from `since` (or this month) through N months ahead.
CREATE FUNCTION ensure_job_event_partitions(months_ahead integer DEFAULT 3, since timestamptz DEFAULT NULL) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    m date;
    last_m date := (date_trunc('month', now()) + make_interval(months => months_ahead))::date;
    made integer := 0;
    pname text;
BEGIN
    m := date_trunc('month', LEAST(now(), COALESCE(since, now())))::date;
    WHILE m <= last_m LOOP
        pname := 'job_events_' || to_char(m, 'YYYYMM');
        IF to_regclass('public.' || pname) IS NULL THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF job_events FOR VALUES FROM (%L) TO (%L)',
                pname, m, (m + interval '1 month')::date
            );
            made := made + 1;
        END IF;
        m := (m + interval '1 month')::date;
    END LOOP;
    RETURN made;
END $$;
--> statement-breakpoint
SELECT ensure_job_event_partitions(3, (SELECT min(ts) FROM job_events_old));
--> statement-breakpoint
INSERT INTO job_events (id, job_id, ts, kind, actor, payload)
    SELECT id, job_id, ts, kind, actor, payload FROM job_events_old;
--> statement-breakpoint
SELECT setval('job_events_id_seq', GREATEST((SELECT COALESCE(max(id), 0) FROM job_events), 1));
--> statement-breakpoint
DROP TABLE job_events_old;

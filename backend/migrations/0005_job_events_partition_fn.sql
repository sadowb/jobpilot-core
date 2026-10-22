-- Replace the first version of the helper (it referenced the transient job_events_old table).
DROP FUNCTION IF EXISTS ensure_job_event_partitions(integer);
--> statement-breakpoint
CREATE OR REPLACE FUNCTION ensure_job_event_partitions(months_ahead integer DEFAULT 3, since timestamptz DEFAULT NULL) RETURNS integer
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

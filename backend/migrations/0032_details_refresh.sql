-- ADR 0029: "Refresh details" re-reads a posting's metadata for any job, including submitted ones. One row per job is
-- the request/lock: queued -> running -> done | failed. It never carries job state; the job row is only touched by the
-- field-by-field merge in service/details.py.
CREATE TABLE IF NOT EXISTS job_detail_requests (
    job_id       text PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    state        text NOT NULL CHECK (state IN ('queued', 'running', 'done', 'failed')),
    requested_at timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz,
    via          text,
    reason       text,
    attempts     integer NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS job_detail_requests_state_idx ON job_detail_requests (state, requested_at);

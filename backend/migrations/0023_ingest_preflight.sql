-- Durable control work; expiry releases browser admission after outages/crashes.
CREATE TABLE job_preflights (
    job_id text PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','running','done','fallback')),
    deadline timestamptz NOT NULL DEFAULT now() + interval '5 minutes',
    published_at timestamptz
);
ALTER TABLE jobs ADD COLUMN try_questions_anyway boolean NOT NULL DEFAULT false;

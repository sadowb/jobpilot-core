-- Counters survive inventory rebuilds and human resumes; attempts retain their charge.
CREATE TABLE IF NOT EXISTS browser_hands_usage (
    job_id text PRIMARY KEY REFERENCES jobs(id),
    actions integer NOT NULL DEFAULT 0 CHECK (actions >= 0)
);
--> statement-breakpoint
ALTER TABLE job_forms
    ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0),
    ADD COLUMN IF NOT EXISTS agent_edits jsonb NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS entry_commands jsonb NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS page_navigation jsonb NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS page_commands jsonb NOT NULL DEFAULT '{}';
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS recipe_suggestions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id text NOT NULL REFERENCES jobs(id),
    board_id text NOT NULL REFERENCES boards(id),
    note text NOT NULL CHECK (length(note) BETWEEN 1 AND 4000),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
    created_at timestamptz NOT NULL DEFAULT now(),
    reviewed_at timestamptz
);

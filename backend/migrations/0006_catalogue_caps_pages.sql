-- Forward-only; repeated execution preserves counters and form progress.
ALTER TABLE boards
    ADD COLUMN IF NOT EXISTS max_prepare_per_day integer NOT NULL DEFAULT 200 CHECK (max_prepare_per_day >= 0),
    ADD COLUMN IF NOT EXISTS max_browser_per_day integer NOT NULL DEFAULT 10000 CHECK (max_browser_per_day >= 0),
    ADD COLUMN IF NOT EXISTS max_account_per_day integer NOT NULL DEFAULT 20 CHECK (max_account_per_day >= 0);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS board_daily_usage (
    board_id text NOT NULL REFERENCES boards(id),
    day date NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')::date,
    preparation integer NOT NULL DEFAULT 0 CHECK (preparation >= 0),
    browsing integer NOT NULL DEFAULT 0 CHECK (browsing >= 0),
    account_creation integer NOT NULL DEFAULT 0 CHECK (account_creation >= 0),
    PRIMARY KEY (board_id, day)
);
--> statement-breakpoint
ALTER TABLE job_forms
    ADD COLUMN IF NOT EXISTS current_page integer NOT NULL DEFAULT 0 CHECK (current_page >= 0),
    ADD COLUMN IF NOT EXISTS pages_complete boolean NOT NULL DEFAULT false;
--> statement-breakpoint
-- Existing Workday settings cannot enable an unsupported wizard.
UPDATE boards SET automation='manual' WHERE id='workday' AND automation<>'manual';

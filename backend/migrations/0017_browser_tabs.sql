-- Shared worker Chrome (ADR 0014): a job's browser is one tab of the node's Chrome, identified by its CDP target id.
-- `context_id` is set only when the tab lives in its own isolated browser context (browser_tab_isolation = context),
-- so closing the job disposes the context. Additive and idempotent; both columns stay NULL for legacy per-job browsers.
ALTER TABLE browser_sessions ADD COLUMN IF NOT EXISTS target_id text;
--> statement-breakpoint
ALTER TABLE browser_sessions ADD COLUMN IF NOT EXISTS context_id text;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS browser_sessions_target ON browser_sessions(node, target_id) WHERE target_id IS NOT NULL;

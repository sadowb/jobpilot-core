-- Node identity for Celery + Redis execution (ADR 0012). Additive and idempotent: a lease records which host
-- holds it, when the run actually began, and which host owns each browser session / worker process, so that
-- browser sweeps and parked approval browsers stay on their own node.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS leased_node text;
--> statement-breakpoint
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS started_at timestamptz;
--> statement-breakpoint
ALTER TABLE browser_sessions ADD COLUMN IF NOT EXISTS node text;
--> statement-breakpoint
ALTER TABLE worker_processes ADD COLUMN IF NOT EXISTS node text;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS tasks_leased_node ON tasks(leased_node) WHERE state = 'leased';

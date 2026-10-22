-- Older deployments applied 0023_agent_session.sql with this same nullable UUID column.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS agent_session_id uuid;
-- Hashes of the last supplied DATA, never a transcript or extra copy of owner values.
ALTER TABLE jobs ADD COLUMN agent_session_context jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE UNIQUE INDEX jobs_agent_session_id ON jobs(agent_session_id) WHERE agent_session_id IS NOT NULL;

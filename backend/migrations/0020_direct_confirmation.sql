-- Reports retain their evidence kind; neither value claims deterministic confirmation.
ALTER TYPE confirmation_kind ADD VALUE IF NOT EXISTS 'agent-reported';
ALTER TYPE confirmation_kind ADD VALUE IF NOT EXISTS 'unapproved-reported';

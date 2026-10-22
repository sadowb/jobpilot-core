-- CV tracks are soft-deleted: history, applications and content-addressed objects stay intact.
ALTER TABLE cv_tracks ADD COLUMN IF NOT EXISTS archived_at timestamptz;

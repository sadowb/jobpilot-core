-- A CV track targets a market (header location / citizenship line / phone differ per market).
-- Existing tracks were written for Morocco.
ALTER TABLE cv_tracks ADD COLUMN IF NOT EXISTS market text NOT NULL DEFAULT 'any';
ALTER TABLE cv_tracks DROP CONSTRAINT IF EXISTS cv_tracks_market_check;
ALTER TABLE cv_tracks ADD CONSTRAINT cv_tracks_market_check CHECK (market IN ('any', 'us', 'morocco'));
UPDATE cv_tracks SET market = 'morocco' WHERE market = 'any' AND archived_at IS NULL;

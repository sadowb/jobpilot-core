-- Python backfill in migrate.apply_data_migration uses domain.canonical.pacing_hostname.
-- The migrator fills existing jobs and drops the transitional default in this transaction.
ALTER TABLE jobs ADD COLUMN pacing_hostname text NOT NULL DEFAULT '';

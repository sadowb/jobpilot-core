-- Data-only migration: apply_data_migration re-derives jobs.pacing_hostname through
-- the board adapters and domain.canonical.pacing_hostname, preserving reservations.
-- Run bin/jp-migrate; executing this file alone does not perform the conversion.
SELECT 1;

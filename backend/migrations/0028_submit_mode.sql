-- ADR 0026 (amended): per-job submit mode override. NULL = use the global runtime setting `submitMode`.
ALTER TABLE jobs ADD COLUMN submit_mode text CHECK (submit_mode IN ('review', 'auto'));

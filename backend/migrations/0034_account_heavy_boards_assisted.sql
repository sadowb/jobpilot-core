-- ADR 0032: iCIMS, Taleo and SuccessFactors sit behind candidate accounts and multi-page wizards that are not verified
-- end to end, so they default to review (assisted) instead of auto. Only rows still at the old default are touched; the
-- owner can set any board back to auto from the board settings.
UPDATE boards SET automation = 'assisted' WHERE id IN ('icims', 'taleo', 'successfactors') AND automation = 'auto';

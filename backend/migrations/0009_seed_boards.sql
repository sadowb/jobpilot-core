-- Seed one boards row per adapter with its default policy so the dashboard lists them all.
-- ON CONFLICT DO NOTHING: owner edits (policy, daily cap) survive.
INSERT INTO boards (id, display_name, automation) VALUES
    ('greenhouse', 'Greenhouse', 'auto'::board_automation),
    ('lever', 'Lever', 'auto'::board_automation),
    ('ashby', 'Ashby', 'auto'::board_automation),
    ('workday', 'Workday', 'manual'::board_automation),
    ('eightfold', 'Eightfold', 'auto'::board_automation),
    ('smartrecruiters', 'SmartRecruiters', 'auto'::board_automation),
    ('icims', 'iCIMS', 'auto'::board_automation),
    ('taleo', 'Taleo', 'auto'::board_automation),
    ('successfactors', 'SAP SuccessFactors', 'auto'::board_automation),
    ('workable', 'Workable', 'auto'::board_automation),
    ('bamboohr', 'BambooHR', 'auto'::board_automation),
    ('linkedin', 'LinkedIn', 'manual'::board_automation),
    ('indeed', 'Indeed', 'manual'::board_automation),
    ('handshake', 'Handshake', 'assisted'::board_automation),
    ('custom', 'Company site', 'assisted'::board_automation)
ON CONFLICT (id) DO NOTHING;

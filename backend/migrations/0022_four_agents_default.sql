-- Four parallel agents is the owner's target. Raise the stored defaults only where they still hold the old defaults
-- (3 agents, 6 browsers); a value the owner chose is left alone. maxOpenBrowsers keeps room for 4 running + 4 parked forms.
UPDATE runtime_settings
SET value = jsonb_set(value, '{workerConcurrency}', '4'::jsonb)
WHERE (value->>'workerConcurrency')::int = 3;

UPDATE runtime_settings
SET value = jsonb_set(value, '{maxOpenBrowsers}', '8'::jsonb)
WHERE (value->>'maxOpenBrowsers')::int = 6;

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/api';
import type { Automation, RuntimeSettings } from '../api/models';
import { useAction } from '../hooks/useAction';
import { AutomationControl } from '../features/automation/AutomationControl';
import { Button, ui, MutationFeedback, PageHeading, Panel, QueryState } from '../components/ui';

const numericFields = [
  {
    key: 'workerConcurrency',
    title: 'Parallel agents',
    hint: 'Agents allowed to run at once, 1 to 32. Never above the worker slots that are online (WORKER_CONCURRENCY per worker container), so a job never waits for a slot it was already given.',
    min: 1,
    max: 32,
    step: '1',
    group: 'workers',
  },
  {
    key: 'maxOpenBrowsers',
    title: 'Open tabs limit',
    hint: 'Tabs in the shared browser: running agents plus filled forms waiting for you to submit them. When reached, agents pause until you submit or dismiss.',
    min: 1,
    max: 64,
    step: '1',
    group: 'workers',
  },
  {
    key: 'browserBudgetUsd',
    title: 'Run cost limit (USD)',
    hint: 'Zero means unlimited. A non-zero value caps each browser run.',
    min: 0,
    max: 100,
    step: 'any',
    group: 'runs',
  },
  {
    key: 'browserRunTimeoutSeconds',
    title: 'Run time limit (seconds)',
    hint: 'Zero means unlimited; an explicit limit must be 30–540 seconds.',
    min: 0,
    max: 540,
    step: '1',
    group: 'runs',
  },
  {
    key: 'agentStallSeconds',
    title: 'Stall watchdog (seconds)',
    hint: 'Direct mode: stop a run after no agent output for this long. Default 900; zero turns it off.',
    min: 0,
    step: '1',
    group: 'runs',
  },
] as const satisfies ReadonlyArray<{
  key: keyof RuntimeSettings;
  title: string;
  hint: string;
  min: number;
  max?: number;
  step: string;
  group: string;
}>;
type NumericKey = (typeof numericFields)[number]['key'];
type NumericDraft = Record<NumericKey, string>;
const toNumericDraft = (settings: RuntimeSettings) =>
  Object.fromEntries(
    numericFields.map((field) => [field.key, String(settings[field.key])]),
  ) as NumericDraft;

// Send only what this form changed, so a stale form can't revert a value changed elsewhere (e.g. the agents stepper).
function changedOnly(draft: RuntimeSettings, baseline: RuntimeSettings): Partial<RuntimeSettings> {
  return Object.fromEntries(
    (Object.keys(draft) as (keyof RuntimeSettings)[])
      .filter((key) => JSON.stringify(draft[key]) !== JSON.stringify(baseline[key]))
      .map((key) => [key, draft[key]]),
  ) as Partial<RuntimeSettings>;
}

function SettingsForm({
  initial,
  automation,
}: {
  initial: RuntimeSettings;
  automation: Automation;
}) {
  const [baseline, setBaseline] = useState(initial);
  const [values, setValues] = useState(initial);
  const [numbers, setNumbers] = useState(() => toNumericDraft(initial));
  const save = useAction((settings: Partial<RuntimeSettings>) => api.saveSettings(settings));
  const locked = automation.running || automation.active.length > 0; // only the non-live settings lock
  const draft = {
    ...values,
    workerConcurrency: Number(numbers.workerConcurrency),
    maxOpenBrowsers: Number(numbers.maxOpenBrowsers),
    browserBudgetUsd: Number(numbers.browserBudgetUsd),
    browserRunTimeoutSeconds: Number(numbers.browserRunTimeoutSeconds),
    agentStallSeconds: Number(numbers.agentStallSeconds),
  } satisfies RuntimeSettings;
  const dirty =
    JSON.stringify(draft) !== JSON.stringify(baseline) ||
    numericFields.some((field) => !numbers[field.key].trim());
  const validNumbers = numericFields.every((field) => {
    const value = Number(numbers[field.key]);
    return (
      numbers[field.key].trim() !== '' &&
      Number.isFinite(value) &&
      value >= field.min &&
      (!('max' in field) || value <= field.max) &&
      (field.step !== '1' || Number.isSafeInteger(value)) &&
      (field.key !== 'browserRunTimeoutSeconds' || value === 0 || value >= 30)
    );
  });
  function numberError(field: (typeof numericFields)[number]): string | null {
    const raw = numbers[field.key].trim();
    if (raw === '') return 'Enter a number.';
    const value = Number(raw);
    if (!Number.isFinite(value)) return 'Enter a number.';
    if (field.step === '1' && !Number.isSafeInteger(value)) return 'Use a whole number.';
    if (field.key === 'browserRunTimeoutSeconds' && value > 0 && value < 30)
      return 'Use zero for unlimited or at least 30 seconds.';
    if (value < field.min) return `Must be at least ${field.min}.`;
    if ('max' in field && value > field.max) return `Must be at most ${field.max}.`;
    return null;
  }
  function numericInputs(group: string) {
    return numericFields
      .filter((field) => field.group === group)
      .map((field) => (
        <label key={field.key} className={ui.label} htmlFor={`settings-${field.key}`}>
          {field.title}
          <input
            id={`settings-${field.key}`}
            className={ui.input}
            type="number"
            required
            min={field.min}
            max={'max' in field ? field.max : undefined}
            step={field.step}
            value={numbers[field.key]}
            onChange={(event) => {
              setNumbers((previous) => ({ ...previous, [field.key]: event.target.value }));
              save.reset();
            }}
            aria-describedby={`hint-${field.key}`}
            aria-invalid={numberError(field) ? true : undefined}
          />
          {numberError(field) ? (
            <span
              id={`hint-${field.key}`}
              role="alert"
              className="text-xs font-normal leading-relaxed text-danger"
            >
              {numberError(field)}
            </span>
          ) : (
            <span
              id={`hint-${field.key}`}
              className="text-xs font-normal leading-relaxed text-muted"
            >
              {field.hint}
            </span>
          )}
        </label>
      ));
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (dirty && validNumbers)
          save.mutate(changedOnly(draft, baseline), {
            onSuccess: (data) => {
              setValues(data);
              setBaseline(data);
              setNumbers(toNumericDraft(data));
            },
          });
      }}
    >
      <div className={ui.notice}>
        <p>
          {automation.running || automation.active.length
            ? 'Worker count and open tabs limit apply live. Stop automation and let active jobs finish to change the rest.'
            : 'Automation is stopped. Every setting is ready to edit.'}
        </p>
      </div>
      <fieldset disabled={save.isPending} className="min-w-0">
        <legend className="sr-only">Runtime settings</legend>
        <Panel title="Workers">
          <div className="grid grid-cols-2 gap-5 max-[560px]:grid-cols-1">
            {numericInputs('workers')}
          </div>
        </Panel>
        <fieldset disabled={locked} className="min-w-0">
          <legend className="sr-only">Agent run limits</legend>
          <Panel
            title="Agent runs"
            description="Long applications have no run time or cost limit by default. Stop ends active runs; the stall watchdog stops a silent agent."
          >
            <div className="grid grid-cols-2 gap-5 max-[560px]:grid-cols-1">
              {numericInputs('runs')}
            </div>
          </Panel>
        </fieldset>
        <Panel
          title="Agent & browser"
          description="Applications run in your BrowserOS neo browser, one tab per job. The browsing agent is fixed to Claude Haiku 5.5 by design; nothing else is selectable."
        >
          <dl className="grid grid-cols-2 gap-5 text-sm max-[560px]:grid-cols-1">
            <div>
              <dt className="font-medium">Browser</dt>
              <dd className="text-muted">BrowserOS neo (MCP, on this machine)</dd>
            </div>
            <div>
              <dt className="font-medium">Model</dt>
              <dd className="text-muted">Claude Haiku 5.5 (Claude Code subscription)</dd>
            </div>
          </dl>
        </Panel>
      </fieldset>
      <div className="mb-6 flex flex-col items-start gap-3">
        <MutationFeedback
          error={save.error}
          success={save.isSuccess}
          message="Runtime settings saved."
        />
        <Button
          type="submit"
          variant="primary"
          loading={save.isPending}
          disabled={!dirty || !validNumbers || save.isPending}
        >
          {save.isPending ? 'Saving…' : 'Save settings'}
        </Button>
      </div>
    </form>
  );
}

export function Settings() {
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.settings });
  const automation = useQuery({ queryKey: ['automation'], queryFn: api.automation });
  return (
    <>
      <PageHeading
        title="Settings"
        description="Control workers, browser capacity and per-run safety limits."
        action={automation.data && <AutomationControl running={automation.data.running} />}
      />
      <QueryState
        pending={settings.isPending || automation.isPending}
        error={settings.error || automation.error}
        retry={() => {
          void settings.refetch();
          void automation.refetch();
        }}
      >
        {settings.data && automation.data && (
          <SettingsForm initial={settings.data} automation={automation.data} />
        )}
      </QueryState>
    </>
  );
}

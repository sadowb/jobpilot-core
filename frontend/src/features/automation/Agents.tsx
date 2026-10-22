import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Minus, Plus } from 'lucide-react';
import { api } from '../../api/api';
import type { ActiveWorker, Automation, RuntimeSettings } from '../../api/models';
import { cn } from '../../utils/cn';
import { useAction } from '../../hooks/useAction';
import { agentSteps, companyName, stepIndex, stepName } from '../../utils/humanize';
import { Button, IconButton, Panel, ui } from '../../components/ui';

const MAX_AGENTS = 32;
const plural = (count: number) => `${count} agent${count === 1 ? '' : 's'}`;

function Elapsed({ seconds }: { seconds: number }) {
  return (
    <span className="tabular-nums">
      {Math.floor(seconds / 60)}m {String(seconds % 60).padStart(2, '0')}s
    </span>
  );
}

function Steps({ step }: { step?: string | null }) {
  const at = stepIndex(step);
  return (
    <span
      className="flex items-center gap-1"
      role="img"
      aria-label={`Step ${Math.min(at + 1, agentSteps.length)} of ${agentSteps.length}: ${stepName(step)}`}
    >
      {agentSteps.map((name, index) => (
        <span
          key={name}
          className={cn(
            'h-1 w-3.5 rounded-full',
            index < at
              ? 'bg-accent/70'
              : index === at
                ? 'bg-accent motion-safe:animate-working'
                : 'bg-line',
          )}
        />
      ))}
    </span>
  );
}

function Tile({ slot, worker }: { slot: number; worker: ActiveWorker }) {
  const doing = worker.started ? stepName(worker.step) : 'Dispatched';
  return (
    <Link
      to={`/jobs/${encodeURIComponent(worker.job)}`}
      className={cn(
        ui.focus,
        'group flex min-h-[7.25rem] min-w-0 flex-col justify-between rounded-card border bg-surface p-4 text-ink transition-colors hover:no-underline',
        'border-line hover:border-accent-line',
      )}
    >
      <span className="flex items-center justify-between gap-2 text-xs text-muted">
        <span className="tabular-nums">Agent {slot}</span>
        <Steps step={worker.step} />
      </span>
      <span className="mt-3 block min-w-0">
        <span className="block truncate text-[13.5px] font-medium group-hover:text-accent">
          {worker.role ?? 'New application'}
        </span>
        <span className="block truncate text-xs text-muted">
          {worker.role ? companyName(worker.company) : 'Reading the posting'} ·{' '}
          {worker.node ?? 'Awaiting worker'}
        </span>
      </span>
      <span className="mt-3 flex items-center justify-between gap-2 text-xs">
        <span className="truncate first-letter:uppercase text-muted">{doing}</span>
        <span className="text-muted">
          <Elapsed seconds={worker.elapsedSeconds} />
        </span>
      </span>
    </Link>
  );
}

// Past a dozen agents, tiles stop being glanceable: one line per agent keeps the whole pool on screen.
function Line({ slot, worker }: { slot: number; worker: ActiveWorker }) {
  return (
    <li>
      <Link
        to={`/jobs/${encodeURIComponent(worker.job)}`}
        className={cn(
          ui.focus,
          'grid grid-cols-[2.25rem_minmax(0,1fr)_auto_3.75rem] items-center gap-3 rounded-control px-2.5 py-2 text-[13px] text-ink hover:bg-raised/60 hover:no-underline max-[560px]:grid-cols-[2rem_minmax(0,1fr)_3.75rem]',
        )}
      >
        <span className="text-xs tabular-nums text-muted">{slot}</span>
        <span className="min-w-0 truncate">
          <span className="font-medium">{worker.role ?? 'New application'}</span>{' '}
          <span className="text-muted">
            · {worker.role ? companyName(worker.company) : 'reading the posting'} ·{' '}
            {worker.node ?? 'Awaiting worker'}
          </span>
        </span>
        <span className="flex items-center gap-2.5 max-[560px]:hidden">
          <span
            className={cn('w-36 truncate text-right text-xs first-letter:uppercase', 'text-muted')}
          >
            {worker.started ? stepName(worker.step) : 'Dispatched'}
          </span>
          <Steps step={worker.step} />
        </span>
        <span className={cn('text-right text-xs', 'text-muted')}>
          <Elapsed seconds={worker.elapsedSeconds} />
        </span>
      </Link>
    </li>
  );
}

/** Live agent pool: resize it on the fly, watch what every agent is doing. */
export function Agents({ data }: { data: Automation }) {
  const [target, setTarget] = useState(data.concurrency);
  const save = useAction(
    (count: number) => api.saveSettings({ workerConcurrency: count } as RuntimeSettings),
    (saved) =>
      `Running ${saved.workerConcurrency} agent${saved.workerConcurrency === 1 ? '' : 's'} in parallel.`,
  );
  useEffect(() => {
    setTarget(data.concurrency);
  }, [data.concurrency]);
  const set = (next: number) => {
    const clamped = Math.min(MAX_AGENTS, Math.max(1, next));
    setTarget(clamped);
    save.mutate(clamped, { onError: () => setTarget(data.concurrency) });
  };
  // Worker IDs are opaque (Celery task UUIDs). Render every active lease, oldest first.
  const working = [...data.active].sort((a, b) => a.since - b.since || a.job.localeCompare(b.job));
  const slots = Math.max(data.concurrency, working.length);
  const idle = Math.max(0, slots - working.length);
  const dense = working.length > 12;
  return (
    <Panel
      title="Agents"
      action={
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted">Parallel agents</span>
          <div
            role="group"
            aria-label="Parallel agents"
            className="inline-flex items-center rounded-full border border-line bg-surface p-0.5 shadow-control"
          >
            <IconButton
              label="Fewer agents"
              className="size-8 rounded-full"
              disabled={save.isPending || target <= 1}
              onClick={() => set(target - 1)}
            >
              <Minus size={14} aria-hidden="true" />
            </IconButton>
            <output
              className={cn(
                'min-w-8 text-center text-[14px] font-semibold tabular-nums text-ink',
                save.isPending && 'opacity-50',
              )}
              aria-live="polite"
            >
              {target}
            </output>
            <IconButton
              label="More agents"
              className="size-8 rounded-full"
              disabled={save.isPending || target >= MAX_AGENTS}
              onClick={() => set(target + 1)}
            >
              <Plus size={14} aria-hidden="true" />
            </IconButton>
          </div>
        </div>
      }
    >
      {!working.length ? (
        <p className="rounded-card border border-dashed border-line px-4 py-7 text-center text-[13px] text-muted">
          {data.running
            ? `${plural(slots)} ready. Nothing to work on right now.`
            : `${plural(slots)} standing by. Start automation to put them to work.`}
        </p>
      ) : dense ? (
        <ul
          className="list-none divide-y divide-line/50 rounded-card border border-line p-1"
          aria-label="Working agents"
        >
          {working.map((worker, index) => (
            <Line key={worker.job} slot={index + 1} worker={worker} />
          ))}
        </ul>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-3">
          {working.map((worker, index) => (
            <Tile key={worker.job} slot={index + 1} worker={worker} />
          ))}
        </div>
      )}
      {working.length > 0 && idle > 0 && (
        <p className="mt-3 text-xs text-muted">
          {plural(idle)} idle, waiting for the next application.
        </p>
      )}
      <dl className="mt-5 flex flex-wrap gap-x-8 gap-y-2 border-t border-line/60 pt-4 text-[13px]">
        {(
          [
            ['Working', working.length],
            ['Finished', data.finished],
            ['Failed', data.failed],
          ] as const
        ).map(([name, value]) => (
          <div key={name} className="flex items-baseline gap-2">
            <dd className="font-serif text-[20px] font-semibold tabular-nums text-ink">{value}</dd>
            <dt className="text-muted">{name}</dt>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-xs leading-relaxed text-muted">
        Changes apply within seconds, even while automation runs. Extra agents finish their current
        job and then retire. Recorded spend is shown in the monitoring panel.
      </p>
    </Panel>
  );
}

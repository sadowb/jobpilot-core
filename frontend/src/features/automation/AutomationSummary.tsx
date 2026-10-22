import { Link } from 'react-router-dom';
import type { Automation } from '../../api/models';
import { idleSentence } from '../../utils/humanize';
import { Chip, Panel, ui } from '../../components/ui';
import { cn } from '../../utils/cn';

const currency = new Intl.NumberFormat(undefined, {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 4,
});
const count = new Intl.NumberFormat();

function MonitoringPanel({ data }: { data: Automation }) {
  const periods = [
    ['Today', data.monitoring.today],
    ['Last 7 days', data.monitoring.last7Days],
    ['All time', data.monitoring.allTime],
  ] as const;
  return (
    <div className="mt-5 border-t border-line/60 pt-5">
      <div className="grid grid-cols-3 gap-6 max-[560px]:grid-cols-1">
        {periods.map(([label, usage]) => (
          <div key={label}>
            <h3 className="text-sm font-semibold text-ink">{label}</h3>
            <p className="mt-2 text-xl tabular-nums text-ink">{currency.format(usage.usd)}</p>
            <p className="text-xs tabular-nums text-muted">{count.format(usage.tokens)} tokens</p>
            <p className="mt-2 text-xs tabular-nums text-muted">
              {count.format(usage.runs)} runs · {count.format(usage.jobs)} metered jobs
            </p>
            <p className="text-xs tabular-nums text-muted">
              {currency.format(usage.averageCostPerJob)} average / job
            </p>
          </div>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Chip tone="neutral">Queue: {data.monitoring.queueDepth}</Chip>
        <Chip tone="neutral">Workers online: {data.workersOnline}</Chip>
        <Chip tone="neutral">Active runs: {data.activeRuns}</Chip>
      </div>
      <p className="mt-3 text-xs leading-relaxed text-muted">
        Recorded model usage in USD, not a subscription invoice. Today and the last seven calendar
        days use UTC. Average covers jobs with recorded usage; unreported usage is excluded. Runs
        count completions in the window plus current runs started in it.
      </p>
      <h3 className="mt-5 text-sm font-semibold text-ink">Recent failed runs</h3>
      {data.monitoring.recentFailures.length ? (
        <ul className="mt-2 divide-y divide-line/60">
          {data.monitoring.recentFailures.map((run) => (
            <li key={run.task} className="py-2 text-xs">
              <Link
                to={`/jobs/${encodeURIComponent(run.job)}`}
                className={cn(ui.link, 'font-medium')}
              >
                {run.role ?? run.job} · {run.company ?? 'Unknown company'}
              </Link>
              <p className="mt-1 text-muted">
                {run.node ?? 'Unknown node'} · {new Date(run.at).toLocaleString()}
              </p>
              <p className="mt-1 break-words text-warn">{run.lastError}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-xs text-muted">No recorded failures.</p>
      )}
    </div>
  );
}

export function AutomationSummary({ data }: { data: Automation }) {
  const reason = data.idleReason;
  const { queueOnline, browserOnline, browsersOpen, browserLimit } = data;
  const heading = !data.running
    ? 'Automation paused'
    : !data.workerOnline
      ? 'Worker offline'
      : reason
        ? 'Automation idle'
        : data.active.length
          ? 'Automation working'
          : 'Waiting for work';
  const description = !data.running
    ? 'Start automation when you’re ready. Active jobs finish before stopping.'
    : !data.workerOnline
      ? 'Automation is enabled. The local worker needs to be brought online before jobs can run.'
      : reason
        ? idleSentence(reason)
        : data.active.length
          ? `${data.active.length} active ${data.active.length === 1 ? 'job' : 'jobs'} of ${data.concurrency} worker slots.`
          : 'No work is running. Eligible jobs will be picked up automatically.';
  return (
    <Panel
      title={heading}
      action={
        <div className="flex flex-wrap items-center gap-2">
          <Chip tone={data.workerOnline ? 'ok' : 'neutral'} dot>
            {data.workerOnline ? 'Worker online' : 'Worker offline'}
          </Chip>
          <Chip tone={queueOnline ? 'ok' : 'neutral'} dot>
            {queueOnline ? 'Queue online' : 'Queue offline'}
          </Chip>
          <Chip tone={browserOnline ? 'ok' : 'neutral'} dot>
            {browserOnline ? 'Browser online' : 'Browser offline'}
          </Chip>
          <Chip tone="neutral">
            Tabs {browsersOpen}/{browserLimit}
          </Chip>
        </div>
      }
    >
      <p className="text-[13.5px] leading-relaxed text-muted" role="status">
        {description}
      </p>
      <MonitoringPanel data={data} />
    </Panel>
  );
}

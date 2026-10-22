import { lazy, Suspense, useCallback, useDeferredValue, useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { CheckCheck, FileSpreadsheet } from 'lucide-react';
import { api } from '../../api/api';
import type { TrackerRow } from '../../api/models';
import { label, statuses } from '../../utils/format';
import { cn } from '../../utils/cn';
import { useAction } from '../../hooks/useAction';
import { pick, useUrlParams } from '../../hooks/useUrlParams';
import { Pager, SortHeader, usePagedSort } from '../../components/ui/PagedTable';
import { AutomationControl } from '../automation/AutomationControl';
import { DuplicatesBanner } from './Library';
import {
  FilterChips,
  matches,
  NoMatches,
  ResultCount,
  SearchField,
} from '../../components/ui/Filters';
import {
  Button,
  Chip,
  Empty,
  LinkButton,
  PageHeading,
  QueryState,
  buttonSmall,
  ui,
} from '../../components/ui';
import { AddMenu, type DialogId } from './AddMenu';
import { Checklist } from './Checklist';
import { columns, MobileCard, TableRow } from './columns';
import { useMedia } from '../../hooks/useMedia';
import { today } from './dates';

// Only loaded when used: keeps the first paint of the home page lean.
const Peek = lazy(() => import('./Peek').then((m) => ({ default: m.Peek })));
const LogApplied = lazy(() => import('./dialogs').then((m) => ({ default: m.LogApplied })));
const ImportSheet = lazy(() => import('./dialogs').then((m) => ({ default: m.ImportSheet })));
const AddForJobPilot = lazy(() => import('./dialogs').then((m) => ({ default: m.AddForJobPilot })));

const over = new Set(['rejected', 'withdrawn', 'closed']);
const statusRank = new Map<string, number>(statuses.map((status, index) => [status, index]));
const sortAccessors = Object.fromEntries(
  columns.filter((column) => column.sort).map((column) => [column.key, column.sort!]),
);
const views = ['all', 'followup'] as const;

function flag(key: string, on: string) {
  try {
    return localStorage.getItem(key) === on;
  } catch {
    return false;
  }
}
function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable */
  }
}

export function Tracker() {
  const navigate = useNavigate();
  const url = useUrlParams();
  const search = url.get('q');
  const query = useDeferredValue(search);
  const filter = url.get('status');
  const tab = pick(url.get('view'), views, 'all');
  const deleted = url.get('deleted') === '1';
  const [peekId, setPeekId] = useState<string | null>(null);
  const phone = useMedia('(max-width: 639px)');
  const [dialog, setDialog] = useState<DialogId | null>(null);
  const refreshMissing = useAction(
    () => api.refreshMissingDetails(),
    (result) =>
      result.queued
        ? `Refreshing ${result.queued} job${result.queued === 1 ? '' : 's'} with missing details.`
        : 'No job is missing its role or company.',
  );
  const [compact, setCompact] = useState(() => flag('jobpilot-density', 'compact'));
  const [more, setMore] = useState(() => flag('jobpilot-columns', 'all'));

  const rowsQuery = useQuery({
    queryKey: ['tracker', deleted ? 'deleted' : 'active'],
    queryFn: () =>
      api.tracker(new URLSearchParams({ limit: '500', ...(deleted ? { archived: 'true' } : {}) })),
  });
  const automation = useQuery({ queryKey: ['automation'], queryFn: api.automation });
  const rows = useMemo(() => rowsQuery.data ?? [], [rowsQuery.data]);

  // One pass over the rows for everything the page summarises.
  const summary = useMemo(() => {
    const counts = new Map<string, number>();
    const due: TrackerRow[] = [];
    const todayIso = today();
    let active = 0,
      needsYou = 0;
    for (const row of rows) {
      counts.set(row.status, (counts.get(row.status) ?? 0) + 1);
      if (!over.has(row.status)) active++;
      if (row.waitingOn === 'you' && row.status !== 'awaiting_approval') needsYou++;
      if (
        row.followUpOn &&
        row.followUpOn <= todayIso &&
        !over.has(row.status) &&
        row.status !== 'offer'
      )
        due.push(row);
    }
    return { counts, due, active, needsYou };
  }, [rows]);

  const baseRows = tab === 'followup' ? summary.due : rows;
  const visible = useMemo(
    () =>
      baseRows.filter(
        (row) =>
          (!filter || row.status === filter) &&
          matches(query, row.company, row.role, row.notes, row.location),
      ),
    [baseRows, filter, query],
  );
  const filtered = !!filter || !!query.trim();
  const clearFilters = () => url.set({ q: null, status: null });

  const paged = usePagedSort(visible, {
    pageSize: 25,
    sort: { key: 'applied', dir: 'desc' },
    accessors: sortAccessors,
    resetKey: `${tab}|${filter}|${query}|${deleted}`,
    sortValue: url.get('sort'),
    onSortChange: (value) => url.set({ sort: value }),
  });
  const shown = useMemo(() => columns.filter((column) => more || column.core), [more]);
  // Previous/next walk the whole filtered + sorted list, turning the page when the neighbour is on another one.
  const closePeek = () => {
    const id = peekId;
    setPeekId(null);
    requestAnimationFrame(() =>
      document
        .querySelector<HTMLElement>(`tr[data-row-id="${id}"], li[data-row-id="${id}"] button`)
        ?.focus(),
    );
  };
  const peekIndex = peekId ? paged.all.findIndex((row) => row.id === peekId) : -1;
  useEffect(() => {
    if (peekId && rowsQuery.isSuccess && peekIndex < 0) setPeekId(null); // filtered out / deleted / purged
  }, [peekId, peekIndex, rowsQuery.isSuccess]);
  const stepPeek = (delta: number) => {
    const next = paged.all[peekIndex + delta];
    if (!next) return;
    setPeekId(next.id);
    paged.setPage(Math.floor((peekIndex + delta) / paged.pageSize));
  };
  // Stable callbacks keep the memoised rows from re-rendering on every parent render (live refresh, typing).
  const onPeek = useCallback(
    (id: string) => setPeekId((current) => (current === id ? null : id)),
    [],
  );
  const onOpen = useCallback(
    (id: string) => navigate(`/jobs/${encodeURIComponent(id)}`),
    [navigate],
  );
  const toApprove = summary.counts.get('awaiting_approval') ?? 0;
  // Every status present gets a chip (pipeline order, unknown ones last), so the chips always add up to "All".
  const chips = [...new Set([...summary.counts.keys(), ...(filter ? [filter] : [])])].sort(
    (a, b) => (statusRank.get(a) ?? 99) - (statusRank.get(b) ?? 99) || a.localeCompare(b),
  );
  const statusOptions = [
    { id: '', label: 'All', count: rows.length },
    ...chips.map((status) => ({
      id: status,
      label: label(status),
      count: summary.counts.get(status) ?? 0,
    })),
  ];
  const stats: [string, number][] = [
    ['Applications', rows.length],
    ['Active', summary.active],
    ['Interviews', summary.counts.get('interview') ?? 0],
    ['Offers', summary.counts.get('offer') ?? 0],
  ];
  const tabClass = (on: boolean) =>
    cn(
      ui.focus,
      'relative flex min-h-10 max-[560px]:min-h-[44px] items-center gap-1.5 rounded-t-control pb-2.5 text-[13px] transition-colors',
      on
        ? '-mb-px border-b-2 border-accent font-semibold text-ink'
        : 'font-normal text-muted hover:text-ink',
    );

  return (
    <>
      <PageHeading
        title="Applications"
        description="Every job you applied to, tracked with quiet precision."
        action={
          <div className="flex flex-wrap items-center gap-2.5">
            <Button
              variant="secondary"
              onClick={() => setDialog('import')}
              className="border-line/80 shadow-none"
            >
              <FileSpreadsheet size={15} aria-hidden="true" />
              Import sheet
            </Button>
            <Button
              variant="secondary"
              loading={refreshMissing.isPending}
              onClick={() => refreshMissing.mutate()}
              className="border-line/80 shadow-none"
            >
              Refresh missing details
            </Button>
            <AddMenu onPick={setDialog} />
          </div>
        }
      />

      <Checklist hasRows={rows.length > 0} loaded={rowsQuery.isSuccess} />
      {!deleted && <DuplicatesBanner />}
      {(toApprove > 0 || summary.needsYou > 0) && (
        <div
          className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-card border border-sage/50 bg-sage/15 px-5 py-3.5 text-ink dark:bg-sage/10"
          role="status"
        >
          <p className="flex items-center gap-2 text-[13px] font-medium">
            <CheckCheck size={17} className="text-accent" aria-hidden="true" />
            {toApprove > 0
              ? `${toApprove} application${toApprove === 1 ? ' is' : 's are'} ready for you to submit`
              : `${summary.needsYou} need${summary.needsYou === 1 ? 's' : ''} your input`}
          </p>
          <Link className={cn(ui.buttonLink, buttonSmall)} to="/needs-you">
            {toApprove > 0 ? 'Review & submit' : 'Open inbox'}
          </Link>
        </div>
      )}

      <dl className="mb-6 flex flex-wrap items-baseline gap-x-8 gap-y-2" aria-label="Summary">
        {stats.map(([name, value]) => (
          <div key={name} className="flex items-baseline gap-2">
            <dd className="font-serif text-[28px] leading-none font-semibold tracking-tight text-ink tabular-nums">
              {value}
            </dd>
            <dt className="text-[13px] text-muted">{name}</dt>
          </div>
        ))}
      </dl>

      <div className="mb-4">
        <FilterChips
          label="Filter by status"
          options={statusOptions}
          value={filter}
          onChange={(status) => url.set({ status: status === filter ? null : status })}
        />
      </div>

      <div className="mb-5 flex items-center justify-between gap-4 border-b border-line">
        <div className="flex items-center gap-6" role="group" aria-label="Application views">
          <button
            type="button"
            aria-pressed={tab === 'all'}
            onClick={() => url.set({ view: null })}
            className={tabClass(tab === 'all')}
          >
            Applications{' '}
            <span className="text-xs font-normal tabular-nums text-muted">({rows.length})</span>
          </button>
          <button
            type="button"
            aria-pressed={tab === 'followup'}
            onClick={() => url.set({ view: 'followup' })}
            className={tabClass(tab === 'followup')}
          >
            <span>To follow up</span>
            {summary.due.length > 0 && (
              <span className="rounded-full bg-warn-soft px-2 py-0.5 text-[11px] leading-none font-medium text-warn">
                {summary.due.length}
              </span>
            )}
          </button>
        </div>
        {automation.data && (
          <div className="flex items-center gap-2.5 pb-2 text-xs text-muted max-[560px]:hidden">
            <Link to="/automation" className="hover:no-underline" title="Manage agents">
              <Chip tone={automation.data.running ? 'ok' : 'neutral'} dot>
                {automation.data.running
                  ? `${automation.data.active.length} of ${automation.data.concurrency} agents working`
                  : `Paused · ${automation.data.concurrency} agents`}
              </Chip>
            </Link>
            <AutomationControl subtle running={automation.data.running} />
          </div>
        )}
      </div>

      <div className="mb-4 flex flex-col gap-2">
        <SearchField
          primary
          className="max-w-md"
          label="Search applications"
          placeholder="Search company, role, location or notes…"
          value={search}
          onChange={(value) => url.set({ q: value })}
        />
        <ResultCount
          shown={visible.length}
          total={baseRows.length}
          noun={
            tab === 'followup'
              ? 'to follow up'
              : deleted
                ? 'deleted'
                : ['application', 'applications']
          }
          filtered={filtered}
          onClear={clearFilters}
        />
      </div>

      <section className="overflow-hidden rounded-card border border-line bg-surface shadow-card">
        <QueryState
          pending={rowsQuery.isPending}
          error={rowsQuery.error}
          retry={() => {
            void rowsQuery.refetch();
          }}
        >
          {!rows.length ? (
            <div className="p-8">
              <Empty
                action={
                  <div className="mt-2 flex flex-wrap justify-center gap-2">
                    <Button variant="primary" onClick={() => setDialog('import')}>
                      <FileSpreadsheet size={15} aria-hidden="true" />
                      Import my spreadsheet
                    </Button>
                    <Button onClick={() => setDialog('applied')}>I applied to a job</Button>
                  </div>
                }
              >
                No applications tracked yet. Import a spreadsheet, log a job you applied to, or
                paste links for JobPilot to apply.
              </Empty>
            </div>
          ) : !visible.length ? (
            <div className="p-8">
              {filtered ? (
                <NoMatches noun="applications" query={query} onClear={clearFilters} />
              ) : (
                <Empty>Nothing to follow up today.</Empty>
              )}
            </div>
          ) : phone ? (
            <ul className="list-none" aria-label="Applications">
              {paged.rows.map((row) => (
                <MobileCard key={row.id} row={row} selected={row.id === peekId} onPeek={onPeek} />
              ))}
            </ul>
          ) : (
            <div
              className="overflow-x-auto"
              role="region"
              aria-label="Applications table"
              tabIndex={0}
            >
              <table
                className={cn(
                  'w-full border-separate border-spacing-0 text-left text-[13px]',
                  more ? 'min-w-[1500px]' : 'min-w-[860px]',
                )}
              >
                <thead className="sticky top-0 z-10 bg-surface/95 backdrop-blur-md">
                  <tr>
                    {shown.map((column) =>
                      column.sort ? (
                        <SortHeader
                          key={column.key}
                          id={column.key}
                          title={column.title}
                          sort={paged.sort}
                          onToggle={paged.toggle}
                          className="px-4 py-3"
                        />
                      ) : (
                        <th
                          key={column.key}
                          scope="col"
                          className="whitespace-nowrap border-b border-line px-4 py-3 text-xs font-normal text-muted"
                        >
                          {column.title}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {paged.rows.map((row) => (
                    <TableRow
                      key={row.id}
                      row={row}
                      shown={shown}
                      compact={compact}
                      selected={row.id === peekId}
                      onPeek={onPeek}
                      onOpen={onOpen}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>

        <div className="px-4 pb-2 sm:px-5">
          <Pager
            page={paged.page}
            pages={paged.pages}
            total={paged.total}
            pageSize={paged.pageSize}
            onPage={paged.setPage}
            onPageSize={paged.setPageSize}
            label="applications"
          />
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line/60 bg-surface px-4 py-3 text-xs text-muted sm:px-5">
          <p className="tabular-nums">
            {rows.length} {deleted ? 'deleted' : 'applications in total'}{' '}
            <span className="max-sm:hidden"> · Click a row for details, double-click to open</span>
          </p>
          <div className="flex flex-wrap items-center gap-x-5">
            <LinkButton
              muted
              onClick={() => url.set({ deleted: deleted ? null : '1', status: null, view: null })}
            >
              {deleted ? 'Back to applications' : 'Show deleted'}
            </LinkButton>
            {!phone && (
              <LinkButton
                muted
                onClick={() => {
                  remember('jobpilot-columns', more ? 'core' : 'all');
                  setMore(!more);
                }}
              >
                {more ? 'Show fewer columns' : 'Show all columns'}
              </LinkButton>
            )}
            {!phone && (
              <LinkButton
                muted
                aria-pressed={compact}
                onClick={() => {
                  remember('jobpilot-density', compact ? 'comfortable' : 'compact');
                  setCompact(!compact);
                }}
              >
                {compact ? 'Comfortable rows' : 'Compact rows'}
              </LinkButton>
            )}
          </div>
        </div>
      </section>

      <Suspense fallback={null}>
        {peekId && (
          <Peek
            id={peekId}
            onClose={closePeek}
            position={peekIndex >= 0 ? `${peekIndex + 1} / ${paged.all.length}` : undefined}
            onPrev={peekIndex > 0 ? () => stepPeek(-1) : undefined}
            onNext={
              peekIndex >= 0 && peekIndex < paged.all.length - 1 ? () => stepPeek(1) : undefined
            }
          />
        )}
        {dialog === 'applied' && <LogApplied onClose={() => setDialog(null)} />}
        {dialog === 'import' && <ImportSheet onClose={() => setDialog(null)} />}
        {dialog === 'add' && <AddForJobPilot onClose={() => setDialog(null)} />}
      </Suspense>
    </>
  );
}

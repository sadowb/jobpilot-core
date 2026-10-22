import { useDeferredValue, useMemo } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../api/api';
import type { UpdateItem } from '../api/models';
import { date } from '../utils/format';
import { cn } from '../utils/cn';
import { companyName, dayLabel, eventSentence, eventTone, relativeTime } from '../utils/humanize';
import { Button, Empty, PageHeading, QueryState, ui } from '../components/ui';
import {
  FilterChips,
  matches,
  NoMatches,
  ResultCount,
  SearchField,
} from '../components/ui/Filters';
import { pick, useUrlParams } from '../hooks/useUrlParams';

const dot = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  danger: 'bg-danger',
  info: 'bg-info',
  neutral: 'bg-muted/60',
} as const;
const who = ['all', 'attention', 'agent', 'you'] as const;
type Who = (typeof who)[number];

const fromYou = (item: UpdateItem) =>
  item.actor === 'user' || item.actor === 'you' || item.actor === 'owner';
// The handful of events worth reading when agents produce hundreds: anything that stopped or went wrong.
const needsAttention = (item: UpdateItem) =>
  ['warn', 'danger'].includes(
    eventTone(item.kind, (item.payload ?? {}) as Record<string, unknown>),
  ) && !(item.kind === 'transition');

function Row({ item }: { item: UpdateItem }) {
  const payload = (item.payload ?? {}) as Record<string, unknown>;
  const tone = eventTone(item.kind, payload);
  const body = (
    <>
      <span className="min-w-0 flex-1">
        <span className="block text-[13.5px] text-ink [overflow-wrap:anywhere]">
          {eventSentence(item.kind, payload)}
        </span>
        {item.jobId && (
          <span className="mt-0.5 block truncate text-xs text-muted">
            {item.role ?? 'Untitled role'} · {companyName(item.company)}
          </span>
        )}
      </span>
      <span className="shrink-0 text-right">
        <time
          dateTime={item.ts}
          title={date(item.ts, true)}
          className="block text-xs tabular-nums text-muted"
        >
          {relativeTime(item.ts)}
        </time>
        <span className="block text-[11px] text-muted/80">
          {fromYou(item) ? 'You' : item.actor === 'system' ? 'System' : 'Agent'}
        </span>
      </span>
    </>
  );
  const cls = 'flex items-start gap-3.5 rounded-control px-3 py-3 transition-colors';
  return (
    <li className="flex">
      <span
        className={cn('mt-[1.1rem] -mr-1 ml-1 size-2 shrink-0 rounded-full', dot[tone])}
        aria-hidden="true"
      />
      {item.jobId ? (
        <Link
          to={`/jobs/${encodeURIComponent(item.jobId)}`}
          className={cn(
            cls,
            ui.focus,
            'min-w-0 flex-1 pl-4 text-ink hover:bg-raised/60 hover:no-underline',
          )}
        >
          {body}
        </Link>
      ) : (
        <div className={cn(cls, 'min-w-0 flex-1 pl-4')}>{body}</div>
      )}
    </li>
  );
}

export function Updates() {
  const url = useUrlParams();
  const filter: Who = pick(url.get('who'), who, 'all');
  const search = url.get('q');
  const query = useDeferredValue(search);
  const feed = useInfiniteQuery({
    queryKey: ['updates'],
    queryFn: ({ pageParam }) => api.updates(pageParam),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => last.next ?? undefined,
  });
  const loaded = useMemo(() => feed.data?.pages.flatMap((page) => page.items) ?? [], [feed.data]);
  // The API has no server-side search or actor filter, so both work over the pages loaded so far (see "Load older").
  const searched = useMemo(
    () =>
      loaded.filter((item) =>
        matches(
          query,
          eventSentence(item.kind, (item.payload ?? {}) as Record<string, unknown>),
          item.role,
          item.company,
        ),
      ),
    [loaded, query],
  );
  const items = useMemo(
    () =>
      searched.filter(
        (item) =>
          filter === 'all' ||
          (filter === 'attention'
            ? needsAttention(item)
            : filter === 'you'
              ? fromYou(item)
              : !fromYou(item)),
      ),
    [searched, filter],
  );
  const mine = useMemo(() => searched.filter(fromYou).length, [searched]);
  const days = useMemo(() => {
    const out: { day: string; items: UpdateItem[] }[] = [];
    for (const item of items) {
      const day = dayLabel(item.ts);
      const last = out[out.length - 1];
      if (last?.day === day) last.items.push(item);
      else out.push({ day, items: [item] });
    }
    return out;
  }, [items]);
  const filtered = !!query.trim() || filter !== 'all';
  const clear = () => url.set({ q: null, who: null });
  const options = [
    { id: 'all' as const, label: 'Everything', count: searched.length },
    {
      id: 'attention' as const,
      label: 'Needs attention',
      count: searched.filter(needsAttention).length,
    },
    { id: 'agent' as const, label: 'Agents', count: searched.length - mine },
    { id: 'you' as const, label: 'You', count: mine },
  ];

  return (
    <>
      <PageHeading title="Updates" description="What your agents and you changed, newest first." />
      <QueryState
        pending={feed.isPending}
        error={feed.error}
        retry={() => {
          void feed.refetch();
        }}
      >
        {loaded.length > 0 && (
          <div className="mb-5 flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-3">
              <SearchField
                primary
                className="max-w-md"
                label="Search updates"
                placeholder="Search activity, company or role…"
                value={search}
                onChange={(value) => url.set({ q: value })}
              />
              <FilterChips
                label="Who made the change"
                options={options}
                value={filter}
                onChange={(value) => url.set({ who: value === 'all' ? null : value })}
              />
            </div>
            <ResultCount
              shown={items.length}
              total={loaded.length}
              noun={feed.hasNextPage ? ['loaded update', 'loaded updates'] : ['update', 'updates']}
              filtered={filtered}
              onClear={clear}
            />
          </div>
        )}
        {days.length === 0 ? (
          <section className="rounded-card border border-line bg-surface shadow-card">
            {filtered && loaded.length ? (
              <NoMatches noun="loaded updates" query={query} onClear={clear} />
            ) : (
              <Empty>
                Nothing yet. Updates appear here as agents work and as you change applications.
              </Empty>
            )}
          </section>
        ) : (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
            {/* minmax(0,…): truncated rows must not widen the page */}
            {days.map((group) => (
              <section key={group.day} aria-label={group.day}>
                <h2 className="mb-2 px-1 text-xs font-medium text-muted">
                  {group.day}{' '}
                  <span className="tabular-nums text-muted/70">· {group.items.length}</span>
                </h2>
                <ol className="list-none divide-y divide-line/60 overflow-hidden rounded-card border border-line bg-surface p-1 shadow-card">
                  {group.items.map((item) => (
                    <Row key={item.id} item={item} />
                  ))}
                </ol>
              </section>
            ))}
          </div>
        )}
        {feed.hasNextPage && (
          <div className="mt-6 flex justify-center">
            <Button
              loading={feed.isFetchingNextPage}
              onClick={() => {
                void feed.fetchNextPage();
              }}
            >
              Load older updates
            </Button>
          </div>
        )}
      </QueryState>
    </>
  );
}

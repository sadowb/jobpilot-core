import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../api/api';
import type { NeedsYou as NeedsData, ReadyJob } from '../api/models';
import { blockedHelp, companyName, cvFileName, splitNeeds } from '../utils/humanize';
import { HelpList, Questions } from '../features/needs-you/Inbox';
import { ReviewLane } from '../features/approvals/ReviewLane';
import { CheckCheck } from 'lucide-react';
import { Button, Chip, ui, Empty, PageHeading, Panel, QueryState } from '../components/ui';
import {
  FilterChips,
  matches,
  NoMatches,
  ResultCount,
  SearchField,
} from '../components/ui/Filters';
import { pick, useUrlParams } from '../hooks/useUrlParams';

const kinds = ['all', 'ready', 'review', 'answers', 'help'] as const;
type Kind = (typeof kinds)[number];

export function NeedsYou() {
  const needs = useQuery({ queryKey: ['needs-you'], queryFn: api.needsYou });
  const url = useUrlParams();
  const search = url.get('q');
  const query = useDeferredValue(search);
  const show = pick(url.get('show'), kinds, 'all');
  const split = useMemo(() => splitNeeds(needs.data), [needs.data]);
  const all = useMemo(() => {
    const clean = split.ready.filter((job) => job.clean && !job.reasons.length);
    return {
      clean,
      closer: split.ready.filter((job) => !clean.includes(job)),
      answers: split.answers,
      help: split.help,
    };
  }, [split]);
  // With many agents in parallel the inbox grows fast: one search across every group, plus a group filter with counts.
  const visible = useMemo(
    () => ({
      clean: all.clean.filter((job) => matches(query, job.company, job.role)),
      closer: all.closer.filter((job) => matches(query, job.company, job.role, ...job.reasons)),
      answers: all.answers.filter((job) =>
        matches(
          query,
          job.company,
          job.role,
          job.reason,
          ...(job.missing ?? []),
          ...(job.missingFields ?? []).map((field) => field.label),
        ),
      ),
      help: all.help.filter((job) => {
        const help = blockedHelp(job.reason);
        return matches(query, job.company, job.role, job.reason, help.title, help.next);
      }),
    }),
    [all, query],
  );
  const want = (kind: Kind) => show === 'all' || show === kind;
  const clean = want('ready') ? visible.clean : [];
  const closer = want('review') ? visible.closer : [];
  const answers = want('answers') ? visible.answers : [];
  const help = want('help') ? visible.help : [];
  const ready = [...clean, ...closer];
  const total = all.clean.length + all.closer.length + all.answers.length + all.help.length;
  const shownCount = ready.length + answers.length + help.length;
  const filtered = !!query.trim() || show !== 'all';
  const clear = () => url.set({ q: null, show: null });
  const options = [
    {
      id: 'all' as const,
      label: 'All',
      count:
        visible.clean.length + visible.closer.length + visible.answers.length + visible.help.length,
    },
    { id: 'answers' as const, label: 'Questions', count: visible.answers.length },
    { id: 'ready' as const, label: 'Ready to submit', count: visible.clean.length },
    { id: 'review' as const, label: 'Closer look', count: visible.closer.length },
    { id: 'help' as const, label: 'Paused', count: visible.help.length },
  ];

  useEffect(() => {
    if (!needs.data || !window.location.hash) return;
    try {
      (
        document.getElementById(decodeURIComponent(window.location.hash.slice(1))) ??
        document.getElementById('questions')
      )?.scrollIntoView();
    } catch {
      /* Ignore an invalid fragment. */
    }
  }, [needs.data]);

  const waiting = total;
  useEffect(() => {
    const base = document.title.replace(/^\(\d+\) /, '');
    document.title = waiting ? `(${waiting}) ${base}` : base;
    return () => {
      document.title = base;
    };
  }, [waiting]);

  return (
    <>
      <PageHeading
        title="Needs you"
        description="Everything your agents are waiting on, most useful first."
      />
      <QueryState
        pending={needs.isPending}
        error={needs.error}
        retry={() => {
          void needs.refetch();
        }}
      >
        {total > 0 && (
          <div className="mb-5 flex flex-col gap-3">
            <SearchField
              primary
              className="max-w-md"
              label="Search waiting applications"
              placeholder="Search company, role or question…"
              value={search}
              onChange={(value) => url.set({ q: value })}
            />
            <FilterChips
              label="Show"
              options={options}
              value={show}
              hideEmpty
              onChange={(value) =>
                url.set({ show: value === 'all' || value === show ? null : value })
              }
            />
            <ResultCount
              shown={shownCount}
              total={total}
              noun="waiting"
              filtered={filtered}
              onClear={clear}
            />
          </div>
        )}
        {answers.length > 0 && (
          <Panel
            id="questions"
            title="Application questions"
            description="Answer each application's questions together, then send them once."
          >
            <Questions jobs={answers} />
          </Panel>
        )}
        {ready.length > 0 && (
          <section id="approvals" className="mb-6 space-y-5">
            {clean.length > 0 && (
              <div className="rounded-card border border-sage/60 bg-gradient-to-br from-sage/25 via-surface to-sage/10 dark:from-sage/15 dark:via-surface dark:to-sage/5 p-6 sm:p-7 shadow-card">
                <div className="flex flex-wrap items-center justify-between gap-5">
                  <div className="min-w-0 flex-1 basis-72">
                    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-accent">
                      Ready to submit
                    </span>
                    <div className="mt-2 flex items-baseline gap-3">
                      <span className="font-serif text-[38px] sm:text-[44px] font-semibold tracking-tight text-ink tabular-nums leading-none">
                        {clean.length}
                      </span>
                      <span className="font-serif text-[20px] sm:text-[22px] font-medium tracking-tight text-ink">
                        clean application{clean.length === 1 ? '' : 's'} ready
                      </span>
                    </div>
                    <p className="mt-2 max-w-xl text-[13.5px] leading-relaxed text-muted text-pretty">
                      Every automatic check passed. CV is “{cvFileName}”. The agent never submits:
                      you check each tab in Neo, click Submit there, then confirm here.
                    </p>
                  </div>
                </div>
                <div className="mt-5">
                  <ReviewLane jobs={clean} />
                </div>
              </div>
            )}

            {closer.length > 0 && (
              <div className="rounded-card border border-line bg-surface p-6 shadow-card">
                <p className="text-xs font-normal text-warn">Needs a closer look</p>
                <h2 className="mt-1 font-serif text-[18px] font-semibold tracking-tight text-ink">
                  {closer.length} to review one by one
                </h2>
                <ul className="mt-4 list-none divide-y divide-line/60">
                  {closer.map((job) => (
                    <li
                      key={job.id}
                      id={job.id}
                      className="flex flex-wrap items-center justify-between gap-4 py-3.5 first:pt-0 last:pb-0"
                    >
                      <div className="min-w-0 flex-1 basis-56">
                        <p className="font-semibold text-ink [overflow-wrap:anywhere]">
                          {companyName(job.company)}
                        </p>
                        <p className="text-[13px] text-muted [overflow-wrap:anywhere]">
                          {job.role}
                        </p>
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                          {job.reasons.map((reason) => (
                            <Chip key={reason} tone="warn">
                              {reason}
                            </Chip>
                          ))}
                        </div>
                      </div>
                      <Link to={`/jobs/${encodeURIComponent(job.id)}`} className={ui.buttonLink}>
                        Review
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}

        {help.length > 0 && (
          <Panel
            id="paused"
            title={`${help.length} paused`}
            description="Each one says what happened and what to do next."
          >
            <HelpList jobs={help} />
          </Panel>
        )}
        {total > 0 && !shownCount && (
          <section className={ui.card}>
            <NoMatches noun="waiting applications" query={query} onClear={clear} />
          </section>
        )}
        {!total && (
          <section className={ui.card}>
            <Empty
              action={
                <Link className={ui.buttonLink} to="/">
                  Open applications
                </Link>
              }
            >
              You’re all caught up. No applications need review or answers right now.
            </Empty>
          </section>
        )}
      </QueryState>
    </>
  );
}

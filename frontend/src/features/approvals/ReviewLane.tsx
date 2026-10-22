import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { api } from '../../api/api';
import { isReportedDraft, reportedDraftLabel } from '../../utils/direct';
import type { ReadyJob } from '../../api/models';
import { useAction } from '../../hooks/useAction';
import {
  answerValue,
  companyName,
  cvFileName,
  fieldLabel,
  isAgentAnswer,
  sourceInfo,
  trackName,
} from '../../utils/humanize';
import { cn } from '../../utils/cn';
import { Chip, ErrorMessage, IconButton, Kbd, LinkButton, Skeleton, ui } from '../../components/ui';
import { OwnerSubmitActions } from './OwnerSubmit';

const SKIP_KEY = 'jobpilot.reviewLane.skipped';
const readSkipped = (): string[] => {
  try {
    return JSON.parse(sessionStorage.getItem(SKIP_KEY) ?? '[]') as string[];
  } catch {
    return [];
  }
};

/**
 * One application at a time, keyboard first: S/→ skips, ← goes back, O opens the job. The list is live, so applications the
 * agents finish while you review simply appear at the end. The agent never submits (ADR 0026): the owner checks the tab in Neo,
 * submits there and records it here; the server stores the draft hash the owner saw.
 */
export function ReviewLane({ jobs }: { jobs: ReadyJob[] }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [skipped, setSkipped] = useState<string[]>(readSkipped);
  const queue = useMemo(() => jobs.filter((job) => !skipped.includes(job.id)), [jobs, skipped]);
  const current = queue[0];
  const next = queue[1];
  const detail = useQuery({
    queryKey: ['approval-review', current?.id],
    queryFn: () => api.job(current!.id),
    enabled: !!current,
    staleTime: 0,
    refetchOnWindowFocus: false,
  });
  const tracks = useQuery({ queryKey: ['tracks'], queryFn: api.tracks, staleTime: Infinity });
  useEffect(() => {
    if (next)
      void client.prefetchQuery({
        queryKey: ['approval-review', next.id],
        queryFn: () => api.job(next.id),
        staleTime: 30_000,
      });
  }, [client, next]);
  useEffect(() => {
    try {
      sessionStorage.setItem(SKIP_KEY, JSON.stringify(skipped));
    } catch {
      /* private mode */
    }
  }, [skipped]);

  const job = detail.data;
  const verified =
    !!job &&
    job.status === 'awaiting_approval' &&
    job.form.verified &&
    job.form.missing.length === 0;
  const busy = detail.isPending;
  const skip = () => {
    if (current && !busy) setSkipped((previous) => [...previous, current.id]);
  };
  const back = () => setSkipped((previous) => previous.slice(0, -1));

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.repeat || event.metaKey || event.ctrlKey || event.altKey) return;
      if (
        target &&
        (target.isContentEditable ||
          ['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A'].includes(target.tagName))
      )
        return;
      if (document.querySelector('dialog[open]')) return; // a modal owns the keyboard
      const key = event.key.toLowerCase();
      if (key === 's' || event.key === 'ArrowRight') {
        event.preventDefault();
        skip();
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault();
        back();
      } else if (key === 'o' && current) {
        event.preventDefault();
        navigate(`/jobs/${encodeURIComponent(current.id)}`);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  if (!current)
    return skipped.length ? (
      <div className="rounded-card border border-line bg-surface p-5 text-[13px] text-muted">
        You skipped {skipped.length} {skipped.length === 1 ? 'application' : 'applications'}.{' '}
        <LinkButton onClick={() => setSkipped([])}>Review them again</LinkButton>
      </div>
    ) : null;

  const inventory = (job?.inventory ?? []) as Record<string, unknown>[];
  // Reading order follows risk, not form order: what the agent wrote, or what was worked out from this particular posting,
  // comes first and in full. Answers copied from the profile (or derived from its sections) and saved answers are the
  // same on every application, so they stay compact below.
  const rows = (job?.plan ?? []).map((row, index) => {
    const info = sourceInfo(row.source);
    const agent = !!job && isAgentAnswer(job, row);
    const value = answerValue(row.value);
    return {
      id: `${String(row.key)}-${index}`,
      label: fieldLabel(row.key, inventory),
      value,
      info,
      agent,
      look:
        agent ||
        info.kind === 'other' ||
        (info.kind === 'derived' && /job|history/i.test(String(row.source))) ||
        value.length > 90,
    };
  });
  const look = rows.filter((row) => row.look).sort((a, b) => Number(b.agent) - Number(a.agent));
  const routine = rows.filter((row) => !row.look);
  const done = Math.max(0, jobs.length - queue.length);
  return (
    <section
      aria-label="Review queue"
      className="overflow-hidden rounded-card border border-line bg-surface shadow-card"
    >
      <div className="h-0.5 bg-line/60" aria-hidden="true">
        <div
          className="h-full bg-accent transition-[width] duration-300 motion-reduce:transition-none"
          style={{ width: `${jobs.length ? (done / jobs.length) * 100 : 0}%` }}
        />
      </div>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3.5">
        <div className="min-w-0">
          <p className="text-xs text-muted tabular-nums" aria-live="polite">
            {queue.length} left{skipped.length ? ` · ${skipped.length} skipped` : ''}
          </p>
          <Link
            to={`/jobs/${encodeURIComponent(current.id)}`}
            className="block truncate font-serif text-[18px] font-semibold tracking-tight text-ink hover:no-underline"
          >
            {companyName(current.company)}{' '}
            <span className="font-sans text-[13px] font-normal text-muted">· {current.role}</span>
          </Link>
        </div>
        <div className="flex items-center gap-1">
          <IconButton
            label="Previous skipped application"
            title="Back (←)"
            aria-keyshortcuts="ArrowLeft"
            disabled={!skipped.length}
            onClick={back}
          >
            <ChevronLeft size={18} aria-hidden="true" />
          </IconButton>
          <IconButton
            label="Skip for now"
            title="Skip (S or →)"
            aria-keyshortcuts="S ArrowRight"
            disabled={busy || queue.length < 2}
            onClick={skip}
          >
            <ChevronRight size={18} aria-hidden="true" />
          </IconButton>
        </div>
      </header>
      <div className="px-5 py-4">
        {detail.isPending ? (
          <div className="grid gap-2.5" role="status" aria-label="Loading application">
            <Skeleton className="h-3 w-2/5" />
            <Skeleton className="h-3 w-4/5" />
            <Skeleton className="h-3 w-3/5" />
          </div>
        ) : detail.error ? (
          <ErrorMessage error={detail.error} />
        ) : (
          job && (
            <>
              <p className="text-xs text-muted">
                Sent as “{cvFileName}” · {trackName(job.cv.track, tracks.data)}
                {job.cv.mode === 'tailor' ? ' · tailored' : ''} · {job.form.fields || rows.length}{' '}
                fields
              </p>
              {isReportedDraft(job) && (
                <p className="mt-2 text-xs text-muted">{reportedDraftLabel}</p>
              )}
              {!verified && (
                <p className="mt-2 text-xs text-warn">
                  Not ready: this application changed or needs a check. Open it to review.
                </p>
              )}
              {look.length > 0 && (
                <div className="mt-3.5">
                  <h3 className="text-xs font-medium text-ink">
                    Read these{' '}
                    <span className="font-normal text-muted">
                      · written or worked out for this job
                    </span>
                  </h3>
                  <dl className="mt-2 grid gap-2">
                    {look.map((row) => (
                      <div
                        key={row.id}
                        className={cn(
                          'min-w-0 rounded-control border px-3.5 py-2.5',
                          row.agent ? 'border-warn/25 bg-warn-soft' : 'border-line/70 bg-raised/40',
                        )}
                      >
                        <dt className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs text-muted">
                          <span className="min-w-0 [overflow-wrap:anywhere]">{row.label}</span>
                          <Chip tone={row.agent ? 'warn' : 'neutral'} className="shrink-0">
                            {row.agent ? 'Set by agent' : row.info.label}
                          </Chip>
                        </dt>
                        <dd className="mt-1 text-[13.5px] leading-relaxed text-ink [overflow-wrap:anywhere]">
                          {row.value}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </div>
              )}
              {routine.length > 0 && (
                <details className="group mt-4" open={look.length === 0}>
                  <summary
                    className={cn(
                      ui.focus,
                      'flex min-h-8 cursor-pointer list-none items-center gap-1.5 rounded-sm text-xs font-medium text-muted select-none hover:text-ink [&::-webkit-details-marker]:hidden',
                    )}
                  >
                    <ChevronRight
                      size={13}
                      className="transition-transform group-open:rotate-90 motion-reduce:transition-none"
                      aria-hidden="true"
                    />
                    {routine.length} from your profile and saved answers
                  </summary>
                  <dl className="mt-2 grid grid-cols-1 gap-x-8 gap-y-2.5 sm:grid-cols-2">
                    {routine.map((row) => (
                      <div key={row.id} className="min-w-0" title={row.info.detail}>
                        <dt className="truncate text-xs text-muted">{row.label}</dt>
                        <dd className="truncate text-[13px] text-ink" title={row.value}>
                          {row.value}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </details>
              )}
              {!rows.length && (
                <p className="mt-3 text-[13px] text-muted">
                  No answers were prepared for this form.
                </p>
              )}
            </>
          )
        )}
      </div>
      <footer className="grid gap-3 border-t border-line bg-raised/30 px-5 py-3">
        {verified ? (
          <OwnerSubmitActions jobId={current.id} signal={current.parkSignal ?? null} />
        ) : null}
        <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted max-[860px]:hidden">
          <Kbd>S</Kbd> skip · <Kbd>←</Kbd> back · <Kbd>O</Kbd> open
        </p>
      </footer>
    </section>
  );
}

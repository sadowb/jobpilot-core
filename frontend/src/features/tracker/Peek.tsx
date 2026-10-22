import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronUp, ExternalLink, X } from 'lucide-react';
import { api } from '../../api/api';
import { cn } from '../../utils/cn';
import { date, safeUrl } from '../../utils/format';
import { agentNote, companyName, cvFileName, trackName } from '../../utils/humanize';
import { IconButton, StatusChip, ui } from '../../components/ui';
import { RefreshDetails } from './RefreshDetails';
import { CvPreview } from '../cv/CvPreview';
import { SubmitEvidence } from '../approvals/SubmitEvidence';

type Tracking = Record<string, string | null | undefined>;

// Right-hand "peek": read a row's essentials without leaving the table. Esc closes it.
export function Peek({
  id,
  onClose,
  onPrev,
  onNext,
  position,
}: {
  id: string;
  onClose: () => void;
  onPrev?: () => void;
  onNext?: () => void;
  position?: string;
}) {
  const job = useQuery({ queryKey: ['job', id], queryFn: () => api.job(id) });
  const [cvOpen, setCvOpen] = useState(false); // the CV dialog is the only thing on screen while it is open; closing it dismisses the peek too
  useEffect(() => {
    // Click away closes. Rows are excluded (they toggle/switch the peek themselves); dialogs and toasts belong to it.
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target?.isConnected) return;
      if (
        target.closest('[aria-label="Application preview"], dialog, [data-row-id], [role="status"]')
      )
        return;
      onClose();
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [onClose]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
      )
        return;
      if (document.querySelector('dialog[open]')) return; // Esc inside a dialog opened from here closes only that dialog
      if (event.key === 'Escape') onClose();
      else if ((event.key === 'ArrowUp' || event.key === 'k') && onPrev) {
        event.preventDefault();
        onPrev();
      } else if ((event.key === 'ArrowDown' || event.key === 'j') && onNext) {
        event.preventDefault();
        onNext();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, onPrev, onNext]);
  const data = job.data;
  const info = ((data as unknown as { tracking?: Tracking })?.tracking ?? {}) as Tracking;
  const link = data ? safeUrl(data.url) : undefined;
  const tracks = useQuery({ queryKey: ['tracks'], queryFn: api.tracks, staleTime: Infinity });
  const cvUsed = data?.cv.track
    ? `${trackName(data.cv.track, tracks.data)} · ${data.cv.mode === 'tailor' ? 'tailored for this job' : 'base CV'}`
    : info.resumeLabel;
  const captured = new Set(['Location', 'Remote / on-site', 'Type', 'Level']); // read from the posting
  const facts: [string, string | null | undefined][] = [
    ['Applied', info.appliedOn ? date(info.appliedOn) : null],
    ['Location', data?.location],
    ['Remote / on-site', info.workMode],
    ['Type', info.employmentType],
    ['Level', info.jobLevel],
    ['Salary', data?.pay],
    ['Applied via', info.submittedVia ?? (info.source === 'platform' ? 'JobPilot' : null)],
    ['CV used', cvUsed],
    ['Follow up', info.followUpOn ? date(info.followUpOn) : null],
  ];
  return (
    <aside
      aria-label="Application preview"
      className={cn(
        'fixed inset-y-0 right-0 z-40 flex w-[min(26rem,100vw)] max-[560px]:w-full flex-col border-l border-line bg-surface shadow-floating transition-transform duration-200 motion-reduce:transition-none',
        cvOpen && 'translate-x-full',
      )}
    >
      <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0">
          <p className="truncate text-xs text-muted">{companyName(data?.company)}</p>
          <h2 className="mt-0.5 font-serif text-[19px] font-semibold leading-snug tracking-tight [overflow-wrap:anywhere]">
            {data?.role ?? 'Loading…'}
          </h2>
          {data && (
            <div className="mt-2">
              <StatusChip status={data.status} />
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          {(onPrev || onNext) && (
            <>
              {position && (
                <span className="px-1 text-xs tabular-nums text-muted" aria-live="polite">
                  {position}
                </span>
              )}
              <IconButton
                label="Previous application"
                title="Previous (↑ or K)"
                aria-keyshortcuts="ArrowUp K"
                disabled={!onPrev}
                onClick={onPrev}
              >
                <ChevronUp size={18} aria-hidden="true" />
              </IconButton>
              <IconButton
                label="Next application"
                title="Next (↓ or J)"
                aria-keyshortcuts="ArrowDown J"
                disabled={!onNext}
                onClick={onNext}
              >
                <ChevronDown size={18} aria-hidden="true" />
              </IconButton>
            </>
          )}
          <IconButton
            label="Close preview"
            title="Close (Esc)"
            aria-keyshortcuts="Escape"
            onClick={onClose}
          >
            <X size={18} aria-hidden="true" />
          </IconButton>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {!data ? (
          <p className="text-[13px] text-muted">
            {job.error ? 'Could not load this application.' : 'Loading…'}
          </p>
        ) : (
          <>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-4">
              {facts.map(([name, value]) => (
                <div key={name}>
                  <dt className="text-xs text-muted">{name}</dt>
                  <dd className="mt-0.5 text-[13px] text-ink">
                    {value || (
                      <span className="text-muted">
                        {captured.has(name) ? 'Not captured · use Refresh details' : '—'}
                      </span>
                    )}
                  </dd>
                </div>
              ))}
            </dl>
            <div className="mt-4 flex flex-wrap items-center gap-3 text-xs text-muted">
              <span>Sent to employers as “{cvFileName}”.</span>
              {data.cv.track && (
                <CvPreview job={id} onOpenChange={(open) => (open ? setCvOpen(true) : onClose())} />
              )}
            </div>
            {data.submitEvidence && (
              <section className="mt-6">
                {agentNote(data.blocked_reason) && (
                  <p className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-ink">
                    <span className="text-muted">The agent said: </span>
                    {agentNote(data.blocked_reason)}
                  </p>
                )}
                <SubmitEvidence jobId={id} evidence={data.submitEvidence} />
              </section>
            )}
            {info.notes && (
              <section className="mt-6">
                <h3 className="text-xs font-medium text-muted">Your notes</h3>
                <p className="mt-1 whitespace-pre-wrap text-[13px] leading-relaxed">{info.notes}</p>
              </section>
            )}
            <section className="mt-6">
              <h3 className="text-xs font-medium text-muted">Job description</h3>
              {data.posting ? (
                <p className="mt-1 line-clamp-[14] whitespace-pre-wrap text-[13px] leading-relaxed text-ink/90">
                  {data.posting}
                </p>
              ) : (
                <p className="mt-1 text-[13px] text-muted">Not captured · use Refresh details.</p>
              )}
            </section>
          </>
        )}
      </div>
      <footer className="flex flex-wrap items-center gap-2 border-t border-line px-5 py-3">
        <Link className={ui.buttonLinkPrimary} to={`/jobs/${encodeURIComponent(id)}`}>
          Open full details
        </Link>
        <RefreshDetails id={id} refresh={data?.detailsRefresh} />
        {link && !new URL(link).hostname.endsWith('.invalid') && (
          <a className={ui.buttonLink} href={link} target="_blank" rel="noopener noreferrer">
            Posting
            <ExternalLink size={13} aria-hidden="true" />
          </a>
        )}
      </footer>
    </aside>
  );
}

import type { JobDetail } from '../../api/models';
import { relativeTime } from '../../utils/humanize';

export type SubmitEvidenceData = NonNullable<JobDetail['submitEvidence']>;

/**
 * What JobPilot itself saw on the application tab right after the submit run: URL (no query), page title, the first
 * lines of visible text and a screenshot. It is captured by the server, not reported by the agent. The text is the
 * employer's page, so it is shown as plain text only and never as a link or markup.
 */
export function SubmitEvidence({
  jobId,
  evidence,
}: {
  jobId: string;
  evidence: SubmitEvidenceData;
}) {
  const screenshot = `/api/v1/jobs/${encodeURIComponent(jobId)}/submit-evidence/screenshot?v=${encodeURIComponent(evidence.capturedAt)}`;
  return (
    <section aria-label="What the page showed after the submit attempt" className="mt-4">
      <h3 className="text-xs font-medium text-muted">
        What the page showed
        {evidence.capturedAt && (
          <span className="font-normal"> · {relativeTime(evidence.capturedAt)}</span>
        )}
      </h3>
      <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-[13px]">
        {evidence.url && (
          <>
            <dt className="text-muted">Page</dt>
            <dd className="break-all text-ink">{evidence.url}</dd>
          </>
        )}
        {evidence.title && (
          <>
            <dt className="text-muted">Title</dt>
            <dd className="text-ink">{evidence.title}</dd>
          </>
        )}
      </dl>
      {evidence.excerpt ? (
        <p className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-control border border-line bg-raised/40 px-3 py-2 text-[13px] leading-relaxed text-ink/90">
          {evidence.excerpt}
        </p>
      ) : (
        <p className="mt-2 text-[13px] text-muted">The page had no visible text.</p>
      )}
      {evidence.hasScreenshot && (
        <a href={screenshot} target="_blank" rel="noopener noreferrer" className="mt-3 block">
          <img
            src={screenshot}
            alt="Screenshot of the application page right after the submit attempt"
            loading="lazy"
            className="max-h-80 w-full rounded-control border border-line object-contain object-top"
          />
        </a>
      )}
    </section>
  );
}

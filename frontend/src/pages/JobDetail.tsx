import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ExternalLink, CheckCheck, ArrowRight } from 'lucide-react';
import { RefreshDetails } from '../features/tracker/RefreshDetails';
import { api } from '../api/api';
import type { JobDetail as JobData, JobStatus, SubmitMode } from '../api/models';
import { date, finalStatuses, safeUrl, summarize } from '../utils/format';
import { useAction } from '../hooks/useAction';
import { isReportedDraft, reportedDraftLabel } from '../utils/direct';
import { OwnerSubmitActions } from '../features/approvals/OwnerSubmit';
import { SubmitEvidence } from '../features/approvals/SubmitEvidence';
import { DeleteApplication } from '../features/tracker/Library';
import { AgentGuidance } from '../features/automation/AgentGuidance';
import {
  agentNote,
  answerValue,
  blockedHelp,
  companyName,
  cvFileName,
  eventSentence,
  fieldLabel,
  isAgentAnswer,
  relativeTime,
  sourceLabel,
  trackName,
} from '../utils/humanize';
import { ConfirmDialog } from '../components/ui/Dialog';
import { CvPreview } from '../features/cv/CvPreview';
import { PreparedAnswers, inventoryLabels } from '../features/answers/PreparedAnswers';
import {
  Button,
  ui,
  Card,
  Chip,
  Empty,
  MutationFeedback,
  PageHeading,
  Panel,
  QueryState,
  StatusChip,
  ErrorMessage,
} from '../components/ui';
import { cn } from '../utils/cn';
const outcomes = [
  { value: 'screening', title: 'Screening' },
  { value: 'interview', title: 'Interview' },
  { value: 'offer', title: 'Offer' },
  { value: 'rejected', title: 'Rejected' },
  { value: 'withdrawn', title: 'Withdrawn' },
  { value: 'closed', title: 'Closed' },
] as const satisfies ReadonlyArray<{ value: JobStatus; title: string }>;

function JobOutcome({ job }: { job: JobData }) {
  const [status, setStatus] = useState<JobStatus>(
    finalStatuses.includes(job.status)
      ? outcomes.find((outcome) => outcome.value !== job.status)!.value
      : 'closed',
  );
  const [confirming, setConfirming] = useState(false);
  const action = useAction(() => api.jobStatus(job.id, { status }));
  if (
    !['submitted', 'screening', 'interview', 'offer', 'rejected', 'withdrawn'].includes(job.status)
  )
    return null;
  return (
    <Panel title="Record an outcome">
      <p className="mb-4 text-[13px] leading-relaxed text-muted">
        Record an update you have confirmed. The server checks which status changes are allowed.
      </p>
      <form
        className={ui.stack}
        onSubmit={(event) => {
          event.preventDefault();
          setConfirming(true);
        }}
      >
        <label className={ui.label}>
          Outcome
          <select
            className={ui.select}
            value={status}
            disabled={action.isPending}
            onChange={(event) => {
              setStatus(event.target.value as JobStatus);
              action.reset();
            }}
          >
            {outcomes.map((outcome) => (
              <option
                key={outcome.value}
                value={outcome.value}
                disabled={outcome.value === job.status}
              >
                {outcome.title}
              </option>
            ))}
          </select>
        </label>
        <MutationFeedback
          error={action.error}
          success={action.isSuccess}
          message="Application outcome recorded."
        />
        <div>
          <Button type="submit" disabled={action.isPending || status === job.status}>
            Record outcome
          </Button>
        </div>
      </form>
      {confirming && (
        <ConfirmDialog
          title="Record this outcome?"
          tone="primary"
          confirmLabel="Confirm outcome"
          pendingLabel="Recording…"
          pending={action.isPending}
          error={action.error}
          onClose={() => setConfirming(false)}
          onConfirm={() => action.mutate(undefined, { onSuccess: () => setConfirming(false) })}
        >
          <p>
            Mark <strong>{job.role}</strong> at <strong>{companyName(job.company)}</strong> as{' '}
            <strong>{outcomes.find((outcome) => outcome.value === status)?.title}</strong> based on
            your confirmation.
          </p>
        </ConfirmDialog>
      )}
    </Panel>
  );
}
function SubmitModeSetting({ job }: { job: JobData }) {
  const info = job.submitMode;
  const locked = finalStatuses.includes(job.status);
  const action = useAction(
    (mode: SubmitMode | null) => api.saveSubmitMode(job.id, mode),
    'Saved. It applies to the next run of this job.',
  );
  const label = (mode: string) => (mode === 'auto' ? 'auto-submit' : 'review first');
  return (
    <Panel title="Submitting">
      <p className="text-xs leading-relaxed text-muted">
        Next run: <span className="font-medium text-ink">{label(info.effective)}</span>.
        {info.reason && info.effective === 'review' && ` ${info.reason}`}
        {info.effective === 'review' &&
          info.requested === 'auto' &&
          !info.reason &&
          ' Auto-submit was requested, but this job is reviewed first.'}
      </p>
      <label className={`${ui.label} mt-3`}>
        This job
        <select
          value={info.override ?? ''}
          disabled={locked || action.isPending}
          onChange={(event) =>
            action.mutate(event.target.value === '' ? null : (event.target.value as SubmitMode))
          }
          className={ui.select}
        >
          <option value="">Use the default</option>
          <option value="review">Review first</option>
          <option value="auto">Auto-submit</option>
        </select>
      </label>
      <MutationFeedback error={action.error} success={action.isSuccess} />
    </Panel>
  );
}
function CvSettings({ job }: { job: JobData }) {
  const tracks = useQuery({ queryKey: ['tracks'], queryFn: api.tracks });
  const [track, setTrack] = useState(job.cv.track ?? '');
  const [tailor, setTailor] = useState(job.cv.mode === 'tailor');
  const action = useAction(() =>
    api.saveJobSettings(job.id, {
      ...(track ? { cvTrack: track } : {}),
      cvMode: tailor ? 'tailor' : 'base',
    }),
  );
  const locked = finalStatuses.includes(job.status);
  const cvDecision = job.events.find((event) =>
    ['cv_track_selected', 'job_settings'].includes(event.kind),
  );
  const selectionSummary =
    cvDecision?.kind === 'cv_track_selected' &&
    cvDecision.payload?.source === 'model-comparison' &&
    cvDecision.payload?.track === job.cv.track &&
    typeof cvDecision.payload?.summary === 'string'
      ? cvDecision.payload.summary
      : null;
  return (
    <Panel title="CV">
      <p className="leading-relaxed text-muted text-xs">
        {job.cv.attached ? 'CV attached to this application.' : 'CV not yet attached.'}
        {locked && ' Settings are locked after submission.'} Sent to employers as “{cvFileName}”.
      </p>
      {selectionSummary && (
        <div className="mt-3 rounded-control border border-line p-3 text-sm">
          <p className="font-medium">Why this CV</p>
          <p className="mt-1 whitespace-pre-wrap break-words text-muted">{selectionSummary}</p>
        </div>
      )}
      {job.cv.track && (
        <div className="mt-3">
          <CvPreview job={job.id} />
        </div>
      )}
      <QueryState
        pending={tracks.isPending}
        error={tracks.error}
        retry={() => {
          void tracks.refetch();
        }}
      >
        <form
          className={ui.stack}
          onSubmit={(event) => {
            event.preventDefault();
            action.mutate();
          }}
        >
          <label className={ui.label}>
            Track
            <select
              value={track}
              disabled={locked || action.isPending}
              onChange={(event) => setTrack(event.target.value)}
              className={ui.select}
            >
              <option value="">Default track</option>
              {job.cv.track && !tracks.data?.some((item) => item.id === job.cv.track) && (
                <option value={job.cv.track}>{job.cv.track}</option>
              )}
              {tracks.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </select>
          </label>
          <label className={ui.checkLabel}>
            <input
              type="checkbox"
              checked={tailor}
              disabled={locked || action.isPending}
              onChange={(event) => setTailor(event.target.checked)}
              className={ui.checkbox}
            />
            Tailor for this job
          </label>
          <MutationFeedback error={action.error} success={action.isSuccess} />
          <div>
            <Button type="submit" loading={action.isPending} disabled={locked || action.isPending}>
              {action.isPending ? 'Saving…' : 'Save CV settings'}
            </Button>
          </div>
        </form>
      </QueryState>
    </Panel>
  );
}
type Tracking = Record<string, string | null | undefined>;
const trackingFields: {
  key: string;
  field: string;
  title: string;
  kind?: 'date' | 'text' | 'area';
}[] = [
  { key: 'appliedOn', field: 'applied_on', title: 'Date applied', kind: 'date' },
  {
    key: 'dateContacted',
    field: 'date_contacted',
    title: 'Date contacted / screened',
    kind: 'date',
  },
  { key: 'interviewOn', field: 'interview_on', title: 'Date of interview', kind: 'date' },
  { key: 'followUpOn', field: 'follow_up_on', title: 'Follow up on', kind: 'date' },
  { key: 'resumeLabel', field: 'resume_label', title: 'Resume used' },
  { key: 'submittedVia', field: 'submitted_via', title: 'Applied via' },
  { key: 'jobLevel', field: 'job_level', title: 'Job level' },
  { key: 'pay', field: 'pay', title: 'Salary' },
  { key: 'location', field: 'location', title: 'Location' },
  { key: 'employmentType', field: 'employment_type', title: 'Type (full-time, internship…)' },
  { key: 'workMode', field: 'work_mode', title: 'Remote / on-site' },
  { key: 'notes', field: 'notes', title: 'Your notes', kind: 'area' },
];
function TrackingPanel({ job }: { job: JobData }) {
  const info = (job as unknown as { tracking?: Tracking }).tracking ?? {};
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(trackingFields.map((item) => [item.field, info[item.key] ?? ''])),
  );
  const save = useAction(() => {
    const body: Record<string, string> = {};
    for (const item of trackingFields) {
      if ((values[item.field] ?? '') !== (info[item.key] ?? ''))
        body[item.field] = values[item.field] ?? '';
    }
    return api.updateTracking(job.id, body);
  }, 'Details saved.');
  return (
    <Panel title="Your tracking">
      <form
        className={ui.stack}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          {trackingFields.map((item) =>
            item.kind === 'area' ? (
              <label key={item.field} className={`${ui.label} sm:col-span-2`}>
                {item.title}
                <textarea
                  rows={3}
                  value={values[item.field] ?? ''}
                  onChange={(event) => setValues({ ...values, [item.field]: event.target.value })}
                  className={ui.textarea}
                />
              </label>
            ) : (
              <label key={item.field} className={ui.label}>
                {item.title}
                <input
                  type={item.kind === 'date' ? 'date' : 'text'}
                  value={values[item.field] ?? ''}
                  onChange={(event) => setValues({ ...values, [item.field]: event.target.value })}
                  className={ui.input}
                />
              </label>
            ),
          )}
        </div>
        <MutationFeedback error={save.error} success={save.isSuccess} />
        <div>
          <Button
            type="submit"
            variant="primary"
            loading={save.isPending}
            disabled={save.isPending}
          >
            Save details
          </Button>
        </div>
      </form>
    </Panel>
  );
}
const quickSteps: { value: string; title: string }[] = [
  { value: 'screening', title: 'Got contacted' },
  { value: 'interview', title: 'Interview' },
  { value: 'offer', title: 'Offer' },
  { value: 'rejected', title: 'Rejected' },
];
function QuickStatus({ job, mine }: { job: JobData; mine: boolean }) {
  const [interviewOn, setInterviewOn] = useState('');
  const [picking, setPicking] = useState(false);
  const set = useAction(
    (next: string) =>
      mine
        ? api.updateTracking(
            job.id,
            next === 'interview' && interviewOn
              ? { status: next, interview_on: interviewOn }
              : { status: next },
          )
        : api.jobStatus(job.id, { status: next as JobStatus }),
    'Status updated.',
  );
  const done = [
    'submitted',
    'screening',
    'interview',
    'offer',
    'rejected',
    'withdrawn',
    'closed',
  ].includes(job.status);
  if (!done) return null;
  return (
    <Panel title="What happened?">
      <p className="mb-4 text-[13px] leading-relaxed text-muted">
        Update this application with one click. You can change this anytime.
      </p>
      <div className="flex flex-wrap gap-2" role="group" aria-label="Update status">
        {quickSteps.map((step) => (
          <Button
            key={step.value}
            variant={job.status === step.value ? 'primary' : 'default'}
            aria-pressed={job.status === step.value}
            disabled={set.isPending}
            onClick={() => {
              if (step.value === 'interview' && mine) setPicking(true);
              else set.mutate(step.value);
            }}
          >
            {step.title}
          </Button>
        ))}
      </div>
      {picking && (
        <form
          className="mt-4 flex flex-wrap items-end gap-3 rounded-control border border-line bg-raised/40 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            set.mutate('interview', { onSuccess: () => setPicking(false) });
          }}
        >
          <label className={ui.label}>
            Interview date
            <input
              type="date"
              autoFocus
              value={interviewOn}
              onChange={(event) => setInterviewOn(event.target.value)}
              className={ui.input}
            />
          </label>
          <div className={ui.actions}>
            <Button type="submit" variant="primary" loading={set.isPending}>
              Save interview
            </Button>
            <Button type="button" onClick={() => setPicking(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </Panel>
  );
}
const nextSteps: Record<string, string> = {
  capture: 'JobPilot is reading the posting.',
  inventory: 'Finding the application fields.',
  plan: 'Preparing your answers.',
  cv: 'Attaching your CV.',
  fill: 'Filling in the application.',
  verify: 'Running the application checks.',
  next_page: 'Moving to the next page of the form.',
  await_approval: 'Waiting for your approval.',
  submit: 'Submitting.',
  confirm: 'Checking the submission confirmation.',
  idle: 'Nothing left to do for this application.',
  blocked: 'This application is waiting for you.',
};
const nextStepText = (step: string | null | undefined) =>
  (step && nextSteps[step]) || 'Waiting for the next step.';
const PlanAnswer = ({ job, row }: { job: JobData; row: JobData['plan'][number] }) => {
  const agent = isAgentAnswer(job, row as unknown as Record<string, unknown>);
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1.5 border-b border-line/60 py-3.5 last:border-0">
      <div className="min-w-0 flex-1 basis-[220px]">
        <p className="text-xs font-medium text-muted">
          {fieldLabel(row.key, job.inventory as unknown as Record<string, unknown>[])}
        </p>
        <p className="mt-1 font-medium text-[13.5px] text-ink [overflow-wrap:anywhere]">
          {answerValue(row.value)}
        </p>
      </div>
      <Chip tone={agent ? 'warn' : 'neutral'}>{sourceLabel(row.source)}</Chip>
    </li>
  );
};
export function JobDetail() {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const inApp = ((window.history.state as { idx?: number } | null)?.idx ?? 0) > 0; // react-router's history index
  const query = useQuery({ queryKey: ['job', id], queryFn: () => api.job(id) });
  const boards = useQuery({ queryKey: ['boards'], queryFn: api.boards });
  const [confirming, setConfirming] = useState(false);
  const submitted = useAction(() => api.confirmJobSubmitted(id));
  const job = query.data;
  const board = boards.data?.find((item) => item.id === job?.board);
  const reason = job?.blocked_reason?.toLowerCase().replaceAll('_', ' ') ?? '';
  const uncertain =
    reason.includes('submission uncertain') || job?.status === 'submission_uncertain';
  const manual =
    reason.includes('apply manually') ||
    job?.status === 'apply_manually' ||
    (!!board && board.automation !== 'auto');
  const needsSubmission = !!job && !finalStatuses.includes(job.status);
  const verified = job && job.form.missing.length === 0 && job.form.verified;
  // The agent never submits (ADR 0026): a verified draft waits for the owner to submit it in Neo.
  const readyToSubmit = !!job && job.status === 'awaiting_approval' && !!verified && !uncertain;
  const external = job ? safeUrl(job.url) : undefined;
  const mine =
    !!job &&
    !!(job as unknown as { tracking?: Tracking }).tracking?.source &&
    (job as unknown as { tracking: Tracking }).tracking.source !== 'platform';
  return (
    <>
      {/* Back returns to the exact list view (search, filters and page live in the URL); a direct visit falls back to Applications. */}
      <Link
        className={`${ui.focus} mb-4 inline-flex min-h-10 items-center gap-1.5 rounded-control text-xs font-medium text-muted hover:text-ink transition-colors`}
        to="/"
        onClick={(event) => {
          if (inApp) {
            event.preventDefault();
            navigate(-1);
          }
        }}
      >
        ← {inApp ? 'Back' : 'Applications'}
      </Link>
      <QueryState
        pending={query.isPending}
        error={query.error}
        retry={() => {
          void query.refetch();
        }}
      >
        {job && (
          <>
            <PageHeading
              title={job.role}
              description={`${companyName(job.company)}${job.location ? ` · ${job.location}` : ''}${job.pay ? ` · ${job.pay}` : ''}`}
              action={
                uncertain ? (
                  <Chip tone="warn" dot>
                    Submission uncertain
                  </Chip>
                ) : manual && needsSubmission ? (
                  <Chip tone="warn" dot>
                    Apply manually
                  </Chip>
                ) : (
                  <StatusChip status={job.status} />
                )
              }
            />
            <div className="-mt-4 mb-6 flex flex-wrap items-center gap-3 text-xs text-muted">
              {board && <span>{board.displayName}</span>}
              <RefreshDetails id={job.id} refresh={job.detailsRefresh} />
              {external && (
                <a
                  href={external}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={cn(ui.link, 'font-medium')}
                >
                  View posting <ExternalLink size={12} />
                </a>
              )}
            </div>
            {!mine && (
              <Card className="flex flex-wrap items-center justify-between gap-6 border-sage/60 bg-gradient-to-br from-sage/20 via-surface to-sage/10 dark:from-sage/15 dark:via-surface dark:to-sage/5 p-6 sm:p-7 shadow-card max-[560px]:[&>button]:w-full max-[560px]:[&>a]:w-full">
                <div className="min-w-0 flex-1 basis-[240px]">
                  <h2 className="mb-1.5 flex items-center gap-2 font-serif text-[18px] font-semibold tracking-tight text-ink text-balance">
                    <CheckCheck size={18} className="shrink-0 text-accent" aria-hidden="true" />
                    {readyToSubmit
                      ? 'Ready for you to submit'
                      : uncertain
                        ? 'Submission uncertain'
                        : needsSubmission
                          ? 'Apply manually'
                          : 'Next step'}
                  </h2>
                  <p className="text-[13.5px] leading-relaxed text-muted text-pretty">
                    {readyToSubmit
                      ? `The agent filled the application and stopped before Submit. Check it in Neo, click Submit yourself, then confirm here. CV: “${cvFileName}”.`
                      : uncertain
                        ? 'Check your confirmation email or the employer’s site before applying again. If it was submitted, record your confirmation below.'
                        : needsSubmission
                          ? 'Review the posting and apply on the employer’s site, then record your submission here.'
                          : nextStepText(job.next)}
                  </p>
                  {readyToSubmit && (
                    <div className="mt-3">
                      <OwnerSubmitActions jobId={job.id} signal={job.parkSignal ?? null} />
                    </div>
                  )}
                  {job.blocked_reason &&
                    job.status !== 'awaiting_approval' &&
                    !needsSubmission &&
                    (() => {
                      const help = blockedHelp(job.blocked_reason);
                      return (
                        <p className="mt-2 text-[13px] leading-relaxed text-ink text-pretty">
                          <span className="font-medium">{help.title}.</span>{' '}
                          <span className="text-muted">{help.next}</span>
                        </p>
                      );
                    })()}
                </div>
                {!needsSubmission && job.waiting_on === 'you' && (
                  <Link className={ui.buttonLink} to={`/needs-you#${encodeURIComponent(job.id)}`}>
                    Open in Needs you
                  </Link>
                )}
              </Card>
            )}
            {needsSubmission && !readyToSubmit && (
              <Panel title={uncertain ? 'Confirm the submission' : 'Your manual application'}>
                <p className="mb-4 text-[13px] leading-relaxed text-muted">
                  {uncertain
                    ? 'Use this only after verifying that the employer received your application.'
                    : 'Submit directly on the employer’s site, then confirm it here.'}
                </p>
                {uncertain && agentNote(job.blocked_reason) && (
                  <p className="mb-4 whitespace-pre-wrap break-words text-[13px] leading-relaxed text-ink">
                    <span className="text-muted">The agent said: </span>
                    {agentNote(job.blocked_reason)}
                  </p>
                )}
                {job.submitEvidence && (
                  <div className="mb-4">
                    <SubmitEvidence jobId={job.id} evidence={job.submitEvidence} />
                  </div>
                )}
                <div className={ui.actions}>
                  {external && (
                    <a
                      href={external}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={ui.buttonLink}
                    >
                      Open employer’s site <ExternalLink size={14} aria-hidden="true" />
                    </a>
                  )}
                  <Button
                    variant="primary"
                    disabled={submitted.isPending || submitted.isSuccess}
                    onClick={() => {
                      submitted.reset();
                      setConfirming(true);
                    }}
                  >
                    I submitted it
                  </Button>
                </div>
                <MutationFeedback
                  error={submitted.error}
                  success={submitted.isSuccess}
                  message="Submission recorded."
                />
              </Panel>
            )}
            {!mine && <AgentGuidance key={job.id} job={job} />}
            <QuickStatus key={`quick:${job.id}:${job.status}`} job={job} mine={mine} />
            <TrackingPanel
              key={`${job.id}:${JSON.stringify((job as unknown as { tracking?: unknown }).tracking)}`}
              job={job}
            />
            {!mine && (
              <div className={ui.twoColumns}>
                <Panel title="Form progress">
                  {isReportedDraft(job) && (
                    <p className="mb-3 text-xs text-muted">{reportedDraftLabel}</p>
                  )}
                  <dl className={ui.progress}>
                    {[
                      ['Fields', job.form.fields],
                      ['Planned', job.form.planned],
                      ['Missing', job.form.missing.length],
                      ['Filled', job.form.filled ? 'Yes' : 'No'],
                      [
                        isReportedDraft(job) ? 'Reported complete' : 'Verified',
                        job.form.verified ? 'Yes' : 'No',
                      ],
                    ].map(([key, value]) => (
                      <div key={key}>
                        <dt>{key}</dt>
                        <dd>{value}</dd>
                      </div>
                    ))}
                  </dl>
                  {job.form.missing.length > 0 && (
                    <p className="mt-2 text-xs leading-relaxed text-muted">
                      Missing: {job.form.missing.join(', ')}
                    </p>
                  )}
                </Panel>
                <CvSettings key={`${job.id}:${job.cv.track}:${job.cv.mode}`} job={job} />
                <SubmitModeSetting key={`${job.id}:${job.submitMode.override ?? ''}`} job={job} />
              </div>
            )}
            {!mine && (
              <PreparedAnswers
                jobId={job.id}
                labels={inventoryLabels(job.inventory as unknown as Record<string, unknown>[])}
              />
            )}
            {!mine && (
              <Panel title="What will be submitted">
                <dl className="mb-4 grid gap-x-8 gap-y-3 sm:grid-cols-2 rounded-control border border-line/60 bg-raised/30 p-4">
                  <div>
                    <dt className="text-xs font-medium text-muted">CV</dt>
                    <dd className="mt-0.5 text-[13px] font-semibold text-ink">{cvFileName}</dd>
                  </div>
                  <div>
                    <dt className="text-xs font-medium text-muted">CV track</dt>
                    <dd className="mt-0.5 text-[13px] font-semibold text-ink">
                      {trackName(job.cv.track)}
                      {job.cv.mode === 'tailor' ? ' · tailored' : ''}
                    </dd>
                  </div>
                </dl>
                {job.plan.length ? (
                  <ul className="list-none" aria-label="Application answers">
                    {job.plan.map((row, index) => (
                      <PlanAnswer key={`${row.key}-${index}`} job={job} row={row} />
                    ))}
                  </ul>
                ) : (
                  <Empty>No answers have been prepared yet.</Empty>
                )}
                {job.form.missing.length > 0 && (
                  <p className="mt-3 text-xs leading-relaxed text-warn">
                    Still missing: {job.form.missing.map((key) => fieldLabel(key)).join(', ')}
                  </p>
                )}
              </Panel>
            )}
            <Panel title="Recorded usage">
              <p className="text-sm tabular-nums text-muted">
                ${job.usage.usd.toFixed(4)} · {job.usage.tokens.toLocaleString()} tokens
              </p>
            </Panel>
            <Panel title="Activity">
              {job.events.length ? (
                <ol className="list-none">
                  {job.events
                    .filter(
                      (event) =>
                        !['lease_released', 'browser_closed', 'draft_invalidated'].includes(
                          event.kind,
                        ),
                    )
                    .slice(0, 40)
                    .map((event, index) => (
                      <li
                        key={`${event.ts}-${index}`}
                        className="flex flex-wrap items-baseline justify-between gap-x-4 border-b border-line/60 py-3 last:border-0"
                      >
                        <span className="min-w-0 flex-1 basis-[240px] text-[13px] text-ink">
                          {eventSentence(
                            event.kind,
                            (event.payload ?? {}) as Record<string, unknown>,
                          )}
                        </span>
                        <time
                          dateTime={event.ts}
                          title={date(event.ts, true)}
                          className="text-xs tabular-nums text-muted"
                        >
                          {relativeTime(event.ts)}
                        </time>
                      </li>
                    ))}
                </ol>
              ) : (
                <Empty>No activity yet.</Empty>
              )}
              {job.events.length > 0 && (
                <details className="mt-4 text-xs">
                  <summary className="cursor-pointer font-medium text-muted hover:text-ink transition-colors">
                    Technical details
                  </summary>
                  <ol className="mt-3 list-none space-y-2">
                    {job.events.map((event, index) => (
                      <li
                        key={`raw-${event.ts}-${index}`}
                        className="rounded-control border border-line bg-raised/40 p-3"
                      >
                        <div className={ui.row}>
                          <strong className="font-semibold text-ink">{event.kind}</strong>
                          <span className="text-muted">
                            {event.actor} · {date(event.ts, true)}
                          </span>
                        </div>
                        <pre className="mt-2 font-mono whitespace-pre-wrap text-[11px] leading-relaxed text-muted [overflow-wrap:anywhere]">
                          {summarize(event.payload)}
                        </pre>
                      </li>
                    ))}
                  </ol>
                </details>
              )}
            </Panel>
            {(job as unknown as { tracking?: Tracking }).tracking?.companyDescription && (
              <Panel title="About the company">
                <div className="whitespace-pre-wrap text-[13.5px] leading-relaxed text-ink [overflow-wrap:anywhere]">
                  {(job as unknown as { tracking: Tracking }).tracking.companyDescription}
                </div>
              </Panel>
            )}
            <Panel title="Manage">
              <p className="mb-3 text-[13px] text-muted">
                Delete this application if it was added by mistake. You can restore it later from
                the Deleted view.
              </p>
              <DeleteApplication id={job.id} title={job.role} onDone={() => navigate('/')} />
            </Panel>
            <Panel title="Job description">
              {job.posting ? (
                <div className="whitespace-pre-wrap text-[13.5px] leading-relaxed text-ink [overflow-wrap:anywhere]">
                  {job.posting}
                </div>
              ) : (
                <Empty>No posting text available.</Empty>
              )}
            </Panel>
            {confirming && (
              <ConfirmDialog
                title="Record your submission"
                tone="primary"
                confirmLabel="Confirm submission"
                pendingLabel="Recording…"
                pending={submitted.isPending}
                error={submitted.error}
                onClose={() => setConfirming(false)}
                onConfirm={() =>
                  submitted.mutate(undefined, { onSuccess: () => setConfirming(false) })
                }
              >
                <p>
                  Confirm that you submitted the application for <strong>{job.role}</strong> at{' '}
                  <strong>{companyName(job.company)}</strong>.
                </p>
              </ConfirmDialog>
            )}
          </>
        )}
      </QueryState>
    </>
  );
}

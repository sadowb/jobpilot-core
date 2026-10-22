import { useMemo, useState, type ChangeEvent } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUpRight } from 'lucide-react';
import { api } from '../../api/api';
import type { NeedsYou } from '../../api/models';
import { useAction } from '../../hooks/useAction';
import {
  blockedHelp,
  companyName,
  fieldLabel,
  helpGroupTitle,
  helpOrder,
  type Help,
  type HelpKind,
  focusMessage,
} from '../../utils/humanize';
import { Button, ErrorMessage, LinkButton, ui } from '../../components/ui';
import { cn } from '../../utils/cn';
import { SubmitEvidence } from '../approvals/SubmitEvidence';

type Blocked = NeedsYou['blocked'][number];
type Field = Blocked['missingFields'][number];
const jobLink = (id: string) => `/jobs/${encodeURIComponent(id)}`;
const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;

/** Small inline control that tells the owner which Neo page holds the agent's live tab (changes nothing). */
function ShowTab({ jobId }: { jobId: string }) {
  const focus = useAction(() => api.focusJobTab(jobId), focusMessage);
  return (
    <LinkButton onClick={() => focus.mutate()} loading={focus.isPending} className="ml-2">
      Find agent tab
    </LinkButton>
  );
}

const inputKinds = {
  text: 'Text',
  search: 'Text',
  email: 'Email address',
  tel: 'Phone number',
  url: 'Web address',
  number: 'Number',
  date: 'Date',
  'datetime-local': 'Date and time',
  month: 'Month',
  week: 'Week',
  time: 'Time',
} as const;

function inputKind(type: string | undefined): keyof typeof inputKinds | undefined {
  return type && Object.hasOwn(inputKinds, type) ? (type as keyof typeof inputKinds) : undefined;
}

/** Presentation names only: never infer an answer or employer choices from the label. */
function fieldKind(field: Field | undefined): string {
  const type = field?.type;
  const input = inputKind(type);
  if (field?.options.length) {
    return field.multiple
      ? 'Choice (multiple allowed on the form)'
      : field.options.length === 2 && field.options.includes('Yes') && field.options.includes('No')
        ? 'Yes/no'
        : 'Choice';
  }
  if (input) return inputKinds[input];
  switch (type) {
    case 'textarea':
      return 'Long text';
    case 'select':
    case 'custom-select':
    case 'combobox':
    case 'radio':
      return 'Choice';
    case 'checkbox':
    case 'custom-checkbox':
    case 'boolean':
      return 'Yes/no';
    case 'file':
      return 'File';
    default:
      return type ? `Field type: ${type}` : 'Field kind not provided';
  }
}

/** What changed since the owner last answered: answers already given are acknowledged, only the rest is asked. */
function newVsAnswered(job: Blocked, newCount: number): string {
  const parts: string[] = [];
  if ((job.answeredCount ?? 0) > 0) parts.push(`You answered ${job.answeredCount} ✓`);
  if ((job.settledCount ?? 0) > 0)
    parts.push(
      `${plural(job.settledCount, 'question')} already had your saved answer and went back to the agent`,
    );
  parts.push(`${plural(newCount, 'new question')}${job.step ? ` from ${job.step}` : ''}`);
  return parts.join(' · ');
}

/** Small secondary actions on every Needs You card: the owner did the work, or no longer wants this one. */
function CardActions({ job }: { job: Blocked }) {
  const [confirmSubmitted, setConfirmSubmitted] = useState(false);
  const completed = useAction(
    () => api.ownerCompleted(job.id),
    'Continuing. The agent will check the tab and move on.',
  );
  const submitted = useAction(
    () => api.confirmJobSubmitted(job.id),
    'Recorded as submitted. Its tab will close.',
  );
  const skip = useAction(
    () => api.archiveJob(job.id),
    'Removed. You can restore it from the tracker.',
  );
  const pending = completed.isPending || submitted.isPending || skip.isPending;
  return (
    <div className="space-y-2">
      <div className={ui.actions}>
        <Button
          type="button"
          loading={completed.isPending}
          disabled={pending}
          onClick={() => completed.mutate()}
        >
          I filled it in the tab myself — continue
        </Button>
        {confirmSubmitted ? (
          <Button
            type="button"
            loading={submitted.isPending}
            disabled={pending}
            onClick={() => submitted.mutate()}
          >
            Yes, I submitted it
          </Button>
        ) : (
          <Button type="button" disabled={pending} onClick={() => setConfirmSubmitted(true)}>
            I already submitted this
          </Button>
        )}
        <Button
          type="button"
          loading={skip.isPending}
          disabled={pending}
          onClick={() => skip.mutate()}
        >
          Skip / not interested
        </Button>
      </div>
      <ErrorMessage error={completed.error ?? submitted.error ?? skip.error} />
    </div>
  );
}

function QuestionBatch({ job }: { job: Blocked }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [skipped, setSkipped] = useState<Record<string, boolean>>({});
  // "Save for all jobs" defaults on for questions the server recognises as job-independent (start date, salary, …).
  const [saveAll, setSaveAll] = useState<Record<string, boolean>>({});
  const fields = (job.missing ?? []).map(
    (key) =>
      job.missingFields.find((f) => f.key === key) ??
      ({
        key,
        label: fieldLabel(key),
        type: 'text',
        required: true,
        options: [],
        generic: false,
      } as Field),
  );
  const answers = () =>
    fields
      .filter((f) => values[f.key]?.trim() || skipped[f.key])
      .map((f) => ({
        question: f.key,
        answer: values[f.key]?.trim() ?? '',
        scope: (saveAll[f.key] ?? f.generic) && !skipped[f.key] ? 'global' : `job:${job.id}`,
        skip: !!skipped[f.key],
      }));
  const save = useAction(async () => {
    const batch = answers();
    if (batch.length) await api.saveJobAnswers(job.id, { answers: batch, resume: true });
    else await api.resumeJob(job.id);
  }, 'Answers sent. The job continues when all required answers are available.');
  const tryAnyway = useAction(async () => {
    const batch = answers();
    if (batch.length) await api.saveJobAnswers(job.id, { answers: batch, resume: false });
    await api.resumeJob(job.id, true);
  }, 'The agent will try the remaining questions.');
  const pending = save.isPending || tryAnyway.isPending;
  return (
    <form
      id={job.id}
      className="space-y-4 rounded-card border border-line bg-surface p-5"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <Link to={jobLink(job.id)} className={cn(ui.link, 'font-medium text-ink')}>
        {job.role} · {companyName(job.company)}
      </Link>
      {job.tabOpen && <ShowTab jobId={job.id} />}
      <p className="text-sm font-medium text-ink">{newVsAnswered(job, fields.length)}</p>
      <p className="text-sm text-muted">
        Only required unanswered questions need your input. Unknown optional fields stay empty.
      </p>
      {fields.map((field, index) => {
        const id = `question-${job.id}-${index}`;
        const props = {
          id,
          value: values[field.key] ?? '',
          disabled: pending || !!skipped[field.key],
          onChange: (
            event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>,
          ) => setValues((v) => ({ ...v, [field.key]: event.target.value })),
        };
        return (
          <div key={field.key} className="space-y-2">
            <label htmlFor={id} className="block text-sm font-medium">
              {field.label}
            </label>
            <p className="text-xs text-muted">
              {fieldKind(field)} · {field.required ? 'Required' : 'Requirement not reported'}
            </p>
            {field.unmatchedValue && (
              <p className="text-sm text-muted">
                “{field.unmatchedValue}” is not in this form's list. Choose a replacement for this
                application only.
              </p>
            )}
            {field.type === 'file' || field.type === 'secret' || field.type === 'password' ? (
              <p className="text-sm text-muted">
                Answer controls are unavailable for this field.{' '}
                <Link to={jobLink(job.id)} className={cn(ui.link, 'underline')}>
                  Open job
                </Link>
                {field.type !== 'file' &&
                  ' and complete sign-in in Neo. Do not save passwords or verification codes here.'}
              </p>
            ) : field.options.length ? (
              <select {...props} className={ui.select}>
                <option value="">Choose…</option>
                {field.options.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
            ) : field.type === 'textarea' ? (
              <textarea {...props} rows={3} className={ui.textarea} />
            ) : (
              <input
                {...props}
                type={inputKind(field.type) ?? 'text'}
                autoComplete="off"
                className={ui.input}
              />
            )}
            {!field.options.length &&
              ['custom-select', 'combobox', 'select', 'radio'].includes(field.type ?? '') && (
                <p className="text-xs text-muted">
                  Enter your answer. The agent will ask again, showing the form's suggestions, if
                  none matches.
                </p>
              )}
            {(field.optionsTotal ?? 0) > field.options.length && (
              <p className="text-xs text-muted">
                Showing {field.options.length} of {field.optionsTotal} choices reported by the form.
              </p>
            )}
            <label className="flex items-center gap-2 text-xs text-muted">
              <input
                type="checkbox"
                checked={!!skipped[field.key]}
                disabled={pending}
                onChange={(e) => setSkipped((v) => ({ ...v, [field.key]: e.target.checked }))}
              />
              Skip this question; leave it empty
            </label>
            {field.type !== 'file' && field.type !== 'secret' && field.type !== 'password' && (
              <label className="flex items-center gap-2 text-xs text-muted">
                <input
                  type="checkbox"
                  checked={(saveAll[field.key] ?? field.generic) && !skipped[field.key]}
                  disabled={pending || !!skipped[field.key]}
                  onChange={(e) => setSaveAll((v) => ({ ...v, [field.key]: e.target.checked }))}
                />
                Save for all jobs
              </label>
            )}
          </div>
        );
      })}
      <div className={ui.actions}>
        <Button type="submit" variant="primary" loading={save.isPending} disabled={pending}>
          Send answers
        </Button>
        <Button
          type="button"
          loading={tryAnyway.isPending}
          disabled={pending}
          onClick={() => tryAnyway.mutate()}
        >
          Let the agent try anyway
        </Button>
      </div>
      <ErrorMessage error={save.error ?? tryAnyway.error} />
      <CardActions job={job} />
    </form>
  );
}

export function Questions({ jobs }: { jobs: Blocked[] }) {
  return (
    <div className="space-y-4">
      {jobs.map((job) => (
        <QuestionBatch key={job.id} job={job} />
      ))}
    </div>
  );
}

function HelpRow({ job, help: raw }: { job: Blocked; help: Help }) {
  // Saved answers wait for the owner to send them; whatever the agent last reported, Send is the next step.
  const unsent = (job.answersPending ?? 0) > 0;
  const help: Help = unsent
    ? {
        kind: 'review',
        title: 'Your answers are saved and not sent yet',
        next: `${plural(job.answersPending ?? 0, 'answer')} saved. Send them to the agent to continue.`,
        detail: raw.detail,
        action: 'resume',
      }
    : raw;
  const resume = useAction(() => api.resumeJob(job.id), 'Resumed. An agent will pick it up.');
  const acknowledge = useAction(
    () => api.acknowledgeUnapprovedSubmit(job.id),
    'Marked as checked. Automation stays stopped until you start it.',
  );
  const focus = useAction(() => api.focusJobTab(job.id), focusMessage);
  const emailCode = useAction(
    () => api.requestEmailCode(job.id),
    'The agent is reading the code from Gmail again.',
  );
  const link = jobLink(job.id);
  const linkTitle = help.action === 'apply' ? 'Open to apply' : 'Open job';
  return (
    <li
      id={job.id}
      className="flex scroll-mt-20 flex-wrap items-start justify-between gap-x-6 gap-y-3 py-4 first:pt-0 last:pb-0"
    >
      <div className="min-w-0 flex-1 basis-72">
        <p className="text-[13.5px] font-medium text-ink text-pretty">{help.title}</p>
        <p className="mt-0.5 text-[13px] leading-relaxed text-muted text-pretty">{help.next}</p>
        {help.detail && (
          <p className="mt-2 whitespace-pre-wrap break-words text-[13px] leading-relaxed text-ink text-pretty">
            <span className="text-muted">The agent said: </span>
            {help.detail}
          </p>
        )}
        {help.kind === 'check' && job.submitEvidence && (
          <SubmitEvidence jobId={job.id} evidence={job.submitEvidence} />
        )}
        <Link
          to={jobLink(job.id)}
          className={cn(ui.linkMuted, 'mt-1.5 max-w-full hover:text-accent')}
        >
          <span className="truncate">
            <span className="font-medium text-ink">{companyName(job.company)}</span> · {job.role}
          </span>
          <ArrowUpRight size={12} className="shrink-0" aria-hidden="true" />
        </Link>
        <ErrorMessage error={resume.error} />
        <ErrorMessage error={acknowledge.error} />
        <ErrorMessage error={focus.error} />
        <ErrorMessage error={emailCode.error} />
        <div className="mt-3">
          <CardActions job={job} />
        </div>
      </div>
      <div className={`${ui.actions} max-[560px]:w-full max-[560px]:[&>*]:flex-1`}>
        {help.action === 'resume' && (
          <Button variant="primary" loading={resume.isPending} onClick={() => resume.mutate()}>
            {unsent ? 'Send to agent' : 'Resume'}
          </Button>
        )}
        {help.action === 'acknowledge' && (
          <Button
            variant="primary"
            loading={acknowledge.isPending}
            onClick={() => acknowledge.mutate()}
          >
            I checked it
          </Button>
        )}
        {job.status === 'awaiting_approval' && (
          <Button loading={emailCode.isPending} onClick={() => emailCode.mutate()}>
            Get the email code from Gmail
          </Button>
        )}
        {job.tabOpen && (
          <Button variant="primary" loading={focus.isPending} onClick={() => focus.mutate()}>
            Find agent tab
          </Button>
        )}
        <Link className={ui.buttonLink} to={link}>
          {linkTitle}
        </Link>
      </div>
    </li>
  );
}

/** Paused applications grouped by what the owner has to do, most urgent kind first, each with its next step up front. */
export function HelpList({ jobs }: { jobs: Blocked[] }) {
  const groups = useMemo(() => {
    const byKind = new Map<HelpKind, { job: Blocked; help: Help }[]>();
    for (const job of jobs) {
      const help = blockedHelp(job.reason);
      byKind.set(help.kind, [...(byKind.get(help.kind) ?? []), { job, help }]);
    }
    return helpOrder
      .filter((kind) => byKind.has(kind))
      .map((kind) => ({ kind, items: byKind.get(kind)! }));
  }, [jobs]);
  return (
    <div className="flex flex-col gap-6">
      {groups.map((group) => (
        <section key={group.kind} aria-label={helpGroupTitle(group.kind)}>
          <h3 className="mb-3 flex items-center gap-2 text-xs font-medium text-muted">
            {helpGroupTitle(group.kind)}
            <span className="tabular-nums text-muted/70">{group.items.length}</span>
          </h3>
          <ul className="list-none divide-y divide-line/60">
            {group.items.map(({ job, help }) => (
              <HelpRow key={job.id} job={job} help={help} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

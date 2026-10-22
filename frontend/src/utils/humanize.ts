import { ApiError } from '../api/http';
import type { JobDetail, NeedsYou, Track } from '../api/models';

export const cvFileName = 'Abdelhadi Marjane.pdf';
type RecordData = Record<string, unknown>;
const record = (value: unknown): RecordData =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as RecordData) : {};

export function fieldLabel(key: unknown, inventory: RecordData[] = []): string {
  const raw = typeof key === 'string' ? key : '';
  const clean = (value: string) =>
    value
      .replace(/^page:\d+:/, '')
      .replace(/^radio:/, '')
      .replace(/^#/, '');
  const field = inventory.find((item) =>
    [item.key, item.selector, item.id, item.name].some(
      (value) => typeof value === 'string' && (value === raw || clean(value) === clean(raw)),
    ),
  );
  if (typeof field?.label === 'string' && field.label.trim()) return field.label.trim();
  const words = clean(raw)
    .replace(/([a-z\d])([A-Z])/g, '$1 $2')
    .replace(/[_:#.\-[\]]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return words ? words[0].toUpperCase() + words.slice(1).toLowerCase() : 'Application answer';
}
export type SourceKind = 'agent' | 'derived' | 'answer' | 'profile' | 'missing' | 'other';
/** Where a prepared answer came from. `kind` drives how much attention the review screens give it. */
export function sourceInfo(source: unknown): { kind: SourceKind; label: string; detail: string } {
  const raw = String(source ?? '').trim();
  const value = raw.toLowerCase();
  const tail = raw.includes(':') ? raw.slice(raw.indexOf(':') + 1) : '';
  if (value.startsWith('agent'))
    return {
      kind: 'agent',
      label: 'Set by agent',
      detail: 'The agent wrote this itself. Read it before approving.',
    };
  if (value.startsWith('derived'))
    return {
      kind: 'derived',
      label: 'Worked out',
      detail: tail
        ? `Worked out from your ${fieldLabel(tail).toLowerCase()}`
        : 'Worked out from your profile',
    };
  if (value.startsWith('profile'))
    return {
      kind: 'profile',
      label: 'From your profile',
      detail: tail ? `Your profile: ${fieldLabel(tail).toLowerCase()}` : 'From your profile',
    };
  if (/missing|unresolved|unknown/.test(value) || !value)
    return { kind: 'missing', label: 'Needs your answer', detail: 'No answer yet' };
  if (/answer|saved|user|you/.test(value)) {
    const scope = tail.startsWith('job')
      ? 'for this job only'
      : tail.startsWith('company')
        ? 'for this company'
        : 'for every application';
    return { kind: 'answer', label: 'Saved answer', detail: `An answer you saved ${scope}` };
  }
  return { kind: 'other', label: 'Prepared answer', detail: 'Prepared by JobPilot' };
}
export function sourceLabel(source: unknown): string {
  return sourceInfo(source).label;
}
export function agentEdits(job: JobDetail): unknown[] {
  const edits: unknown = record(job).agentEdits;
  return Array.isArray(edits) ? edits : [];
}
export function isAgentAnswer(job: JobDetail, row: RecordData): boolean {
  return (
    sourceLabel(row.source) === 'Set by agent' ||
    agentEdits(job).some((edit) => {
      const item = record(edit);
      return [item.key, item.field, item.fieldKey, item.questionKey].some(
        (key) => key !== undefined && key === row.key,
      );
    })
  );
}
export function hasAgentAnswers(job: JobDetail): boolean {
  return (
    agentEdits(job).length > 0 || job.plan.some((row) => sourceLabel(row.source) === 'Set by agent')
  );
}
export function answerValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Not provided';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) return value.map(answerValue).join(', ');
  if (typeof value === 'object')
    return Object.entries(record(value))
      .map(([key, item]) => `${fieldLabel(key)}: ${answerValue(item)}`)
      .join(' · ');
  return String(value);
}
export function companyName(company: string | null | undefined): string {
  const value = company?.trim();
  if (!value || /^(unknown|n\/a|null|none|localhost|company not provided)$/i.test(value))
    return 'Unknown company';
  const hostname = value
    .replace(/^https?:\/\//, '')
    .split('/')[0]
    .replace(/:\d+$/, '');
  return /^(\d{1,3}\.){3}\d{1,3}$/.test(hostname) || hostname.includes(':')
    ? 'Unknown company'
    : value;
}
// The same names the edit form uses, so the activity feed and the form agree.
const trackedFieldTitles: Record<string, string> = {
  applied_on: 'Applied date',
  date_contacted: 'Date contacted',
  interview_on: 'Interview date',
  follow_up_on: 'Follow-up date',
  resume_label: 'Resume used',
  submitted_via: 'Applied via',
  job_level: 'Job level',
  employment_type: 'Job type',
  work_mode: 'Work mode',
  user_status: 'Status note',
  notes: 'Notes',
  role: 'Job title',
  location: 'Location',
  pay: 'Salary',
};
export function trackName(track: string | null, tracks?: Track[]): string {
  return (
    tracks?.find((item) => item.id === track)?.title ??
    (track ? fieldLabel(track) : 'Default track')
  );
}
export type HelpAction = 'resume' | 'apply' | 'answer' | 'open' | 'acknowledge';
export type HelpKind = 'verify' | 'apply' | 'check' | 'review' | 'retry' | 'limit';
export type Help = {
  kind: HelpKind;
  title: string;
  next: string;
  action: HelpAction;
  /** The agent's own words about what it saw, when it gave any. Untrusted page-derived text: show as plain text. */
  detail?: string;
};
/** The agent's explanation after a submit run that did not confirm; the server appends it to the wait reason. */
export function agentNote(reason: string | null | undefined): string | null {
  const match = /Agent note:\s*([\s\S]+)$/.exec(String(reason ?? ''));
  return match ? match[1].trim() || null : null;
}
const helpGroups: Record<HelpKind, string> = {
  verify: 'Finish a check on the site',
  apply: 'Apply yourself',
  check: 'Confirm what happened',
  review: 'Review before it continues',
  retry: 'The agent got stuck',
  limit: 'Waiting on a limit',
};
export const helpGroupTitle = (kind: HelpKind) => helpGroups[kind];
export const helpOrder: HelpKind[] = ['check', 'verify', 'review', 'apply', 'retry', 'limit'];

/**
 * Turns the backend's blocked_reason (a short machine phrase) into what happened, what to do next, and which button to
 * offer. Unknown reasons fall back to a generic "open it" so nothing is ever hidden or dead-ended.
 */
export function blockedHelp(reason: string | null | undefined): Help {
  const raw = String(reason ?? '');
  const value = raw.toLowerCase().replaceAll('_', ' ');
  if (/choose a cv for this job/.test(value))
    return {
      kind: 'retry',
      title: 'The agent could not pick a CV for this job',
      next: 'Resume makes it read the posting again and choose from your CVs. Or pick one yourself on the job page.',
      detail: raw.replace(/^choose a cv for this job:\s*/i, ''),
      action: 'resume',
    };
  // Server phrase (service/approvals.py): "SUBMITTED AS REPORTED WITHOUT APPROVAL: check this application".
  if (/submitted (as reported )?without approval/.test(value))
    return {
      kind: 'check',
      title: 'This may have been sent without your approval',
      next: 'Automation stopped itself and stays stopped. Check the employer’s site, then press “I checked it”; restart automation yourself when you are ready.',
      action: 'acknowledge',
    };
  if (/submission uncertain/.test(value))
    return {
      kind: 'check',
      title: 'Not sure the employer received it',
      next: 'Check what the page showed below, or look for a confirmation email. If it went through, record it on the job page. Do not apply twice.',
      action: 'open',
      detail: agentNote(raw) ?? undefined,
    };
  if (/captcha/.test(value))
    return {
      kind: 'verify',
      title: 'The site is asking for a CAPTCHA',
      next: 'Solve it in the browser window, then press Resume.',
      action: 'resume',
    };
  if (/paste the code yourself/.test(value))
    return {
      kind: 'verify',
      title: 'Enter the emailed code yourself',
      next: 'JobPilot could not finish the code step from Gmail. Type the code in the application tab, then press “I submitted it”, or try Gmail again.',
      detail: agentNote(raw) ?? undefined,
      action: 'open',
    };
  if (/mfa|email code|verification code|two.factor|verification link/.test(value))
    return {
      kind: 'verify',
      title: 'The site wants a sign-in code',
      next: 'The agent could not complete verification. Check the application in Neo, then resume; do not paste codes here.',
      action: 'resume',
    };
  if (/approve account creation|login wall|sign.in/.test(value))
    return {
      kind: 'verify',
      title: 'Sign in through BrowserOS Neo',
      next: 'Use an existing session or SSO in Neo, then resume. JobPilot does not store or type passwords.',
      action: 'resume',
    };
  if (/duplicate/.test(value))
    return {
      kind: 'review',
      title: 'You may have applied to this already',
      next: 'Compare the two applications. Delete this one if it is the same job, or resume it if it is different.',
      action: 'open',
    };
  if (/form changed since approval|re.approval|could not restore approved/.test(value))
    return {
      kind: 'review',
      title: 'The form changed after you approved',
      next: 'Nothing was sent. Review the answers again and approve the new version.',
      action: 'open',
    };
  const answersMissing = /missing answers?:?\s*(\d+)?/.exec(value);
  if (answersMissing) {
    const count = Number(answersMissing[1] ?? 0);
    return {
      kind: 'review',
      title:
        count === 1
          ? 'Waiting for one answer from you'
          : `Waiting for ${count || 'some'} answers from you`,
      next: 'Answer in Needs you and this application continues by itself.',
      action: 'answer',
    };
  }
  if (/couldn.t find|missing|unanswered/.test(value))
    return {
      kind: 'review',
      title: 'Some details are missing',
      next:
        raw
          .replace(/^couldn.t find:\s*/i, 'Missing: ')
          .replace(/\s*add them on the job page\.?$/i, '') ||
        'Add the missing details on the job page.',
      action: 'open',
    };
  if (/apply manually|needs you to press submit|cannot be safely automated/.test(value))
    return {
      kind: 'apply',
      title: 'This site needs you to apply yourself',
      next: 'Open the posting, apply on the employer’s site, then mark it as submitted on the job page.',
      action: 'apply',
    };
  if (/outside the job.s allowed host|outside the job.s site/.test(value))
    return {
      kind: 'retry',
      title: 'The site sent the agent to a different website',
      next: 'The agent stopped to stay safe. Open the posting to see where it leads; apply yourself if it moved to another site.',
      action: 'open',
    };
  if (
    /retry limit|no progress|stuck|action cap|could not inspect|timeout|took too long/.test(value)
  )
    return {
      kind: 'retry',
      title: 'The agent could not get past a step',
      next: `It tried${/no progress at (\w+)/.test(value) ? ` “${stepName(value.match(/no progress at (\w+)/)![1])}”` : ''} more than once without success. Resume to let it try again, or apply yourself.`,
      action: 'resume',
    };
  if (/automation error|inventory and plan required|eval failed|error/.test(value))
    return {
      kind: 'retry',
      title: 'Something went wrong while preparing this',
      next: 'Resume to let the agent start this step again. If it stops here twice, apply yourself.',
      action: 'resume',
    };
  return {
    kind: 'review',
    title: 'This application needs a look',
    next: raw
      ? `${raw} Resume to let the agent continue, or open it to look first.`
      : 'Open it to see what happened.',
    action: 'resume',
  };
}
const stepNames: Record<string, string> = {
  capture: 'reading the posting',
  inventory: 'finding the form fields',
  plan: 'preparing answers',
  cv: 'attaching your CV',
  fill: 'filling the form',
  verify: 'checking the form',
  next_page: 'moving to the next page',
  await_approval: 'waiting for your approval',
  submit: 'submitting',
  confirm: 'confirming',
};
// The live agent list reports the kind of the job's latest event; each one means the agent is now on the step after it.
const stepAfterEvent: Record<string, string> = {
  ingest: 'capture',
  browser_open: 'capture',
  applied_logged: 'capture',
  meta: 'inventory',
  inventory: 'plan',
  plan: 'cv',
  auto_answered: 'cv',
  cv: 'fill',
  cv_check: 'fill',
  cv_track_selected: 'fill',
  fill: 'verify',
  pages_complete: 'verify',
  check: 'verify',
  verification: 'verify',
  approval: 'submit',
  submit_clicked: 'confirm',
};
const asStep = (value: string | null | undefined) =>
  (value && (stepNames[value] ? value : stepAfterEvent[value])) || null;
export const stepName = (step: string | null | undefined) => {
  const known = asStep(step);
  return known ? stepNames[known] : 'working';
};
/** The pipeline an agent walks for one application, in order, for progress indicators. */
export const agentSteps = ['capture', 'inventory', 'plan', 'cv', 'fill', 'verify'] as const;
export function stepIndex(step: string | null | undefined): number {
  const known = asStep(step);
  const index = (agentSteps as readonly string[]).indexOf(
    known === 'next_page' ? 'fill' : (known ?? ''),
  );
  return index < 0
    ? known === 'await_approval' || known === 'submit' || known === 'confirm'
      ? agentSteps.length
      : 0
    : index;
}

export function friendlyError(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error ?? '');
  if (
    /ERR_UNSAFE_PORT|ERR_CONNECTION|ERR_NAME_NOT_RESOLVED|ERR_ADDRESS|browser open fails/i.test(raw)
  )
    return 'This link could not be reached. Check that the address is correct and the site is online.';
  // Conflict messages are written for the owner by the server (e.g. "no longer waiting for answers"): show them as is.
  if (error instanceof ApiError && error.status === 409 && raw.trim() && raw.length <= 240)
    return raw;
  const value = raw.toLowerCase().replaceAll('_', ' ');
  if (/illegal transition|transition refused/.test(value))
    return 'JobPilot could not move this application to the next step. Open the job to review it.';
  if (/stopped|drain|active lease/.test(value))
    return 'Stop automation and wait for active jobs to finish, then try again.';
  if (/401|session|csrf/.test(value))
    return 'Your session needs refreshing. Reload JobPilot and try again.';
  if (/failed to fetch|networkerror|network request|could not connect/.test(value))
    return 'Could not connect to JobPilot. Check that it is running and try again.';
  if (/not verified|checks/.test(value) && !/code|link/.test(value))
    return 'The application checks need to finish successfully before approval.';
  if (/is not one of the choices|not a country/.test(value)) return raw;
  // Everything a job can be blocked on has one wording, shared with the Needs you inbox.
  const help = blockedHelp(raw);
  if (help.title !== 'This application needs a look') return `${help.title}.`;
  return 'This action could not be completed. Review the details and try again.';
}
export function idleSentence(reason: string | null | undefined): string {
  if (!reason) return 'Eligible applications will be picked up automatically.';
  // Server-written, already owner-facing: names why runs keep being released for retry.
  if (/^runs are being retried/i.test(reason))
    return `${reason.charAt(0).toUpperCase()}${reason.slice(1)}.`;
  const value = reason.toLowerCase().replaceAll('_', ' ');
  if (/stopped|paused|disabled/.test(value))
    return 'Automation is paused. Start it when you’re ready.';
  if (/offline|heartbeat|worker/.test(value))
    return 'The local worker is offline. Bring it online to continue preparing applications.';
  if (/no .*job|no .*work|empty|eligible/.test(value))
    return 'There are no applications ready for automation right now.';
  if (/limit|capacity|concurrency/.test(value))
    return 'Waiting for an available worker slot or browser tab.';
  if (/pacing|cooldown|domain/.test(value)) return 'Waiting briefly before visiting the next site.';
  return 'Automation is waiting for the next application to become available.';
}
export function relativeTime(value: string): string {
  const elapsed = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(elapsed)) return 'Time unavailable';
  if (Math.abs(elapsed) < 60_000) return 'Just now';
  const unit =
    Math.abs(elapsed) < 3_600_000 ? 'minute' : Math.abs(elapsed) < 86_400_000 ? 'hour' : 'day';
  const size = unit === 'minute' ? 60_000 : unit === 'hour' ? 3_600_000 : 86_400_000;
  return new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' }).format(
    -Math.floor(elapsed / size),
    unit,
  );
}
export function eventSentence(kind: string, payload: RecordData): string {
  const count = (...keys: string[]) =>
    keys.map((key) => payload[key]).find((value) => typeof value === 'number');
  const fields = count('fields', 'filled', 'count', 'planned');
  const checks = count('checks', 'passed', 'count', 'total');
  switch (kind) {
    case 'agent_guidance':
      return `You: ${typeof payload.message === 'string' ? payload.message : 'Guidance sent'}`;
    case 'agent_progress':
      return typeof payload.message === 'string' ? payload.message : 'Working on the application';
    case 'bulk_fill':
      return `Filled ${typeof payload.fieldsFilled === 'number' ? payload.fieldsFilled : 0} fields from About me and saved answers in ${typeof payload.seconds === 'number' ? payload.seconds.toFixed(1) : '?'}s`;
    case 'agent_timing':
      return `Agent timing: ${typeof payload.browserSeconds === 'number' ? payload.browserSeconds.toFixed(1) : '?'}s in browser tools; ${typeof payload.modelAndStartupSeconds === 'number' ? payload.modelAndStartupSeconds.toFixed(1) : '?'}s in the model and startup`;
    case 'agent_stalled':
      return 'The agent stopped because it was no longer making progress.';
    case 'browser_attempt':
      return 'Started reading and filling the application';
    case 'direct_result':
      return typeof payload.note === 'string' && payload.note.trim()
        ? payload.note
        : payload.status === 'filled'
          ? 'The agent finished preparing the application'
          : 'The agent reported its application progress';
    case 'browser_open':
      return 'Opened the posting';
    case 'ingest':
      return 'Added this job to your pipeline';
    case 'applied_logged':
      return 'You logged that you applied to this job';
    case 'tracker_import':
      return 'Imported from your spreadsheet';
    case 'tracking_updated': {
      const fields = Array.isArray(payload.fields) ? payload.fields.map(String) : [];
      if (fields.includes('status') && typeof payload.status === 'string')
        return `You set the status to ${fieldLabel(payload.status)}`;
      const names = fields.map((name) => trackedFieldTitles[name] ?? fieldLabel(name));
      return names.length
        ? `You updated ${names.slice(0, 3).join(', ')}${names.length > 3 ? ' and more' : ''}`
        : 'You updated the tracking details';
    }
    case 'meta':
      return 'Read the job details';
    case 'inventory':
      return fields === undefined
        ? 'Found the application fields'
        : `Found ${fields} application fields`;
    case 'plan':
      return fields === undefined
        ? 'Prepared the application answers'
        : `Prepared ${fields} application answers`;
    case 'fill':
      return Array.isArray(payload.failures) && payload.failures.length
        ? 'Some fields could not be filled'
        : fields === undefined
          ? 'Filled the application answers'
          : `Filled ${fields} fields`;
    case 'check':
    case 'verification':
      return payload.ok === false
        ? 'Application checks need attention'
        : payload.ok === true
          ? checks === undefined
            ? 'All application checks passed'
            : `All ${checks} checks passed`
          : 'Ran the application checks';
    case 'transition':
      return payload.to === 'awaiting_approval'
        ? 'Waiting for your approval'
        : payload.to === 'submitted'
          ? 'Application submitted'
          : `Application moved to ${fieldLabel(payload.to).toLowerCase()}`;
    case 'wait': {
      const reason = String(payload.reason ?? '');
      if (/approv/i.test(reason)) return 'Waiting for your approval';
      if (/missing answers?/i.test(reason))
        return `Waiting for your answer${/:\s*1\b/.test(reason) ? '' : 's'}`;
      return reason ? blockedHelp(reason).title : 'Waiting for you';
    }
    case 'mail_code_used':
      return 'Typed the emailed verification code';
    case 'mail_link_used':
      return 'Opened the emailed verification link';
    case 'mail_unavailable':
      return 'Could not read the mailbox for a verification email';
    case 'approval':
      return payload.method === 'auto-detected'
        ? 'JobPilot saw you submit in Neo and recorded it'
        : payload.scope === 'auto-mode'
          ? 'Auto-submit was used for this application'
          : payload.scope === 'owner-submitted'
            ? 'You said you submitted this application'
            : 'You approved this application for submission';
    case 'submit_clicked':
      return payload.auto
        ? 'Auto-submit started: the agent was allowed to click Submit once'
        : payload.unapproved
          ? 'The application was submitted without your go-ahead'
          : 'The application was submitted';
    case 'confirmation':
      return payload.ok === false
        ? 'Could not confirm that the site received the application'
        : 'Checked the submission confirmation';
    case 'answer_updated':
      return 'You saved an application answer';
    case 'resume':
      return 'You resumed this application';
    case 'cv':
      return payload.mode === 'tailor' ? 'Prepared the tailored CV' : 'Prepared your CV';
    case 'cv_check':
      return payload.ok === false ? 'Your CV needs attention' : 'Checked your CV';
    case 'cv_track_selected':
      return payload.source === 'model-comparison' && typeof payload.summary === 'string'
        ? `Selected CV: ${String(payload.track)}. ${payload.summary}`
        : 'Selected a CV track';
    case 'answers_saved':
      return 'You saved answers (not sent to the agent yet)';
    case 'posting_prefetched':
      return `Read the posting from ${String(payload.provider ?? 'the job board')} without opening a browser (${Number(payload.questions ?? 0)} questions found)`;
    case 'answers_prefetched': {
      const stored = Number(payload.stored ?? 0);
      const known = Number(payload.known ?? 0);
      const open = Number(payload.open ?? 0);
      return `Prepared answers before opening the browser: ${known} from your profile and saved answers, ${stored} written by the agent${open ? `, ${open} still open` : ''}`;
    }
    case 'answer_suggested':
      return 'The agent drafted an answer for this application';
    case 'answers_prefetch_failed':
      return 'Could not prepare answers early; the browser agent will answer them';
    case 'cv_select_page_retry':
      return 'Waiting for the job page to show its posting before choosing a CV';
    case 'job_settings':
      return 'You updated the CV settings';
    case 'draft_invalidated':
      return 'The draft changed and needs fresh checks';
    case 'pages_complete':
      return 'Reviewed all application pages';
    case 'browser_closed':
      return 'Closed the application browser';
    case 'profile_updated':
      return 'You updated your profile';
    case 'fact_created':
      return 'You added a fact about yourself';
    case 'fact_updated':
      return 'You updated a fact about yourself';
    case 'fact_deleted':
      return 'You removed a fact about yourself';
    case 'answer_deleted':
      return 'You removed a saved answer';
    case 'merged':
      return 'You merged duplicate applications into this one';
    case 'merged_into':
      return 'Merged into another application';
    case 'mode_changed': {
      const label = (value: unknown) =>
        value === 'auto' ? 'auto-submit' : value === 'review' ? 'review first' : 'the default';
      return payload.scope === 'job'
        ? `You set this job to ${label(payload.to)}`
        : `You switched submitting to ${label(payload.to)}`;
    }
    case 'submit_mode':
      return payload.mode === 'auto'
        ? 'This run was allowed to submit on its own'
        : typeof payload.reason === 'string' && payload.reason
          ? `This run stops before Submit. ${payload.reason}`
          : 'This run stops before Submit for your review';
    case 'park_signal':
      return payload.signal === 'tab_closed'
        ? 'The application tab was closed before a submission was seen'
        : 'The application tab changed and may have been submitted';
    case 'park_signal_dismissed':
      return 'You said it was not submitted';
    case 'submit_fence_released':
      return 'The agent stopped before Submit, so nothing was sent';
    case 'automation_changed':
      return payload.enabled === true ? 'Automation started' : 'Automation paused';
    case 'account_created':
      return 'Created an account on the employer’s site';
    case 'credential_refused':
      return 'Blocked a sign-in that did not match the site';
    case 'agent_timeout':
      return 'An agent took too long and will retry';
    case 'agent_unavailable':
      return 'Claude was unavailable, so automation paused';
    case 'error':
      return 'Something went wrong while preparing this application';
    case 'auto_answered':
      return payload.after === 0
        ? 'Your saved answers filled the missing questions'
        : 'Your saved answers filled some missing questions';
    case 'submitted_without_approval':
      return 'This may have been sent without your approval';
    case 'lease_released':
      return 'The worker finished this step';
    case 'transition_refused':
      return 'The application could not move to the next step';
    default:
      return /error|fail/.test(kind)
        ? 'The application needs attention'
        : 'Application activity recorded';
  }
}
export function splitNeeds(data?: NeedsYou) {
  const ready = [...(data?.readyToApprove ?? [])];
  const approvalIds = new Set(ready.map((job) => job.id));
  for (const job of data?.blocked ?? []) {
    if (job.status === 'awaiting_approval' && !approvalIds.has(job.id)) {
      ready.push({
        id: job.id,
        company: job.company,
        role: job.role,
        cvTrack: null,
        draftHash: null,
        clean: false,
        reasons: ['Open it to review'],
        tabOpen: Boolean(job.tabOpen),
      });
      approvalIds.add(job.id);
    }
  }
  const other = (data?.blocked ?? []).filter((job) => !approvalIds.has(job.id));
  return {
    ready,
    answers: other.filter((job) => job.missing?.length),
    help: other.filter((job) => !job.missing?.length),
  };
}

export function eventTone(
  kind: string,
  payload: RecordData,
): 'ok' | 'warn' | 'danger' | 'info' | 'neutral' {
  if (kind === 'submit_clicked' || kind === 'applied_logged' || kind === 'approval') return 'ok';
  if (kind === 'transition')
    return payload.to === 'submitted'
      ? 'ok'
      : payload.to === 'awaiting_approval'
        ? 'warn'
        : 'neutral';
  if (
    kind === 'wait' ||
    kind === 'transition_refused' ||
    /error|fail|agent_unavailable|agent_timeout/.test(kind)
  )
    return 'warn';
  if (kind === 'ingest' || kind === 'browser_review') return 'info';
  return 'neutral';
}
export function dayLabel(value: string): string {
  const day = new Date(value);
  if (Number.isNaN(day.getTime())) return 'Earlier';
  const diff = Math.round(
    (new Date().setHours(0, 0, 0, 0) - new Date(day).setHours(0, 0, 0, 0)) / 86_400_000,
  );
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  return day.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
}

export interface TabFocusResult {
  focused: boolean;
  reason: string;
  page?: string | null;
  title?: string | null;
  group?: string | null;
}

/** What to tell the owner after "Find agent tab": raised, or exactly where the tab is when Neo cannot raise it. */
export function focusMessage(result: TabFocusResult): string {
  if (result.reason === 'no_tab') return 'That tab is already closed. Open the job instead.';
  if (result.focused) return 'Agent tab brought to the front in BrowserOS neo.';
  const where = [
    result.group ? `the "${result.group}" tab group (expanded)` : null,
    `Neo page ${result.page ?? '?'}`,
    result.title ? `titled "${result.title}"` : null,
  ].filter(Boolean);
  return `The agent tab is in ${where.join(', ')}: switch to it in Neo.`;
}

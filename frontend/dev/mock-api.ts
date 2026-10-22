// Dev-only fake API for designing the "many agents at once" views without a busy backend.
// Run: pnpm exec vite --config vite.mock.config.ts   (http://127.0.0.1:5175)
// Nothing here is bundled into the dashboard and no request ever reaches the real server: writes are accepted in memory
// only, so pressing Approve in this mode approves nothing.
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { Plugin } from 'vite';
import type { NeedsYou } from '../src/api/models';

type Json = Record<string, unknown>;

const companies = ['Varda Space', 'Anthropic', 'Stripe', 'Figma', 'Linear', 'Vercel', 'Ramp', 'Notion', 'Datadog', 'Cloudflare', 'OCP Group', 'Scale AI',
  'Mistral AI', 'Hugging Face', 'Plaid', 'Retool', 'Airtable', 'Brex', 'Rippling', 'Mercury', 'Supabase', 'PostHog', 'Replit', 'Modal'];
const roles = ['Software Engineer Intern', 'AI Engineer, New Grad', 'Product Analytics Internship - Spring 2027', 'Machine Learning Engineer I',
  'Data Analyst', 'Forward Deployed Engineer', 'Cybersecurity Internship - Summer 2027', 'Associate Consultant', 'Backend Engineer (Python)', 'Research Engineer'];
const boards = ['greenhouse', 'lever', 'ashby', 'workable', 'custom'];
const pick = <T,>(list: T[], index: number) => list[index % list.length];
const id = (index: number) => `j_mock${String(index).padStart(3, '0')}`;
const iso = (minutesAgo: number) => new Date(Date.now() - minutesAgo * 60_000).toISOString();
const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);

const questions = [
  { key: 'q_name', label: 'Name', type: 'text', options: [], required: true, generic: false },
  { key: 'q_sponsorship', label: 'Will you now or in the future require sponsorship for employment visa status?', type: 'radio', options: ['Yes', 'No'], required: true, generic: false },
  { key: 'q_relocate', label: 'Are you willing to relocate to San Francisco?', type: 'select', options: ['Yes', 'No', 'Open to discussing'], required: true, generic: false },
  { key: 'q_salary', label: 'What are your salary expectations (USD)?', type: 'number', options: [], required: false, generic: false },
  { key: 'q_start', label: 'Earliest start date', type: 'date', options: [], required: true, generic: false },
  { key: 'q_heard', label: 'How did you hear about this role?', type: 'custom-select', options: ['LinkedIn', 'Company website', 'Referral', 'Other'], required: false, generic: false },
  { key: 'q_consent', label: 'May we contact you about future roles?', type: 'checkbox', options: ['Yes', 'No'], required: false, generic: false },
  { key: 'q_why', label: 'Why would you like to work with us?', type: 'textarea', options: [], required: true, generic: false },
  { key: 'q_start', label: 'Available Start Date', type: 'date', options: [], required: true, generic: true },
  { key: 'q_email', label: 'Contact email', type: 'email', options: [], required: true, generic: false },
  { key: 'q_phone', label: 'Contact phone', type: 'tel', options: [], required: false, generic: false },
  { key: 'q_portfolio', label: 'Portfolio URL', type: 'url', options: [], required: false, generic: false },
  // Files are currently hidden by the backend; keep a fixture for the explanatory fallback if one is ever sent.
  { key: 'q_file', label: 'Portfolio document', type: 'file', options: [], required: false, generic: false },
  { key: 'q_team', label: 'Preferred team', type: 'select', options: ['Platform', 'Research', 'Product'], optionsTotal: 80, required: true, multiple: true, generic: false },
  { key: 'q_discipline', label: 'Discipline', type: 'custom-select', options: ['Computer Science', 'Mathematics', 'Other'], searchable: false, required: true, generic: false },
  { key: 'q_city', label: 'Location (City)', type: 'custom-select', options: [], searchable: true, required: true, generic: false },
  { key: 'q_capture_failed', label: 'End date month', type: 'custom-select', options: [], required: true, generic: false },
  { key: 'q_school', label: 'School', type: 'custom-select', options: ['Al Azhar University', 'Other / not listed'], searchable: true, unmatchedValue: 'Example State University', required: true, generic: false },
  { key: 'q_choices_missing', label: 'Preferred office', type: 'select', options: [], required: true, generic: false },
  { key: 'q_unknown', label: 'Additional information', type: 'unrecognized', options: [], required: null, multiple: null, generic: false },
] satisfies NeedsYou['blocked'][number]['missingFields'];
const helpReasons = [
  'Click Save on the password prompt in Neo',
  'automation error: inventory and plan required', 'no progress at inventory', "browser navigated outside the job's allowed host",
  'CAPTCHA on the application page', 'submission uncertain: check the parked application before continuing. Agent note: After one click the page shows "Phone: enter a valid phone number" under the Phone field and the form is still on screen.', 'automation retry limit reached',
  'apply manually: board form cannot be safely automated', 'possible duplicate of j_mock004', 'approve account creation for https://careers.example.com',
  'email code required to sign in', 'interactive action cap exhausted', 'form changed since approval; re-approval required',
];

const statuses = ['submitted', 'submitted', 'screening', 'interview', 'offer', 'rejected', 'preparing', 'awaiting_approval', 'preparing', 'submitted'];
const tracker = Array.from({ length: 64 }, (_, index) => ({
  id: id(index), company: pick(companies, index), role: pick(roles, index * 3), appliedOn: index % 9 === 6 ? null : day(-index), status: pick(statuses, index),
  userStatus: null, waitingOn: index % 11 === 0 ? 'you' : index % 9 === 6 ? 'agent' : 'site', blockedReason: null,
  dateContacted: index % 5 === 2 ? day(-index + 3) : null, interviewOn: index % 10 === 3 ? day(4) : null, followUpOn: index % 4 === 1 ? day(index % 8 === 1 ? -1 : 6) : null,
  resumeLabel: null, cvTrack: pick(['swe', 'ai-engineer', 'analytics'], index), cvMode: index % 6 === 0 ? 'tailor' : 'base', submittedVia: null, jobLevel: 'Entry',
  salary: index % 3 === 0 ? '$120k–$150k' : null, location: pick(['San Francisco, CA', 'Remote (US)', 'Casablanca, Morocco', 'New York, NY', 'Atlanta, Georgia, United States · Hybrid'], index),
  workMode: pick(['Hybrid', 'Remote', 'On-site'], index), employmentType: pick(['Full-time', 'Internship'], index), url: `https://job-boards.greenhouse.io/${pick(companies, index).toLowerCase().replace(/\W+/g, '')}/jobs/${4000 + index}`,
  source: index % 3 === 0 ? 'platform' : 'manual', notes: index % 7 === 0 ? 'Referred by a friend on the infra team.' : null, hasDescription: true, updatedAt: iso(index * 37), archivedAt: null,
  missing: index % 13 === 5 ? ['location'] : [],
}));

const ready = Array.from({ length: 17 }, (_, index) => ({
  id: id(100 + index), company: pick(companies, index + (index % 4 === 0 ? 0 : 3)), role: pick(roles, index * 7), draftHash: `hash${index}`, cvTrack: pick(['swe', 'ai-engineer'], index), parkSignal: (index === 1 ? 'looks_submitted' : index === 2 ? 'tab_closed' : null) as string | null,
  clean: index < 14, reasons: index < 14 ? [] : pick([['2 answers set by the agent'], ['CV was tailored for this job'], ['Optional question left empty', '1 answer set by the agent']], index),
}));
const evidenceFor = (reason: string | null | undefined) => reason?.includes('submission uncertain')
  ? { capturedAt: iso(2), url: 'https://job-boards.greenhouse.io/acme/jobs/4001', title: 'Software Engineer Intern - Acme',
      excerpt: 'There was a problem with your submission. Phone: enter a valid phone number. First name Last name Email Phone Resume Submit application', hasScreenshot: true }
  : null;
const blocked = [
  ...Array.from({ length: 11 }, (_, index) => {
    const mine = questions.filter((_, q) => (index + q) % 3 === 0 || (q === 0 && index < 8));
    return { id: id(200 + index), company: pick(companies, index + 6), role: pick(roles, index * 5), board: pick(boards, index), reason: `missing answers: ${mine.length}`, missing: mine.map(q => q.key), missingFields: mine, status: 'preparing', answeredCount: index % 3 === 0 ? 4 : 0, settledCount: index % 3 === 0 ? 1 : 0, step: index % 3 === 0 ? 'step 2 of 8' : null };
  }),
  ...helpReasons.map((reason, index) => ({ id: id(300 + index), company: pick(companies, index + 12), role: pick(roles, index * 2), reason, missing: [], missingFields: [], status: 'preparing', answeredCount: 0, settledCount: 0, step: null })),
];

const steps = ['capture', 'inventory', 'plan', 'cv', 'fill', 'verify', 'next_page', 'fill', 'verify', 'capture', 'inventory', 'fill'];
let concurrency = 16;
let maxOpenBrowsers = 6;
let running = true;
const automation = () => ({
  running, concurrency, started: 212, finished: 187, failed: 6, workerOnline: true, queueOnline: true,
  idleReason: null, browsersOpen: 2, browserLimit: maxOpenBrowsers, capacity: concurrency,
  active: running ? steps.slice(0, concurrency).map((step, index) => ({ worker: `00000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`, job: id(400 + index), since: Date.now() - (20 + index * 41) * 1000,
    role: index === 9 ? null : pick(roles, index * 9), company: index === 9 ? null : pick(companies, index + 2), url: 'https://example.com/job', node: 'mac', started: true, elapsedSeconds: 20 + index * 41, step })) : [],
  workersOnline: 1, browserOnline: true, activeRuns: running ? concurrency : 0,
  monitoring: {
    today: { usd: 0.4312, tokens: 2223167, runs: 12, jobs: 10, averageCostPerJob: 0.04312 },
    last7Days: { usd: 3.2, tokens: 10000000, runs: 100, jobs: 80, averageCostPerJob: 0.04 },
    allTime: { usd: 24, tokens: 80000000, runs: 800, jobs: 600, averageCostPerJob: 0.04 },
    queueDepth: 23, recentFailures: [{ task: 'failed-1', job: id(400), role: 'Software engineer', company: 'Acme', at: iso(0), node: 'mac', lastError: 'agent_stalled' }],
  },
});

const plan = [
  ['#first_name', 'Jane', 'profile:first_name'], ['#last_name', 'Doe', 'profile:last_name'], ['#email', 'jane.doe@example.com', 'profile:email'],
  ['#phone', '+1 555 010 0199', 'profile:phone'], ['#location', 'Atlanta, GA', 'profile:city'], ['#linkedin', 'https://linkedin.com/in/example', 'profile:linkedin'],
  ['#school', 'Example State University', 'derived:education'], ['#degree', "Bachelor's", 'derived:education'], ['#citizenship', 'Canada', 'derived:citizenship'],
  ['#authorized', 'Yes', 'answer:global'], ['#sponsorship', 'Yes', 'answer:global'], ['#heard', 'Company website', 'derived:job source'],
  ['#why', 'I shipped a retrieval pipeline that cut support load by 30% and want to do the same at your scale.', 'agent'],
  ['#start', 'January 2027', 'agent'], ['#pronouns', 'They/them', 'answer:company:stripe'], ['#github', 'https://github.com/example', 'profile:github'],
].map(([key, value, source]) => ({ key, value, source }));
const inventory = plan.map(row => ({ key: row.key, selector: row.key, label: ({ '#why': 'Why do you want to work here?', '#heard': 'How did you hear about us?', '#authorized': 'Are you legally authorized to work in the United States?',
  '#sponsorship': 'Will you require sponsorship?', '#start': 'Earliest start date', '#school': 'School', '#citizenship': 'Country of citizenship' } as Record<string, string>)[row.key] ?? row.key.slice(1).replace(/_/g, ' '), type: 'text' }));

let submitMode: 'review' | 'auto' = 'review';
const submitOverride = new Map<string, 'review' | 'auto'>();
function submitModeFor(jobId: string): Json {
  const override = submitOverride.get(jobId) ?? null;
  const requested = override ?? submitMode;
  // Mirrors a forced-review reason so the panel is reviewable without the server.
  const forced = requested === 'auto' && jobId.endsWith('3');
  return { effective: forced ? 'review' : requested, requested, override, reason: forced ? 'You chose to continue without answering some required questions.' : null };
}

function job(jobId: string): Json {
  const number = Number(jobId.replace(/\D/g, '')) || 0;
  const approval = ready.find(item => item.id === jobId);
  const stuck = blocked.find(item => item.id === jobId);
  const row = tracker.find(item => item.id === jobId);
  const rows = approval?.clean ? plan.filter(item => item.source !== 'agent') : plan;
  return {
    id: jobId, company: approval?.company ?? stuck?.company ?? row?.company ?? pick(companies, number), role: approval?.role ?? stuck?.role ?? row?.role ?? pick(roles, number),
    usage: { usd: 0.08, tokens: 300000 }, url: `https://job-boards.greenhouse.io/acme/jobs/${number}`, board: pick(boards, number), status: approval ? 'awaiting_approval' : stuck?.status ?? row?.status ?? 'preparing',
    waiting_on: approval || stuck ? 'you' : 'agent', blocked_reason: stuck?.reason ?? null, cv: { track: 'swe', mode: number % 5 === 0 ? 'tailor' : 'base', attached: true },
    form: { fields: rows.length + 1, planned: rows.length, missing: stuck?.missing ?? [], filled: !!approval, verified: !!approval }, next: approval ? 'blocked' : 'fill',
    tracking: { source: 'platform', appliedOn: null }, posting: 'We are looking for an engineer who enjoys ownership.\n\nYou will build and operate services used by millions.', location: 'San Francisco, CA', pay: '$130k–$160k',
    events: [['check', { ok: true, count: 22 }], ['fill', { fields: rows.length }], ['cv', { mode: 'base' }], ['plan', { planned: rows.length }], ['inventory', { fields: rows.length + 1 }], ['meta', {}], ['ingest', {}]]
      .map(([kind, payload], index) => ({ ts: iso(index * 2 + 1), kind, actor: 'system', payload })),
    plan: rows, inventory, agentEdits: [], draftRevision: 3,
    submitMode: submitModeFor(jobId),
    submitEvidence: evidenceFor(stuck?.reason),
  };
}

const kinds: [string, Json, string][] = [
  ['transition', { to: 'awaiting_approval' }, 'system'], ['check', { ok: true, count: 22 }, 'system'], ['fill', { fields: 14 }, 'system'], ['wait', { reason: 'automation error: inventory and plan required' }, 'agent'],
  ['inventory', { fields: 15 }, 'agent'], ['wait', { reason: 'no progress at inventory' }, 'agent'], ['tracking_updated', { fields: ['status'], status: 'interview' }, 'user'], ['meta', {}, 'agent'],
  ['wait', { reason: "browser navigated outside the job's allowed host" }, 'system'], ['agent_timeout', {}, 'system'], ['ingest', {}, 'user'], ['error', { message: 'eval failed: timeout' }, 'system'],
  ['approval', {}, 'user'], ['submit_clicked', {}, 'agent'], ['wait', { reason: 'missing answers: 2' }, 'system'], ['auto_answered', { before: 2, after: 0 }, 'system'],
];
const updates = Array.from({ length: 120 }, (_, index) => {
  const [kind, payload, actor] = pick(kinds, index);
  return { id: 5000 - index, ts: iso(index * 9), kind, actor, payload, jobId: id(index % 40), role: pick(roles, index), company: pick(companies, index) };
});

const tracks = [
  { id: 'swe', title: 'Software Engineering', keywords: ['python', 'backend'], tailorable: true, defaultMode: 'base', market: 'us', sha256: 'a'.repeat(64), jobs: 31 },
  { id: 'ai-engineer', title: 'AI / ML Engineer', keywords: ['llm', 'ml'], tailorable: true, defaultMode: 'tailor', market: 'us', sha256: 'b'.repeat(64), jobs: 18 },
  { id: 'analytics', title: 'Analytics / Data', keywords: ['sql'], tailorable: false, defaultMode: 'base', market: 'any', sha256: 'c'.repeat(64), jobs: 9 },
];
const boardRows = ['greenhouse', 'lever', 'ashby', 'workday', 'linkedin', 'custom'].map((name, index) => ({ id: name, displayName: name[0].toUpperCase() + name.slice(1),
  automation: index === 3 || index === 4 ? 'manual' : index === 5 ? 'assisted' : 'auto' }));

function route(method: string, path: string, query: URLSearchParams, body: Json): unknown {
  if (method !== 'GET') {
    if (path === '/api/ingest') {
      // Mirrors the server contract: one entry per url, in order. "dup" = already tracked, "bad" = rejected.
      return ((body.urls as string[]) ?? []).map((url, index) =>
        url.includes('bad') ? { url, error: 'could not add this link' }
          : { id: `j_mock${index}`, created: !url.includes('dup'), board: 'custom', automation: 'assisted' });
    }
    const ownerSubmitted = /^\/api\/jobs\/([^/]+)\/owner-submitted$/.exec(path);
    if (ownerSubmitted) {
      // Idempotent like the server: a repeat reports `already` and changes nothing.
      const index = ready.findIndex(item => item.id === ownerSubmitted[1]);
      if (index < 0) return { ok: true, already: true, confirmationSeen: null };
      ready.splice(index, 1);
      return { ok: true, already: false, confirmationSeen: index % 2 === 0 };
    }
    if (path.startsWith('/api/automation/')) { running = path.endsWith('start'); return automation(); }
    const reopened = /^\/api\/jobs\/([^/]+)\/(reopen|park-signal\/dismiss)$/.exec(path);
    if (reopened) {
      const item = ready.find(entry => entry.id === reopened[1]);
      if (item) item.parkSignal = null;
      return { ok: true };
    }
    const refreshed = /^\/api\/jobs\/([^/]+)\/refresh-details$/.exec(path);
    if (refreshed) return { ok: true, already: false, state: 'queued' };
    if (path === '/api/jobs/refresh-missing-details') return { ok: true, queued: 3, limit: 25 };
    const modeSaved = /^\/api\/jobs\/([^/]+)\/submit-mode$/.exec(path);
    if (modeSaved) {
      if (body.mode) submitOverride.set(modeSaved[1], body.mode as 'review' | 'auto');
      else submitOverride.delete(modeSaved[1]);
      return { ok: true };
    }
    if (path === '/api/settings') {
      if (body.submitMode) submitMode = body.submitMode as 'review' | 'auto';
      concurrency = Number(body.workerConcurrency ?? concurrency);
      maxOpenBrowsers = Number(body.maxOpenBrowsers ?? maxOpenBrowsers);
      return { workerConcurrency: concurrency, maxOpenBrowsers, submitMode };
    }
    const acknowledged = /^\/api\/jobs\/([^/]+)\/unapproved-submit\/acknowledge$/.exec(path);
    if (acknowledged) {
      const index = blocked.findIndex(item => item.id === acknowledged[1]);
      if (index >= 0) blocked.splice(index, 1); // idempotent: a repeat is a no-op
      return { ok: true };
    }
    const completed = /^\/api\/jobs\/([^/]+)\/owner-completed$/.exec(path);
    if (completed) {
      const index = blocked.findIndex(item => item.id === completed[1]);
      if (index < 0) return { ok: true, already: true };
      blocked.splice(index, 1);
      return { ok: true, already: false };
    }
    const confirmed = /^\/api\/jobs\/([^/]+)\/user-confirmed$/.exec(path);
    if (confirmed) {
      const index = blocked.findIndex(item => item.id === confirmed[1]);
      if (index >= 0) blocked.splice(index, 1);
      return { ok: true };
    }
    const answered = /^\/api\/jobs\/([^/]+)\/(answers|resume)$/.exec(path);
    if (answered) {
      const index = blocked.findIndex(item => item.id === answered[1]);
      const item = blocked[index];
      if (item) {
        const answers = (body.answers ?? []) as { question: string; answer: string; skip: boolean }[];
        const saved = new Set(answers.filter(a => a.skip || a.answer.trim()).map(a => a.question));
        item.missing = item.missing.filter(key => !saved.has(key));
        item.missingFields = item.missingFields.filter(field => !saved.has(field.key));
        if ((body.resume !== false && !item.missing.length) || query.get('try_anyway') === 'true') blocked.splice(index, 1);
      }
    }
    return { ok: true };
  }
  if (path === '/api/session') return { csrf: 'mock' };
  if (path === '/api/tracker') return query.get('archived') ? [] : tracker;
  if (path === '/api/tracker/duplicates') return [];
  if (path === '/api/needs-you') return { blocked: blocked.map(item => ({ ...item, submitEvidence: evidenceFor(item.reason) })), readyToApprove: ready };
  if (path === '/api/automation') return automation();
  if (path === '/api/updates') {
    const before = Number(query.get('before') ?? Infinity);
    const page = updates.filter(item => item.id < before).slice(0, 40);
    return { items: page, next: page.length === 40 ? page[39].id : null };
  }
  if (path === '/api/tracks') return tracks;
  if (path === '/api/boards') return boardRows;
  if (path === '/api/settings') return { workerConcurrency: concurrency, maxOpenBrowsers, submitMode };
  if (path.startsWith('/api/jobs/')) return job(decodeURIComponent(path.split('/')[3] ?? ''));
  if (path === '/api/stats') return {};
  if (path === '/api/profile') return ['first_name', 'last_name', 'email', 'phone', 'city'].map(key => ({ key, value: 'x', source: 'user', confirmedAt: iso(0) }));
  return [];
}

export function mockApi(): Plugin {
  return {
    name: 'jobpilot-mock-api',
    configureServer(server) {
      server.middlewares.use((request: IncomingMessage, response: ServerResponse, next: () => void) => {
        const url = new URL(request.url ?? '/', 'http://mock');
        url.pathname = url.pathname.replace(/^\/api\/v1(?=\/)/, '/api');
        if (!url.pathname.startsWith('/api/')) return next();
        if (/^\/api\/(accounts|account-approvals)(\/|$)/.test(url.pathname)) {
          response.writeHead(404, { 'Content-Type': 'application/json' });
          response.end(JSON.stringify({ detail: 'Not Found' }));
          return;
        }
        if (url.pathname === '/api/events') {
          response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store' });
          const tick = () => response.write(`: ping ${Date.now()}\n\n`);
          tick();
          const timer = setInterval(tick, 3000);
          response.on('close', () => clearInterval(timer));
          return;
        }
        if (url.pathname.endsWith('/submit-evidence/screenshot')) {
          response.setHeader('Content-Type', 'image/svg+xml');
          response.end('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#f4f4f5"/><rect x="40" y="30" width="560" height="40" fill="#fee2e2"/><text x="56" y="56" font-family="sans-serif" font-size="16">There was a problem with your submission.</text><rect x="40" y="100" width="560" height="36" fill="#fff" stroke="#bbb"/><text x="56" y="124" font-family="sans-serif" font-size="14">Phone</text></svg>');
          return;
        }
        const chunks: Buffer[] = [];
        request.on('data', chunk => chunks.push(chunk as Buffer));
        request.on('end', () => {
          let body: Json = {};
          try { body = JSON.parse(Buffer.concat(chunks).toString() || '{}') as Json; } catch { /* empty body */ }
          response.setHeader('Content-Type', 'application/json');
          response.end(JSON.stringify(route(request.method ?? 'GET', url.pathname, url.searchParams, body)));
        });
      });
    },
  };
}

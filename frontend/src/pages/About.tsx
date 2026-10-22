import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check, Copy, Pencil, Plus, Trash2 } from 'lucide-react';
import { api } from '../api/api';
import type { Fact, Profile } from '../api/models';
import { useAction } from '../hooks/useAction';
import {
  Button,
  Chip,
  Empty,
  ErrorMessage,
  IconButton,
  PageHeading,
  Panel,
  QueryState,
  Segment,
  ui,
} from '../components/ui';
import { ConfirmDialog } from '../components/ui/Dialog';
import { matches, NoMatches, ResultCount, SearchField } from '../components/ui/Filters';
import { cn } from '../utils/cn';
import { pick, useUrlParams } from '../hooks/useUrlParams';
import { Pager, SortHeader, usePagedSort } from '../components/ui/PagedTable';
import { ProfileSection } from '../features/profile/ProfileSections';

type Field = {
  key: string;
  title: string;
  placeholder?: string;
  options?: string[];
  wide?: boolean;
  country?: boolean;
};
const groups: { title: string; text: string; fields: Field[] }[] = [
  {
    title: 'Personal info',
    text: 'Used to fill identity fields on application forms.',
    fields: [
      { key: 'first_name', title: 'First name' },
      { key: 'last_name', title: 'Last name' },
      { key: 'preferred_name', title: 'Preferred name' },
      { key: 'email', title: 'Email' },
      { key: 'phone', title: 'Phone', placeholder: '+1 555 0100' },
    ],
  },
  {
    title: 'Links & profiles',
    text: 'Shared with employers on forms that ask for them.',
    fields: [
      { key: 'linkedin', title: 'LinkedIn', placeholder: 'https://linkedin.com/in/…', wide: true },
      { key: 'github', title: 'GitHub', placeholder: 'https://github.com/…' },
      { key: 'website', title: 'Website', placeholder: 'https://…' },
    ],
  },
  {
    title: 'Location & eligibility',
    text: 'Used for work authorization and residency questions.',
    fields: [
      { key: 'city', title: 'City' },
      { key: 'state', title: 'State / region' },
      { key: 'postal_code', title: 'Postal code' },
      { key: 'country', title: 'Country', country: true },
    ],
  },
  {
    title: 'Preferences',
    text: 'Answers to common compensation and availability questions.',
    fields: [
      { key: 'expected_salary', title: 'Expected salary', placeholder: 'e.g. $80,000' },
      { key: 'available_from', title: 'Available from', placeholder: 'e.g. June 2027' },
    ],
  },
];

// One calm field: saves itself when you leave it. No per-row Save button.
function ProfileField({
  field,
  saved,
  countries,
}: {
  field: Field;
  saved: string;
  countries?: string[];
}) {
  const [value, setValue] = useState(saved);
  const action = useAction(
    (next: string) => (next ? api.saveProfile(field.key, next) : api.clearProfile(field.key)),
    `${field.title} saved.`,
  );
  const commit = () => {
    if (value.trim() !== saved.trim()) action.mutate(value.trim());
  };
  const id = `profile-${field.key}`;
  return (
    <div className={field.wide ? 'sm:col-span-2' : ''}>
      <label
        htmlFor={id}
        className="mb-1.5 flex items-center justify-between text-[13px] font-normal text-ink"
      >
        <span>{field.title}</span>
        {action.isSuccess && value.trim() === saved.trim() && (
          <span className="flex items-center gap-1 text-xs font-normal text-ok">
            <Check size={12} aria-hidden="true" />
            Saved
          </span>
        )}
      </label>
      {field.country ? (
        <select
          id={id}
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            action.mutate(event.target.value);
          }}
          className={ui.select}
        >
          <option value="">Not set</option>
          {value && !(countries ?? []).includes(value) && <option>{value}</option>}
          {(countries ?? []).map((name) => (
            <option key={name}>{name}</option>
          ))}
        </select>
      ) : field.options ? (
        <select
          id={id}
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            action.mutate(event.target.value);
          }}
          className={ui.select}
        >
          <option value="">Not set</option>
          {field.options.map((option) => (
            <option key={option}>{option}</option>
          ))}
        </select>
      ) : (
        <input
          id={id}
          value={value}
          placeholder={field.placeholder}
          onChange={(event) => setValue(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === 'Enter') commit();
          }}
          className={ui.input}
        />
      )}
    </div>
  );
}

function FactRow({ fact, similar }: { fact: Fact; similar?: boolean }) {
  const confirmed = !!fact.confirmedAt;
  const [editing, setEditing] = useState(false);
  const [claim, setClaim] = useState(fact.claim);
  const [asking, setAsking] = useState(false);
  const toggle = useAction(
    (confirm: boolean) => api.confirmFact(fact.id, confirm, fact.claim),
    confirmed ? 'Moved back to review.' : 'Confirmed.',
  );
  const save = useAction(() => api.confirmFact(fact.id, confirmed, claim.trim()), 'Fact updated.');
  const remove = useAction(() => api.deleteFact(fact.id), 'Fact deleted.');
  return (
    <li className="border-t border-line/60 py-3.5 first:border-t-0">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          {editing ? (
            <form
              className="grid gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                save.mutate(undefined, { onSuccess: () => setEditing(false) });
              }}
            >
              <label className="sr-only" htmlFor={`fact-${fact.id}`}>
                Edit fact
              </label>
              <textarea
                id={`fact-${fact.id}`}
                autoFocus
                rows={3}
                value={claim}
                onChange={(event) => setClaim(event.target.value)}
                className={ui.textarea}
              />
              <ErrorMessage error={save.error} />
              <div className="flex gap-2">
                <Button
                  type="submit"
                  variant="primary"
                  loading={save.isPending}
                  disabled={!claim.trim() || claim.trim() === fact.claim}
                >
                  Save
                </Button>
                <Button
                  type="button"
                  onClick={() => {
                    setClaim(fact.claim);
                    setEditing(false);
                  }}
                >
                  Cancel
                </Button>
              </div>
            </form>
          ) : (
            <p className="text-[14px] leading-snug [overflow-wrap:anywhere] text-ink">
              {fact.claim}
            </p>
          )}
          <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted [overflow-wrap:anywhere]">
            From {fact.source}
            {similar && (
              <Chip tone="warn">
                <Copy size={11} aria-hidden="true" /> Looks like another fact
              </Chip>
            )}
          </p>
        </div>
        {!editing && (
          <div className="flex shrink-0 items-center gap-1">
            <IconButton
              label={`Edit fact: ${fact.claim.slice(0, 60)}`}
              title="Edit"
              onClick={() => setEditing(true)}
            >
              <Pencil size={15} aria-hidden="true" />
            </IconButton>
            <IconButton
              tone="danger"
              label={`Delete fact: ${fact.claim.slice(0, 60)}`}
              title="Delete"
              onClick={() => {
                remove.reset();
                setAsking(true);
              }}
            >
              <Trash2 size={15} aria-hidden="true" />
            </IconButton>
            <button
              type="button"
              role="switch"
              aria-checked={confirmed}
              aria-label={`${confirmed ? 'Confirmed' : 'Not confirmed'}: ${fact.claim.slice(0, 60)}`}
              disabled={toggle.isPending}
              onClick={() => toggle.mutate(!confirmed)}
              title={
                confirmed
                  ? 'Confirmed: JobPilot may claim this'
                  : 'Not confirmed: JobPilot will not claim this'
              }
              className={cn(
                ui.focus,
                'relative ml-1 h-7 w-12 shrink-0 cursor-pointer rounded-full border transition-colors disabled:cursor-not-allowed disabled:opacity-60 before:absolute before:-inset-2 before:content-[""]',
                confirmed ? 'border-accent bg-accent' : 'border-line bg-raised',
              )}
            >
              <span
                className={`absolute top-0.5 size-5 rounded-full bg-white shadow-sm transition-[left] dark:bg-ink ${confirmed ? 'left-[23px]' : 'left-0.5'}`}
              />
            </button>
          </div>
        )}
      </div>
      {asking && (
        <ConfirmDialog
          title="Delete this fact?"
          confirmLabel="Delete"
          pending={remove.isPending}
          error={remove.error}
          onClose={() => setAsking(false)}
          onConfirm={() => remove.mutate(undefined, { onSuccess: () => setAsking(false) })}
        >
          <p>“{fact.claim}”</p>
          <p className="text-muted">JobPilot will stop using it. This can’t be undone.</p>
        </ConfirmDialog>
      )}
    </li>
  );
}

// Facts that share most of their words are probably the same claim written twice.
const words = (text: string) =>
  new Set(
    text
      .toLowerCase()
      .replace(/[^a-z0-9 ]+/g, ' ')
      .split(/\s+/)
      .filter((word) => word.length > 3),
  );
function similarIds(facts: Fact[]): Set<string> {
  const sets = facts.map((fact) => ({ id: fact.id, words: words(fact.claim) }));
  const out = new Set<string>();
  for (let i = 0; i < sets.length; i++)
    for (let j = i + 1; j < sets.length; j++) {
      const a = sets[i].words,
        b = sets[j].words;
      if (!a.size || !b.size) continue;
      let both = 0;
      a.forEach((word) => {
        if (b.has(word)) both++;
      });
      if (both / (a.size + b.size - both) >= 0.7) {
        out.add(sets[i].id);
        out.add(sets[j].id);
      }
    }
  return out;
}

function AddFact() {
  const [open, setOpen] = useState(false);
  const [claim, setClaim] = useState('');
  const create = useAction(
    () => api.createFact(claim.trim()),
    (result) => (result.created ? 'Fact added.' : 'You already have that fact.'),
  );
  return (
    <div className="mb-3">
      {open ? (
        <form
          className="grid gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate(undefined, {
              onSuccess: () => {
                setClaim('');
                setOpen(false);
              },
            });
          }}
        >
          <label className="sr-only" htmlFor="new-fact">
            New fact
          </label>
          <textarea
            id="new-fact"
            autoFocus
            rows={2}
            value={claim}
            onChange={(event) => setClaim(event.target.value)}
            placeholder="Something true about you that applications may ask, e.g. “Fluent in French and Arabic.”"
            className={ui.textarea}
          />
          <ErrorMessage error={create.error} />
          <div className="flex gap-2">
            <Button
              type="submit"
              variant="primary"
              loading={create.isPending}
              disabled={!claim.trim()}
            >
              Add fact
            </Button>
            <Button type="button" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <Button onClick={() => setOpen(true)}>
          <Plus size={15} aria-hidden="true" />
          Add a fact
        </Button>
      )}
    </div>
  );
}

const factTabs = ['review', 'confirmed'] as const;

function Facts({ facts }: { facts: Fact[] }) {
  const url = useUrlParams();
  const tab = pick(url.get('facts'), factTabs, 'review');
  const query = url.get('fq');
  const pending = facts.filter((fact) => !fact.confirmedAt);
  const done = facts.filter((fact) => fact.confirmedAt);
  const similar = useMemo(() => similarIds(facts), [facts]);
  const base = tab === 'review' ? pending : done;
  const list = base.filter((fact) => matches(query, fact.claim, fact.source));
  const accessors = useMemo(() => ({ claim: (fact: Fact) => fact.claim }), []);
  const paged = usePagedSort(list, { pageSize: 8, accessors, resetKey: `${tab}|${query}` });
  const clear = () => url.set({ fq: null });
  return (
    <Panel
      id="facts"
      title="What you can claim"
      description="JobPilot only writes assertions you have confirmed here."
      action={
        <div className={ui.segmented} role="group" aria-label="Show facts">
          {(
            [
              ['review', 'To review', pending.length],
              ['confirmed', 'Confirmed', done.length],
            ] as const
          ).map(([id, text, count]) => (
            <Segment
              key={id}
              aria-pressed={tab === id}
              active={tab === id}
              onClick={() => url.set({ facts: id === 'review' ? null : id })}
              className="min-h-8 px-3.5 text-xs"
            >
              {text} <span className="tabular-nums">{count}</span>
            </Segment>
          ))}
        </div>
      }
    >
      <AddFact />
      {base.length > 0 && (
        <div className="mb-3 grid gap-2">
          <SearchField
            primary
            label="Search facts"
            placeholder="Search claims or sources…"
            value={query}
            onChange={(value) => url.set({ fq: value })}
          />
          <ResultCount
            shown={list.length}
            total={base.length}
            noun={tab === 'review' ? 'to review' : 'confirmed'}
            filtered={!!query.trim()}
            onClear={clear}
          />
        </div>
      )}
      {paged.rows.length ? (
        <ul className="list-none divide-y divide-line/50">
          {paged.rows.map((fact) => (
            <FactRow
              key={`${fact.id}:${fact.confirmedAt}:${fact.claim}`}
              fact={fact}
              similar={similar.has(fact.id)}
            />
          ))}
        </ul>
      ) : base.length ? (
        <NoMatches noun="facts" query={query} onClear={clear} />
      ) : (
        <Empty>{tab === 'review' ? 'Everything is confirmed.' : 'Nothing confirmed yet.'}</Empty>
      )}
      <div className="pt-2">
        <Pager
          page={paged.page}
          pages={paged.pages}
          total={paged.total}
          pageSize={paged.pageSize}
          onPage={paged.setPage}
          label="facts"
        />
      </div>
    </Panel>
  );
}

function AnswerRow({ row }: { row: { questionKey: string; answer: string; scope: string } }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(row.answer);
  const [asking, setAsking] = useState(false);
  const save = useAction(
    () => api.saveAnswer(row.questionKey, value.trim(), row.scope),
    'Answer updated.',
  );
  const remove = useAction(() => api.deleteAnswer(row.questionKey, row.scope), 'Answer deleted.');
  return (
    <tr className="border-b border-line/60 transition-colors last:border-0 hover:bg-raised/40">
      <td className="px-3.5 py-3 font-normal capitalize text-ink">{row.questionKey}</td>
      <td className="px-3.5 py-3 font-medium text-ink">
        {editing ? (
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              save.mutate(undefined, { onSuccess: () => setEditing(false) });
            }}
          >
            <label className="sr-only" htmlFor={`answer-${row.questionKey}`}>
              Answer
            </label>
            <input
              id={`answer-${row.questionKey}`}
              autoFocus
              value={value}
              onChange={(event) => setValue(event.target.value)}
              className={`${ui.input} max-w-xs`}
            />
            <Button
              type="submit"
              variant="primary"
              loading={save.isPending}
              disabled={!value.trim() || value.trim() === row.answer}
            >
              Save
            </Button>
            <Button
              type="button"
              onClick={() => {
                setValue(row.answer);
                setEditing(false);
              }}
            >
              Cancel
            </Button>
            <ErrorMessage error={save.error} />
          </form>
        ) : (
          row.answer
        )}
      </td>
      <td className="px-3.5 py-3 text-xs text-muted">
        {row.scope === 'global' ? 'All applications' : row.scope}
      </td>
      <td className="px-3.5 py-3 text-right whitespace-nowrap">
        <IconButton
          label={`Edit answer for ${row.questionKey}`}
          title="Edit"
          onClick={() => setEditing(true)}
        >
          <Pencil size={15} aria-hidden="true" />
        </IconButton>
        <IconButton
          tone="danger"
          label={`Delete answer for ${row.questionKey}`}
          title="Delete"
          onClick={() => {
            remove.reset();
            setAsking(true);
          }}
        >
          <Trash2 size={15} aria-hidden="true" />
        </IconButton>
        {asking && (
          <ConfirmDialog
            title="Delete this saved answer?"
            confirmLabel="Delete"
            pending={remove.isPending}
            error={remove.error}
            onClose={() => setAsking(false)}
            onConfirm={() => remove.mutate(undefined, { onSuccess: () => setAsking(false) })}
          >
            <p>JobPilot will ask you “{row.questionKey}” again next time it comes up.</p>
          </ConfirmDialog>
        )}
      </td>
    </tr>
  );
}

function AddAnswer() {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const save = useAction(
    () => api.saveAnswer(question.trim(), answer.trim(), 'global'),
    'Answer saved. It is used the next time this question appears.',
  );
  if (!open)
    return (
      <div className="mb-4">
        <Button onClick={() => setOpen(true)}>
          <Plus size={15} aria-hidden="true" />
          Add an answer
        </Button>
      </div>
    );
  return (
    <form
      className="mb-4 grid gap-3 rounded-control border border-line bg-raised/30 p-4 sm:grid-cols-2"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate(undefined, {
          onSuccess: () => {
            setQuestion('');
            setAnswer('');
            setOpen(false);
          },
        });
      }}
    >
      <label className={ui.label}>
        Question, as the form words it
        <input
          autoFocus
          required
          maxLength={300}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="e.g. Are you willing to relocate?"
          className={ui.input}
        />
      </label>
      <label className={ui.label}>
        Your answer
        <input
          required
          maxLength={2000}
          value={answer}
          onChange={(event) => setAnswer(event.target.value)}
          className={ui.input}
        />
      </label>
      <div className="flex gap-2 sm:col-span-2">
        <Button
          type="submit"
          variant="primary"
          loading={save.isPending}
          disabled={!question.trim() || !answer.trim()}
        >
          Save answer
        </Button>
        <Button type="button" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
      {save.error ? (
        <div className="sm:col-span-2">
          <ErrorMessage error={save.error} />
        </div>
      ) : null}
    </form>
  );
}

export function About() {
  const profile = useQuery({ queryKey: ['profile'], queryFn: api.profile });
  const facts = useQuery({ queryKey: ['facts'], queryFn: api.facts });
  const answers = useQuery({ queryKey: ['answers'], queryFn: api.answers });
  const countries = useQuery({
    queryKey: ['countries'],
    queryFn: api.countries,
    staleTime: Infinity,
  });
  const bank = useQuery({
    queryKey: ['answer-bank'],
    queryFn: api.answerBank,
    staleTime: Infinity,
  });
  const usCitizen = useMemo(() => {
    const v = (profile.data ?? []).find((item: Profile) => item.key === 'citizenships')?.value;
    return (
      Array.isArray(v) && v.some((e) => (e as { country?: string })?.country === 'United States')
    );
  }, [profile.data]);
  const bankGroups = useMemo(() => {
    const out = new Map<string, Awaited<ReturnType<typeof api.answerBank>>>();
    for (const item of bank.data ?? []) {
      if (usCitizen && (item.key === 'work_authorization_us' || item.key === 'us_person_status'))
        continue; // worked out from citizenships
      out.set(item.group, [...(out.get(item.group) ?? []), item]);
    }
    return [...out.entries()];
  }, [bank.data, usCitizen]);
  const values = useMemo(
    () =>
      new Map(
        (profile.data ?? [])
          .filter((item: Profile) => typeof item.value !== 'object')
          .map((item: Profile) => [item.key, item.value === null ? '' : String(item.value)]),
      ),
    [profile.data],
  );
  const raw = useMemo(
    () => new Map((profile.data ?? []).map((item: Profile) => [item.key, item.value as unknown])),
    [profile.data],
  );
  const url = useUrlParams();
  const answerQuery = url.get('aq');
  type AnswerItem = NonNullable<typeof answers.data>[number];
  const answerAccessors = useMemo(
    () => ({
      question: (row: AnswerItem) => row.questionKey,
      answer: (row: AnswerItem) => row.answer,
    }),
    [],
  );
  const allAnswers = useMemo(() => answers.data ?? [], [answers.data]);
  const shownAnswers = useMemo(
    () => allAnswers.filter((row) => matches(answerQuery, row.questionKey, row.answer, row.scope)),
    [allAnswers, answerQuery],
  );
  const pagedAnswers = usePagedSort(shownAnswers, {
    pageSize: 8,
    sort: { key: 'question', dir: 'asc' },
    accessors: answerAccessors,
    resetKey: answerQuery,
  });
  const clearAnswers = () => url.set({ aq: null });

  return (
    <>
      <PageHeading
        title="About me"
        description="Profile details JobPilot uses to fill applications. Personal details save automatically; use Save for education, experience, citizenships and languages."
      />

      <QueryState
        pending={profile.isPending}
        error={profile.error}
        retry={() => {
          void profile.refetch();
        }}
      >
        <div className="mb-6 grid gap-6 lg:grid-cols-2">
          {groups.map((group) => (
            <Panel key={group.title} title={group.title} description={group.text} className="mb-0">
              <div className="grid gap-4 sm:grid-cols-2">
                {group.fields.map((field) => (
                  <ProfileField
                    key={`${field.key}:${values.get(field.key) ?? ''}`}
                    field={field}
                    saved={values.get(field.key) ?? ''}
                    countries={countries.data}
                  />
                ))}
              </div>
            </Panel>
          ))}
        </div>
      </QueryState>

      {bankGroups.length > 0 && (
        <Panel
          id="common-questions"
          title="Common questions"
          description="Pick your answer once. Whatever way a form words the question, JobPilot recognises it and selects the matching option. Questions that depend on the job — how you heard, previous employment at the company, internship, non-compete — and work authorization for countries you hold citizenship of are answered automatically."
        >
          <div className="space-y-6">
            {bankGroups.map(([group, items]) => (
              <div key={group}>
                <h3 className="mb-3 text-xs font-medium text-muted">{group}</h3>
                <div className="grid gap-4 sm:grid-cols-2">
                  {items.map((item) => (
                    <ProfileField
                      key={`${item.key}:${values.get(item.key) ?? ''}`}
                      field={{ key: item.key, title: item.title, options: item.options }}
                      saved={values.get(item.key) ?? ''}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}

      <QueryState
        pending={profile.isPending}
        error={profile.error}
        retry={() => {
          void profile.refetch();
        }}
      >
        <ProfileSection
          key="citizenships"
          id="citizenships"
          value={raw.get('citizenships')}
          legacy={
            values.get('nationality') ? { country: values.get('nationality') ?? '' } : undefined
          }
        />
        <ProfileSection
          key="education"
          id="education"
          value={raw.get('education')}
          legacy={
            values.get('university')
              ? {
                  school: values.get('university') ?? '',
                  degree: values.get('degree') ?? '',
                  field: values.get('major') ?? '',
                  end: /^\d{4}(-\d{2})?$/.test(values.get('graduation') ?? '')
                    ? (values.get('graduation') ?? '')
                    : '',
                  country: values.get('education_country') ?? '',
                  gpa: values.get('gpa') ?? '',
                }
              : undefined
          }
        />
        <ProfileSection key="experience" id="experience" value={raw.get('experience')} />
        <ProfileSection key="languages" id="languages" value={raw.get('languages')} />
      </QueryState>

      <QueryState
        pending={facts.isPending}
        error={facts.error}
        retry={() => {
          void facts.refetch();
        }}
      >
        <Facts facts={facts.data ?? []} />
      </QueryState>

      <Panel
        id="saved-answers"
        title="Saved answers"
        description="Answers to questions that forms ask; reused across applications. Add your own ahead of time."
      >
        <AddAnswer />
        <QueryState
          pending={answers.isPending}
          error={answers.error}
          retry={() => {
            void answers.refetch();
          }}
        >
          {allAnswers.length ? (
            <>
              <div className="mb-3 grid gap-2">
                <SearchField
                  primary
                  hint={false}
                  label="Search saved answers"
                  placeholder="Search questions or answers…"
                  value={answerQuery}
                  onChange={(value) => url.set({ aq: value })}
                />
                <ResultCount
                  shown={shownAnswers.length}
                  total={allAnswers.length}
                  noun={['answer', 'answers']}
                  filtered={!!answerQuery.trim()}
                  onClear={clearAnswers}
                />
              </div>
              {shownAnswers.length ? (
                <>
                  <div
                    className="overflow-x-auto rounded-control border border-line"
                    role="region"
                    aria-label="Saved answers"
                    tabIndex={0}
                  >
                    <table className="w-full border-collapse text-left text-[13px]">
                      <thead className="bg-raised/70">
                        <tr>
                          <SortHeader
                            id="question"
                            title="Question"
                            sort={pagedAnswers.sort}
                            onToggle={pagedAnswers.toggle}
                            className="px-3.5 py-2.5 text-xs font-normal text-muted"
                          />
                          <SortHeader
                            id="answer"
                            title="Answer"
                            sort={pagedAnswers.sort}
                            onToggle={pagedAnswers.toggle}
                            className="px-3.5 py-2.5 text-xs font-normal text-muted"
                          />
                          <th
                            scope="col"
                            className="border-b border-line px-3.5 py-2.5 text-xs font-normal text-muted"
                          >
                            Scope
                          </th>
                          <th scope="col" className="border-b border-line px-3.5 py-2.5">
                            <span className="sr-only">Actions</span>
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {pagedAnswers.rows.map((row) => (
                          <AnswerRow
                            key={`${row.questionKey}:${row.scope}:${row.answer}`}
                            row={row}
                          />
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="pt-2">
                    <Pager
                      page={pagedAnswers.page}
                      pages={pagedAnswers.pages}
                      total={pagedAnswers.total}
                      pageSize={pagedAnswers.pageSize}
                      onPage={pagedAnswers.setPage}
                      label="answers"
                    />
                  </div>
                </>
              ) : (
                <NoMatches noun="answers" query={answerQuery} onClear={clearAnswers} />
              )}
            </>
          ) : (
            <Empty>No saved answers yet. They appear here when questions are answered.</Empty>
          )}
        </QueryState>
      </Panel>
    </>
  );
}

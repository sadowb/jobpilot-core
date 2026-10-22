import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Plus, Trash2 } from 'lucide-react';
import { api } from '../../api/api';
import { useAction } from '../../hooks/useAction';
import { Button, Empty, ErrorMessage, IconButton, Panel, ui } from '../../components/ui';

type Entry = Record<string, string | boolean>;
type SectionId = 'education' | 'experience' | 'languages' | 'citizenships';
type FieldSpec = {
  key: string;
  title: string;
  placeholder?: string;
  wide?: boolean;
  area?: boolean;
  kind?: 'month' | 'country';
};

const SECTIONS: Record<
  SectionId,
  {
    title: string;
    text: string;
    add: string;
    empty: Entry;
    fields: FieldSpec[];
    dated: boolean;
    summary: (entry: Entry, index: number) => string;
  }
> = {
  education: {
    title: 'Education',
    text: 'Every school you attended. The most recent one fills “University”, “Degree”, “Country of education” and similar questions.',
    add: 'Add education',
    dated: true,
    empty: {
      school: '',
      degree: '',
      field: '',
      country: '',
      city: '',
      start: '',
      end: '',
      gpa: '',
      notes: '',
      current: false,
    },
    fields: [
      { key: 'school', title: 'School / university', wide: true },
      { key: 'degree', title: 'Degree', placeholder: 'e.g. BSc' },
      { key: 'field', title: 'Field of study' },
      { key: 'country', title: 'Country of education', kind: 'country' },
      { key: 'city', title: 'City' },
      { key: 'gpa', title: 'GPA', placeholder: 'e.g. 3.6 / 4.0' },
    ],
    summary: (entry) => [entry.degree, entry.field].filter(Boolean).join(' · ') || 'Education',
  },
  experience: {
    title: 'Work experience',
    text: 'Roles you have held. The most recent one answers “Current company” and “Current title”.',
    add: 'Add experience',
    dated: true,
    empty: {
      company: '',
      title: '',
      location: '',
      country: '',
      start: '',
      end: '',
      description: '',
      current: false,
    },
    fields: [
      { key: 'company', title: 'Company', wide: true },
      { key: 'title', title: 'Job title' },
      { key: 'location', title: 'Location' },
      { key: 'country', title: 'Country', kind: 'country' },
      { key: 'description', title: 'What you did', wide: true, area: true },
    ],
    summary: (entry) => String(entry.title || 'Role'),
  },
  citizenships: {
    title: 'Citizenships',
    text: 'Every country you hold citizenship of, primary first. Work-authorization, sponsorship and nationality answers are worked out from this per job, so dual citizens answer correctly for each posting.',
    add: 'Add citizenship',
    dated: false,
    empty: { country: '' },
    fields: [{ key: 'country', title: 'Country', kind: 'country' }],
    summary: (entry, index) =>
      (index === 0 ? 'Primary' : 'Also') + (entry.country ? ` · ${entry.country}` : ''),
  },
  languages: {
    title: 'Languages',
    text: 'Languages you speak and how well.',
    add: 'Add language',
    dated: false,
    empty: { language: '', level: '' },
    fields: [
      { key: 'language', title: 'Language' },
      { key: 'level', title: 'Level', placeholder: 'e.g. Native, Fluent, B2' },
    ],
    summary: (entry) => String(entry.level || 'Language'),
  },
};

const normalise = (value: unknown, id: SectionId): Entry[] =>
  Array.isArray(value)
    ? value
        .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
        .map(
          (item) =>
            ({
              ...SECTIONS[id].empty,
              ...Object.fromEntries(
                Object.entries(item).filter(
                  ([, v]) => typeof v === 'string' || typeof v === 'boolean',
                ),
              ),
            }) as Entry,
        )
    : [];

export function ProfileSection({
  id,
  value,
  legacy,
}: {
  id: SectionId;
  value: unknown;
  legacy?: Entry;
}) {
  const spec = SECTIONS[id];
  const countries = useQuery({
    queryKey: ['countries'],
    queryFn: api.countries,
    staleTime: Infinity,
  });
  const initial = normalise(value, id);
  const seed = !initial.length && legacy ? [{ ...spec.empty, ...legacy }] : initial; // older flat fields become the first entry
  const [entries, setEntries] = useState<Entry[]>(seed);
  const [baseline, setBaseline] = useState<Entry[]>(initial);
  const [seenValue, setSeenValue] = useState(JSON.stringify(value));
  const dirty = JSON.stringify(entries) !== JSON.stringify(baseline);
  const save = useAction((draft: Entry[]) => api.saveSection(id, draft), `${spec.title} saved.`);
  // Refreshed server data can replace a clean form; an unsaved draft belongs to the user.
  if (seenValue !== JSON.stringify(value)) {
    setSeenValue(JSON.stringify(value));
    if (!dirty && !save.isPending) {
      setEntries(seed);
      setBaseline(initial);
    }
  }
  const change = (index: number, key: string, next: string | boolean) => {
    setEntries((previous) =>
      previous.map((entry, at) => (at === index ? { ...entry, [key]: next } : entry)),
    );
  };
  return (
    <Panel
      id={`section-${id}`}
      title={spec.title}
      description={spec.text}
      action={
        <Button
          disabled={save.isPending}
          onClick={() => {
            setEntries((previous) => [...previous, { ...spec.empty }]);
          }}
        >
          <Plus size={15} aria-hidden="true" />
          {spec.add}
        </Button>
      }
    >
      {entries.length === 0 && <Empty>Nothing here yet.</Empty>}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate(entries, {
            onSuccess: (result) => {
              const saved = normalise(result.entries, id);
              setEntries(saved);
              setBaseline(saved);
            },
          });
        }}
      >
        <fieldset disabled={save.isPending}>
          <legend className="sr-only">{spec.title}</legend>
          <ul className="list-none space-y-4">
            {entries.map((entry, index) => (
              <li key={index} className="rounded-control border border-line/70 bg-raised/30 p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <span className="text-[13px] font-medium text-ink">
                    {spec.summary(entry, index)}
                  </span>
                  <IconButton
                    tone="danger"
                    label={`Remove ${spec.title.toLowerCase()} entry ${index + 1}`}
                    title="Remove (saved when you press Save)"
                    onClick={() => {
                      setEntries((previous) => previous.filter((_, at) => at !== index));
                    }}
                  >
                    <Trash2 size={15} aria-hidden="true" />
                  </IconButton>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  {spec.fields.map((field) => (
                    <label
                      key={field.key}
                      className={`${ui.label} ${field.wide ? 'sm:col-span-2' : ''}`}
                    >
                      {field.title}
                      {field.kind === 'country' ? (
                        <select
                          value={String(entry[field.key] ?? '')}
                          onChange={(event) => change(index, field.key, event.target.value)}
                          className={ui.select}
                        >
                          <option value="">Select a country</option>
                          {entry[field.key] &&
                            !(countries.data ?? []).includes(String(entry[field.key])) && (
                              <option>{String(entry[field.key])}</option>
                            )}
                          {(countries.data ?? []).map((name) => (
                            <option key={name}>{name}</option>
                          ))}
                        </select>
                      ) : field.area ? (
                        <textarea
                          rows={3}
                          maxLength={3000}
                          value={String(entry[field.key] ?? '')}
                          onChange={(event) => change(index, field.key, event.target.value)}
                          className={ui.textarea}
                        />
                      ) : (
                        <input
                          maxLength={200}
                          value={String(entry[field.key] ?? '')}
                          placeholder={field.placeholder}
                          onChange={(event) => change(index, field.key, event.target.value)}
                          className={ui.input}
                        />
                      )}
                    </label>
                  ))}
                  {spec.dated && (
                    <>
                      <label className={ui.label}>
                        Start
                        <input
                          type="month"
                          value={String(entry.start ?? '')}
                          onChange={(event) => change(index, 'start', event.target.value)}
                          className={ui.input}
                        />
                      </label>
                      <label className={ui.label}>
                        End
                        <input
                          type="month"
                          disabled={entry.current === true}
                          value={String(entry.end ?? '')}
                          onChange={(event) => change(index, 'end', event.target.value)}
                          className={ui.input}
                        />
                      </label>
                      <label className={`${ui.checkLabel} sm:col-span-2`}>
                        <input
                          type="checkbox"
                          className={ui.checkbox}
                          checked={entry.current === true}
                          onChange={(event) => change(index, 'current', event.target.checked)}
                        />
                        {id === 'education' ? 'I am still studying here' : 'I currently work here'}
                      </label>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </fieldset>
        <ErrorMessage error={save.error} />
        <div className="mt-4 flex items-center gap-3">
          <Button
            type="submit"
            variant="primary"
            loading={save.isPending}
            disabled={!dirty || save.isPending}
          >
            Save {spec.title.toLowerCase()}
          </Button>
          {save.isSuccess && !dirty && <span className={ui.success}>Saved</span>}
          {dirty && !save.isPending && (
            <span className="text-xs text-warn" role="status">
              Unsaved changes
            </span>
          )}
        </div>
      </form>
    </Panel>
  );
}

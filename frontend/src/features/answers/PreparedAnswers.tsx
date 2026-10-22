import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/api';
import type { Answer } from '../../api/models';
import { useAction } from '../../hooks/useAction';
import { Button, Chip, Panel, ui } from '../../components/ui';

const SKIPPED = '(skipped by owner)';
const normalize = (text: string) =>
  text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
const AGENT_SOURCES = ['agent (posting form)', 'default (voluntary self-identification)'];

// What the agent drafted for this one application, readable before any browser opens. An edit is saved under the same
// job-scoped key, so it replaces the draft and the next fill uses it; the agent never overwrites it.
export function PreparedAnswers({
  jobId,
  labels,
}: {
  jobId: string;
  labels: Record<string, string>;
}) {
  const scope = `job:${jobId}`;
  const query = useQuery({ queryKey: ['answers'], queryFn: api.answers });
  const rows = (query.data ?? []).filter((row) => row.scope === scope && row.answer !== SKIPPED);
  if (!rows.length) return null;
  return (
    <Panel title="Answers prepared for this application">
      <p className="mb-3 text-[13px] leading-relaxed text-muted">
        Drafted from the posting&apos;s questions, your profile and your CV before the browser
        opens. Edit any of them; your version is what gets filled in.
      </p>
      <ul className="list-none" aria-label="Prepared answers">
        {rows.map((row) => (
          <PreparedRow key={row.questionKey} row={row} scope={scope} labels={labels} />
        ))}
      </ul>
    </Panel>
  );
}

function PreparedRow({
  row,
  scope,
  labels,
}: {
  row: Answer;
  scope: string;
  labels: Record<string, string>;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(row.answer);
  const save = useAction(
    () => api.saveAnswer(row.questionKey, value.trim(), scope),
    'Answer saved. It will be used the next time the application is filled in.',
  );
  const label = labels[normalize(row.questionKey)] ?? row.questionKey;
  const agent = AGENT_SOURCES.includes(row.source);
  const long = row.answer.length > 90;
  return (
    <li className="border-b border-line/60 py-3.5 last:border-0" data-testid="prepared-answer">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1.5">
        <p className="min-w-0 flex-1 basis-[220px] text-xs font-medium text-muted">
          {label.charAt(0).toUpperCase() + label.slice(1)}
        </p>
        <Chip tone={agent ? 'warn' : 'neutral'}>
          {agent ? 'Drafted by the agent' : 'Edited by you'}
        </Chip>
      </div>
      {editing ? (
        <form
          className="mt-2 flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (!value.trim()) return;
            save.mutate(undefined, { onSuccess: () => setEditing(false) });
          }}
        >
          {long || value.includes('\n') ? (
            <textarea
              className={ui.textarea}
              rows={5}
              value={value}
              aria-label={`Answer to ${label}`}
              onChange={(event) => setValue(event.target.value)}
            />
          ) : (
            <input
              className={ui.input}
              value={value}
              aria-label={`Answer to ${label}`}
              onChange={(event) => setValue(event.target.value)}
            />
          )}
          <div className="flex gap-2">
            <Button
              type="submit"
              variant="primary"
              loading={save.isPending}
              disabled={!value.trim()}
            >
              Save answer
            </Button>
            <Button
              variant="ghost"
              disabled={save.isPending}
              onClick={() => {
                setValue(row.answer);
                setEditing(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <div className="mt-1 flex flex-wrap items-start justify-between gap-3">
          <p className="min-w-0 flex-1 basis-[240px] whitespace-pre-wrap text-[13.5px] font-medium text-ink [overflow-wrap:anywhere]">
            {row.answer}
          </p>
          <Button variant="secondary" onClick={() => setEditing(true)}>
            Edit
          </Button>
        </div>
      )}
    </li>
  );
}

export const inventoryLabels = (
  inventory: ReadonlyArray<Record<string, unknown>> | undefined,
): Record<string, string> => {
  const out: Record<string, string> = {};
  for (const field of inventory ?? []) {
    if (typeof field.label === 'string') out[normalize(field.label)] = field.label;
  }
  return out;
};

import { useState } from 'react';
import type { JobDetail } from '../../api/models';
import { api } from '../../api/api';
import { useAction } from '../../hooks/useAction';
import { finalStatuses } from '../../utils/format';
import { eventSentence, focusMessage, relativeTime } from '../../utils/humanize';
import { Button, MutationFeedback, Panel, ui } from '../../components/ui';

export function AgentGuidance({ job }: { job: JobDetail }) {
  const [message, setMessage] = useState('');
  const send = useAction(
    () => api.guideAgent(job.id, message),
    job.status === 'awaiting_approval'
      ? 'Sent back to the agent with your note. It will continue in the existing tab.'
      : 'Guidance saved. The agent will continue in the existing tab.',
  );
  const focus = useAction(() => api.focusJobTab(job.id), focusMessage);
  const events = job.events
    .filter((event) =>
      [
        'agent_guidance',
        'agent_progress',
        'direct_result',
        'browser_attempt',
        'wait',
        'agent_stalled',
        'bulk_fill',
        'agent_timing',
        'cv_track_selected',
      ].includes(event.kind),
    )
    .slice(0, 8);
  const finished = finalStatuses.includes(job.status);
  const awaiting = job.status === 'awaiting_approval';
  const submitting = job.events.some((event) => event.kind === 'submit_clicked');
  return (
    <Panel
      title="Agent progress and guidance"
      description="See what the agent is doing and give it a useful hint when it gets stuck."
    >
      <p className="mb-3 text-xs text-muted">
        {finished
          ? 'This application is complete. Previous progress is shown below.'
          : job.blocked_reason ||
            'The agent will inspect the existing form and preserve your filled answers.'}
      </p>
      <ol className="mb-4 list-none space-y-2" aria-live="polite">
        {events.map((event, index) => (
          <li
            key={`${event.ts}-${index}`}
            className="rounded-control border border-line p-3 text-[13px]"
          >
            <p className="whitespace-pre-wrap break-words">
              {eventSentence(event.kind, (event.payload ?? {}) as Record<string, unknown>)}
            </p>
            <time className="text-xs text-muted" dateTime={event.ts}>
              {relativeTime(event.ts)}
            </time>
          </li>
        ))}
      </ol>
      {job.tabOpen && (
        <Button className="mb-4" onClick={() => focus.mutate()} disabled={focus.isPending}>
          Find application tab
        </Button>
      )}
      <MutationFeedback error={focus.error} success={false} />
      {!finished && !submitting && (
        <form
          className={ui.stack}
          onSubmit={(event) => {
            event.preventDefault();
            send.mutate(undefined, { onSuccess: () => setMessage('') });
          }}
        >
          <label className={ui.label}>
            Your guidance
            <textarea
              className={ui.textarea}
              rows={3}
              maxLength={2000}
              required
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="For example: I dismissed the popup. Continue with the existing form."
            />
          </label>
          <p className="text-xs text-muted">
            {awaiting
              ? 'The application goes back to the agent, which continues from the parked tab with your note. You approve again afterwards.'
              : 'The current preparation run stops, then continues with your guidance.'}{' '}
            Leave passwords and verification codes out.
          </p>
          <MutationFeedback error={send.error} success={send.isSuccess} message="Guidance sent." />
          <div>
            <Button
              type="submit"
              variant="primary"
              loading={send.isPending}
              disabled={send.isPending || !message.trim()}
            >
              {awaiting ? 'Send back to the agent with this note' : 'Send guidance and continue'}
            </Button>
          </div>
        </form>
      )}
    </Panel>
  );
}

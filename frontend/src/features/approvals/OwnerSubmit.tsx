import { useState } from 'react';
import { ExternalLink } from 'lucide-react';
import { api } from '../../api/api';
import { useAction } from '../../hooks/useAction';
import { focusMessage } from '../../utils/humanize';
import type { ParkSignal } from '../../api/models';
import { Button, ErrorMessage, LinkButton } from '../../components/ui';
import { ConfirmDialog } from '../../components/ui/Dialog';

/**
 * The agent never clicks Submit (ADR 0026). The owner reviews the filled tab in Neo, clicks Submit there, then says so
 * here: JobPilot reads the tab once (read-only) for a confirmation page and records the application.
 */
export function OwnerSubmitActions({
  jobId,
  signal = null,
}: {
  jobId: string;
  signal?: ParkSignal | null;
}) {
  const [elsewhere, setElsewhere] = useState(false);
  const [sendBack, setSendBack] = useState(false);
  const [note, setNote] = useState('');
  const findTab = useAction(() => api.focusJobTab(jobId), focusMessage);
  const submitted = useAction(
    () => api.ownerSubmitted(jobId),
    (result) =>
      result.already
        ? 'This application was already recorded as submitted.'
        : result.confirmationSeen
          ? 'Recorded as submitted. A confirmation page was seen.'
          : 'Recorded as submitted. No confirmation page was seen, so check your email.',
  );
  const marked = useAction(() => api.confirmJobSubmitted(jobId), 'Marked as submitted.');
  const notYet = useAction(
    () => (signal === 'tab_closed' ? api.reopenJob(jobId) : api.dismissParkSignal(jobId)),
    signal === 'tab_closed'
      ? 'Preparing the application again in a fresh tab.'
      : 'Okay, still waiting for you to submit.',
  );
  const notFinished = useAction(
    () => (note.trim() ? api.guideAgent(jobId, note.trim()) : api.reopenJob(jobId)),
    'Sent back to the agent. It continues from the parked tab; you approve again afterwards.',
  );
  const emailCode = useAction(
    () => api.requestEmailCode(jobId),
    (result) =>
      result.started
        ? 'The agent is reading the code from Gmail and will type it into the application tab.'
        : 'A code run is already in progress for this application.',
  );
  const busy =
    emailCode.isPending ||
    notFinished.isPending ||
    submitted.isPending ||
    marked.isPending ||
    notYet.isPending;
  return (
    <div className="grid gap-2.5">
      {signal && (
        <div
          role="status"
          className="grid gap-2 rounded-control border border-warn/40 bg-warn/10 p-3 text-[13px] leading-relaxed text-ink"
        >
          <p className="font-medium">
            {signal === 'tab_closed'
              ? 'The application tab was closed. Did you submit?'
              : 'This looks submitted. Can you confirm?'}
          </p>
          <p className="text-muted">
            {signal === 'tab_closed'
              ? 'JobPilot cannot tell whether you clicked Submit before closing it.'
              : 'The page changed, but no confirmation message was recognised.'}
          </p>
          <div className="flex flex-wrap gap-2 max-[560px]:[&>button]:w-full">
            <Button
              variant="primary"
              disabled={busy}
              loading={submitted.isPending}
              onClick={() => submitted.mutate()}
            >
              Yes, I submitted it
            </Button>
            <Button disabled={busy} loading={notYet.isPending} onClick={() => notYet.mutate()}>
              {signal === 'tab_closed' ? 'No, reopen it' : 'Not yet'}
            </Button>
          </div>
          <ErrorMessage error={notYet.error} />
        </div>
      )}
      <ol className="list-decimal space-y-1 pl-5 text-[13px] leading-relaxed text-muted">
        <li>Open the application tab in Neo and check it.</li>
        <li>Click Submit there yourself. The agent never does.</li>
        <li>
          JobPilot notices a confirmation page by itself; if it does not, press “I submitted it”.
        </li>
      </ol>
      <div className="flex flex-wrap items-center gap-2 max-[560px]:[&>button]:w-full">
        <Button disabled={busy} loading={findTab.isPending} onClick={() => findTab.mutate()}>
          <ExternalLink size={15} aria-hidden="true" />
          Find agent tab
        </Button>
        <Button
          variant="primary"
          disabled={busy}
          loading={submitted.isPending}
          onClick={() => submitted.mutate()}
        >
          I submitted it
        </Button>
        <Button disabled={busy} loading={emailCode.isPending} onClick={() => emailCode.mutate()}>
          Get the email code from Gmail
        </Button>
        <LinkButton disabled={busy} onClick={() => setElsewhere(true)}>
          Mark as submitted elsewhere
        </LinkButton>
        <LinkButton disabled={busy} onClick={() => setSendBack((open) => !open)}>
          Not finished — send back to the agent
        </LinkButton>
      </div>
      {sendBack && (
        <form
          className="grid gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            notFinished.mutate(undefined, {
              onSuccess: () => {
                setSendBack(false);
                setNote('');
              },
            });
          }}
        >
          <label className="grid gap-1 text-[13px] text-muted">
            Note for the agent (optional)
            <textarea
              rows={2}
              maxLength={2000}
              value={note}
              disabled={busy}
              onChange={(event) => setNote(event.target.value)}
              placeholder="For example: fill every required field; do not use the saved email."
              className="rounded-control border border-line bg-surface p-2 text-ink"
            />
          </label>
          <div>
            <Button type="submit" variant="primary" disabled={busy} loading={notFinished.isPending}>
              Send back to the agent
            </Button>
          </div>
        </form>
      )}
      <ErrorMessage
        error={submitted.error ?? findTab.error ?? notFinished.error ?? emailCode.error}
      />
      {elsewhere && (
        <ConfirmDialog
          title="Mark as submitted elsewhere"
          tone="primary"
          confirmLabel="Mark as submitted"
          pendingLabel="Recording…"
          pending={marked.isPending}
          error={marked.error}
          onClose={() => setElsewhere(false)}
          onConfirm={() => marked.mutate(undefined, { onSuccess: () => setElsewhere(false) })}
        >
          Use this if you submitted this application outside the tab JobPilot opened, for example on
          the employer’s site. Nothing is sent from here.
        </ConfirmDialog>
      )}
    </div>
  );
}

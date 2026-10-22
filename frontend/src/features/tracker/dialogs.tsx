import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/api';
import { Dialog } from '../../components/ui/Dialog';
import { Button, Chip, ErrorMessage, LinkButton, ui } from '../../components/ui';
import { cvFileName } from '../../utils/humanize';
import { useAction } from '../../hooks/useAction';
import { friendlyError } from '../../utils/humanize';
import { sendInChunks, shortLink } from '../../utils/links';
import type { IngestResult, LoggedApplication } from '../../api/models';
import { today } from './dates';
import { LinkPasteField, useLinkPaste } from './LinkPaste';

export function LogApplied({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const paste = useLinkPaste();
  const lines = paste.links;
  const [appliedOn, setAppliedOn] = useState(today());
  const [via, setVia] = useState('');
  const [progress, setProgress] = useState<[number, number] | null>(null);
  const action = useAction(
    () =>
      sendInChunks<LoggedApplication>(
        lines,
        (batch) =>
          api.logApplied({
            urls: batch,
            appliedOn,
            ...(via.trim() ? { submittedVia: via.trim() } : {}),
          }),
        (url, message) => ({ url, error: friendlyError(new Error(message)) }),
        (done, total) => setProgress([done, total]),
      ),
    (results) =>
      `${results.filter((item) => !item.error).length} application${results.length === 1 ? '' : 's'} added.`,
  );
  if (action.data) {
    const failed = action.data.filter((item) => item.error).length;
    return (
      <Dialog title="Added to your applications" onClose={onClose}>
        <ul className="list-none" aria-label="Results">
          {action.data.map((item, index) => (
            <li
              key={index}
              className="flex flex-wrap items-center justify-between gap-3 border-b border-line py-3 last:border-0"
            >
              <span className="min-w-0 flex-1 basis-60 [overflow-wrap:anywhere] text-[13px] text-muted">
                {item.url}
              </span>
              {item.error ? (
                <Chip tone="danger">Couldn’t add</Chip>
              ) : item.created === false ? (
                <Chip tone="info">Already tracked, updated</Chip>
              ) : item.needsReview ? (
                <Chip tone="warn" dot>
                  Couldn’t read this page cleanly — nothing was guessed
                </Chip>
              ) : item.filled ? (
                <Chip tone="ok" dot>
                  Filled in automatically
                </Chip>
              ) : (
                <Chip tone="warn" dot>
                  Added — please add the title
                </Chip>
              )}
              {item.id && (
                <LinkButton
                  onClick={() => {
                    onClose();
                    navigate(`/jobs/${encodeURIComponent(item.id!)}`);
                  }}
                >
                  Open
                </LinkButton>
              )}
            </li>
          ))}
        </ul>
        {failed > 0 && (
          <p className="mt-3 text-[13px] text-danger">
            {failed} link{failed === 1 ? '' : 's'} could not be added. Check the address and try
            again.
          </p>
        )}
        <div className={ui.dialogActions}>
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        </div>
      </Dialog>
    );
  }
  return (
    <Dialog title="Save job" onClose={onClose}>
      <div className="mx-auto max-w-[420px]">
        <div className="mb-4 rounded-control border border-sage/40 bg-sage/20 px-3 py-2 text-xs text-ink dark:bg-sage/10">
          <span className="font-medium">Quick save: </span>
          <span className="text-secondary">
            Paste a job posting URL. Haiku reads the page and fills in company, role, location and
            description.
          </span>
        </div>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            action.mutate();
          }}
        >
          <LinkPasteField
            paste={paste}
            label="Job URLs"
            autoFocus
            rows={3}
            placeholder="https://job-boards.greenhouse.io/company/jobs/123"
            textareaClassName="text-xs"
          />
          <div className="grid gap-3 sm:grid-cols-2">
            <label className={ui.label}>
              <span className="text-xs text-muted">Date applied</span>
              <input
                type="date"
                value={appliedOn}
                max={today()}
                onChange={(event) => setAppliedOn(event.target.value)}
                className={ui.input}
              />
            </label>
            <label className={ui.label}>
              <span className="text-xs text-muted">Applied via (optional)</span>
              <input
                value={via}
                onChange={(event) => setVia(event.target.value)}
                placeholder="Company site, referral…"
                className={ui.input}
              />
            </label>
          </div>
          <ErrorMessage error={action.error} />
          <div className="mt-2 flex flex-col gap-2">
            <Button
              type="submit"
              variant="primary"
              loading={action.isPending}
              disabled={!lines.length || action.isPending}
              className="w-full"
            >
              {action.isPending
                ? progress && progress[1] > 1
                  ? `Reading postings… ${progress[0]} of ${progress[1]}`
                  : 'Reading posting…'
                : lines.length > 1
                  ? `Save ${lines.length} jobs`
                  : 'Save job'}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={onClose}
              className="w-full justify-center text-xs"
            >
              Cancel
            </Button>
          </div>
        </form>
      </div>
    </Dialog>
  );
}

export function ImportSheet({ onClose }: { onClose: () => void }) {
  const [text, setText] = useState('');
  const action = useAction(
    () => api.importTracker(text),
    (result) =>
      `Imported ${result.created} new and updated ${result.updated} of ${result.rows} rows${result.errors.length ? ` (${result.errors.length} skipped)` : ''}.`,
  );
  const readFile = async (file: File | undefined) => {
    if (file) setText(await file.text());
  };
  return (
    <Dialog title="Import your spreadsheet" onClose={onClose}>
      <form
        className={ui.stack}
        onSubmit={(event) => {
          event.preventDefault();
          action.mutate(undefined, { onSuccess: onClose });
        }}
      >
        <p className="text-[13px] leading-relaxed text-muted">
          Copy your spreadsheet (with header row) and paste it here, or choose a CSV file. Columns
          are mapped automatically.
        </p>
        <label className={ui.label}>
          Choose a file
          <input
            type="file"
            accept=".csv,.tsv,.txt"
            onChange={(event) => {
              void readFile(event.target.files?.[0]);
            }}
            className={ui.input}
          />
        </label>
        <label className={ui.label}>
          Or paste raw data
          <textarea
            rows={7}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="Date Resume Submitted	Current Status	Company	Job Title…"
            className={`${ui.textarea} font-mono text-xs`}
          />
        </label>
        <ErrorMessage error={action.error} />
        <div className={ui.dialogActions}>
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            loading={action.isPending}
            disabled={!text.trim() || action.isPending}
          >
            Import rows
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

export function AddForJobPilot({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const paste = useLinkPaste();
  const lines = paste.links;
  const [sent, setSent] = useState<string[]>([]);
  const [progress, setProgress] = useState<[number, number] | null>(null);
  const action = useAction(
    () => {
      setSent(lines);
      return sendInChunks<IngestResult>(
        lines,
        (batch) => api.ingest({ urls: batch, cvMode: 'base' }),
        (url, message) => ({ url, error: friendlyError(new Error(message)) }),
        (done, total) => setProgress([done, total]),
      );
    },
    (results) => {
      const queued = results.filter((item) => !('error' in item)).length;
      return `${queued} job${queued === 1 ? '' : 's'} queued for JobPilot.`;
    },
  );
  if (action.data) {
    const rows = action.data.map((result, index) => ({ link: sent[index] ?? '', result }));
    const failed = rows.filter((row) => 'error' in row.result).length;
    return (
      <Dialog title="Sent to JobPilot" onClose={onClose}>
        <ul className="list-none" aria-label="Results">
          {rows.map(({ link, result }, index) => (
            <li
              key={index}
              className="flex flex-wrap items-center justify-between gap-3 border-b border-line py-3 last:border-0"
            >
              <span
                className="min-w-0 flex-1 basis-60 [overflow-wrap:anywhere] text-[13px] text-muted"
                title={link}
              >
                {shortLink(link, 90)}
              </span>
              {'error' in result ? (
                <Chip tone="danger">Couldn’t add</Chip>
              ) : result.created ? (
                <Chip tone="ok" dot>
                  Added
                </Chip>
              ) : (
                <Chip tone="info">Already tracked</Chip>
              )}
              {'id' in result && (
                <LinkButton
                  onClick={() => {
                    onClose();
                    navigate(`/jobs/${encodeURIComponent(result.id)}`);
                  }}
                >
                  Open
                </LinkButton>
              )}
            </li>
          ))}
        </ul>
        {failed > 0 && (
          <p className="mt-3 text-[13px] text-danger">
            {failed} link{failed === 1 ? '' : 's'} could not be added. Check the address and try
            again.
          </p>
        )}
        <div className={ui.dialogActions}>
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        </div>
      </Dialog>
    );
  }
  return (
    <Dialog title="Apply for me" onClose={onClose}>
      <form
        className={ui.stack}
        onSubmit={(event) => {
          event.preventDefault();
          action.mutate();
        }}
      >
        <p className="text-[13px] leading-relaxed text-muted">
          Paste job links. JobPilot reads each posting, fills the application with your profile and
          CV “{cvFileName}”, and stops for your approval before anything is sent.
        </p>
        <LinkPasteField
          paste={paste}
          label="Job links"
          autoFocus
          rows={5}
          placeholder="https://jobs.lever.co/company/…"
        />
        <ErrorMessage error={action.error} />
        <div className={ui.dialogActions}>
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            loading={action.isPending}
            disabled={!lines.length || action.isPending}
          >
            {action.isPending && progress && progress[1] > 1
              ? `Sending… ${progress[0]} of ${progress[1]}`
              : `Queue ${lines.length || ''} job${lines.length === 1 ? '' : 's'}`}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

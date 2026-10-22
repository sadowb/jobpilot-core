import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Copy, Trash2 } from 'lucide-react';
import { api } from '../../api/api';
import type { DuplicateGroup } from '../../api/models';
import { date } from '../../utils/format';
import { companyName } from '../../utils/humanize';
import { useAction } from '../../hooks/useAction';
import { ConfirmDialog, Dialog } from '../../components/ui/Dialog';
import { Button, Chip, ErrorMessage, IconButton, ui } from '../../components/ui';

// One group of likely-duplicate applications: pick the one to keep, the rest fold into it.
function GroupRow({ group }: { group: DuplicateGroup }) {
  const [keep, setKeep] = useState(group.rows[0]?.id ?? '');
  const merge = useAction(
    () =>
      api.mergeJobs(
        keep,
        group.rows.filter((row) => row.id !== keep).map((row) => row.id),
      ),
    'Merged. The duplicates were moved to Deleted.',
  );
  return (
    <li className="rounded-control border border-line p-4">
      <p className="mb-3 font-semibold [overflow-wrap:anywhere]">
        {companyName(group.rows[0]?.company)} · {group.rows[0]?.role}
      </p>
      <fieldset className="grid gap-2">
        <legend className="sr-only">Which one to keep</legend>
        {group.rows.map((row) => (
          <label
            key={row.id}
            className={`flex cursor-pointer items-start gap-3 rounded-control border p-3 text-[13px] ${keep === row.id ? 'border-accent bg-accent-soft' : 'border-line'}`}
          >
            <input
              type="radio"
              name={`keep-${group.key}`}
              checked={keep === row.id}
              onChange={() => setKeep(row.id)}
              className="mt-1"
            />
            <span className="min-w-0 flex-1">
              <span className="block [overflow-wrap:anywhere] text-muted">{row.url}</span>
              <span className="mt-1 flex flex-wrap gap-2">
                <Chip>{row.appliedOn ? `Applied ${date(row.appliedOn)}` : 'Not applied'}</Chip>
                <Chip>{row.status.replaceAll('_', ' ')}</Chip>
              </span>
            </span>
            {keep === row.id && <Chip tone="ok">Keep</Chip>}
          </label>
        ))}
      </fieldset>
      <ErrorMessage error={merge.error} />
      <div className="mt-3 flex justify-end">
        <Button
          variant="primary"
          loading={merge.isPending}
          disabled={merge.isPending || group.rows.length < 2}
          onClick={() => merge.mutate()}
        >
          Merge {group.rows.length} into one
        </Button>
      </div>
    </li>
  );
}

export function DuplicatesBanner() {
  const [open, setOpen] = useState(false);
  const groups = useQuery({ queryKey: ['duplicates'], queryFn: api.duplicates });
  const list = groups.data ?? [];
  if (!list.length) return null;
  return (
    <>
      <div
        className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-card border border-line bg-surface px-5 py-4 shadow-card"
        role="status"
      >
        <p className="flex items-center gap-2 font-medium">
          <Copy size={17} className="text-muted" aria-hidden="true" />
          {list.length} possible duplicate{list.length === 1 ? '' : 's'} found
        </p>
        <Button onClick={() => setOpen(true)}>Review</Button>
      </div>
      {open && (
        <Dialog title="Possible duplicates" onClose={() => setOpen(false)}>
          <p className="mb-4 leading-relaxed text-muted">
            These look like the same job. Pick the one to keep; the others merge into it and move to
            Deleted, so nothing is lost.
          </p>
          <ul className="grid list-none gap-4">
            {list.map((group) => (
              <GroupRow key={group.key} group={group} />
            ))}
          </ul>
          <div className={ui.dialogActions}>
            <Button onClick={() => setOpen(false)}>Close</Button>
          </div>
        </Dialog>
      )}
    </>
  );
}

// Delete with a clear confirmation; deleting hides the application and it can be restored.
// The wrapper stops clicks inside the (portal-less) dialog from bubbling to a table row that opens the peek panel.
export function DeleteApplication({
  id,
  title,
  onDone,
  compact = false,
}: {
  id: string;
  title: string;
  onDone?: () => void;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const remove = useAction(
    () => api.archiveJob(id),
    'Deleted. You can restore it from the Deleted view.',
  );
  return (
    <span className="contents" onClick={(event) => event.stopPropagation()}>
      {compact ? (
        <IconButton
          tone="danger"
          label={`Delete ${title}`}
          title="Delete"
          onClick={() => {
            remove.reset();
            setOpen(true);
          }}
        >
          <Trash2 size={15} aria-hidden="true" />
        </IconButton>
      ) : (
        <Button
          className="text-danger"
          onClick={() => {
            remove.reset();
            setOpen(true);
          }}
        >
          <Trash2 size={15} aria-hidden="true" />
          Delete application
        </Button>
      )}
      {open && (
        <ConfirmDialog
          title="Delete this application?"
          confirmLabel="Delete"
          pending={remove.isPending}
          error={remove.error}
          onClose={() => setOpen(false)}
          onConfirm={() =>
            remove.mutate(undefined, {
              onSuccess: () => {
                setOpen(false);
                onDone?.();
              },
            })
          }
        >
          <p>
            <strong>{title}</strong> will be removed from your applications. It moves to{' '}
            <em>Deleted</em>, where you can restore it or remove it for good.
          </p>
        </ConfirmDialog>
      )}
    </span>
  );
}

export function RestoreOrPurge({ id, title }: { id: string; title: string }) {
  const [confirm, setConfirm] = useState(false);
  const restore = useAction(() => api.restoreJob(id), 'Restored.');
  const purge = useAction(() => api.purgeJob(id), 'Removed for good.');
  return (
    <div
      className="flex flex-wrap items-center justify-end gap-2 [&_button]:whitespace-nowrap"
      onClick={(event) => event.stopPropagation()}
    >
      <Button
        loading={restore.isPending}
        disabled={restore.isPending}
        onClick={() => restore.mutate()}
      >
        Restore
      </Button>
      <Button
        variant="ghost"
        className="text-danger"
        onClick={() => {
          purge.reset();
          setConfirm(true);
        }}
      >
        Remove for good
      </Button>
      {confirm && (
        <ConfirmDialog
          title="Remove for good?"
          confirmLabel="Remove for good"
          pending={purge.isPending}
          error={purge.error}
          onClose={() => setConfirm(false)}
          onConfirm={() => purge.mutate(undefined, { onSuccess: () => setConfirm(false) })}
        >
          <p>
            <strong>{title}</strong> and its history will be permanently erased. This cannot be
            undone.
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}

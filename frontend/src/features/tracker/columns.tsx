import { memo, useState, type ReactNode } from 'react';
import { ExternalLink } from 'lucide-react';
import type { TrackerRow } from '../../api/models';
import { StatusPicker } from '../../components/ui/StatusPicker';
import { StatusChip, buttonSmall, ui } from '../../components/ui';
import { cn } from '../../utils/cn';
import { DeleteApplication, RestoreOrPurge } from './Library';
import { date, label, safeUrl } from '../../utils/format';
import { companyName, cvFileName, trackName } from '../../utils/humanize';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/api';
import { CvPreview } from '../cv/CvPreview';
import { useTracking } from './useTracking';

const dash = <span className="text-muted/60">—</span>;

// Click-to-edit date: plain text (or a quiet "+ Add"), a date input on click, saves on change.
function DateCell({
  row,
  field,
  value,
}: {
  row: TrackerRow;
  field: 'date_contacted' | 'interview_on' | 'follow_up_on';
  value: string | null;
}) {
  const [editing, setEditing] = useState(false);
  const save = useTracking(row.id);
  const name =
    field === 'date_contacted'
      ? 'contacted date'
      : field === 'interview_on'
        ? 'interview date'
        : 'follow-up date';
  if (editing)
    return (
      <input
        type="date"
        autoFocus
        aria-label={`${name} for ${row.role}`}
        defaultValue={value ?? ''}
        disabled={save.isPending}
        onClick={(event) => event.stopPropagation()}
        onBlur={() => setEditing(false)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.stopPropagation();
            setEditing(false);
          }
        }}
        onChange={(event) => {
          const next = event.target.value; // the browser emits partial years (0002-…) while typing; only commit a complete, plausible date
          if (/^\d{4}-\d{2}-\d{2}$/.test(next) && Number(next.slice(0, 4)) >= 2000)
            save.mutate({ [field]: next }, { onSettled: () => setEditing(false) });
        }}
        className="w-[8.5rem] rounded-control border border-line bg-surface px-2 py-1 text-[13px] tabular-nums text-ink shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      />
    );
  return (
    <button
      type="button"
      aria-label={`${value ? 'Change' : 'Add'} ${name} for ${row.role}`}
      onClick={(event) => {
        event.stopPropagation();
        setEditing(true);
      }}
      className={`inline-flex min-h-7 max-[560px]:min-h-[44px] items-center whitespace-nowrap rounded-md px-2 py-0.5 text-left text-[13px] tabular-nums transition-colors hover:bg-raised hover:ring-1 hover:ring-line/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${value ? 'text-ink' : 'text-muted/60 hover:text-muted'}`}
    >
      {value ? date(value) : <span className="text-xs text-muted/60">+ Add</span>}
    </button>
  );
}

function StatusCell({ row }: { row: TrackerRow }) {
  const save = useTracking(row.id, 'Status updated.');
  if (row.source === 'platform') return <StatusChip status={row.status} />;
  const note =
    row.userStatus && row.userStatus.toLowerCase() !== label(row.status).toLowerCase()
      ? row.userStatus
      : null;
  return (
    <div className="flex min-w-0 flex-col items-start gap-0.5">
      <StatusPicker
        value={row.status}
        subject={row.role}
        disabled={save.isPending}
        onChange={(status) => save.mutate({ status })}
      />
      {note && (
        <span className="max-w-[10rem] truncate pl-1 text-[11px] text-muted" title={note}>
          {note}
        </span>
      )}
    </div>
  );
}

function PostingLink({ url }: { url: string }) {
  const link = safeUrl(url);
  if (!link) return dash;
  const hostname = new URL(link).hostname;
  if (hostname.endsWith('.invalid')) return dash;
  return (
    <a
      href={link}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(event) => event.stopPropagation()}
      title={`Open the job posting (${hostname})`}
      className={cn(ui.buttonLink, buttonSmall, 'gap-1.5 hover:border-accent hover:text-accent')}
    >
      <span className="max-w-[9rem] truncate">
        {hostname.replace(/^(www|jobs|job-boards|boards|careers)\./, '')}
      </span>
      <ExternalLink size={12} aria-hidden="true" />
    </a>
  );
}

// Which kind of CV went out. The file the employer receives is always "Abdelhadi Marjane.pdf" (see title).
function ResumeCell({ row }: { row: TrackerRow }) {
  const tracks = useQuery({ queryKey: ['tracks'], queryFn: api.tracks, staleTime: Infinity });
  const name = row.cvTrack ? trackName(row.cvTrack, tracks.data) : null;
  if (!name && !row.resumeLabel) return dash;
  const mode = row.cvTrack ? (row.cvMode === 'tailor' ? 'Tailored for this job' : 'Base CV') : null;
  return (
    <div className="flex min-w-0 items-center gap-1" title={`Sent as ${cvFileName}`}>
      <div className="min-w-0 flex-1">
        <span className="block truncate text-[13px] text-ink">{name ?? row.resumeLabel}</span>
        <span className="block truncate text-xs text-muted">
          {name && row.resumeLabel && row.resumeLabel !== name ? row.resumeLabel : mode}
        </span>
      </div>
      {row.cvTrack && <CvPreview job={row.id} compact />}
    </div>
  );
}

function Details({ row }: { row: TrackerRow }) {
  if (row.missing.length)
    return row.waitingOn === 'agent' ? (
      <span className="inline-flex items-center gap-2 whitespace-nowrap text-xs text-info">
        <span className="size-1.5 animate-pulse rounded-full bg-info" aria-hidden="true" />
        Reading the page…
      </span>
    ) : (
      <span
        title={`Missing: ${row.missing.join(', ')}`}
        className="inline-flex items-center gap-2 whitespace-nowrap text-xs text-warn"
      >
        <span className="size-1.5 rounded-full bg-warn" aria-hidden="true" />
        {row.missing.length === 1
          ? `${row.missing[0][0].toUpperCase()}${row.missing[0].slice(1)} missing`
          : `${row.missing.length} details missing`}
      </span>
    );
  const seen = new Set<string>();
  const parts = [row.location, row.workMode, row.employmentType].filter(
    (value): value is string =>
      !!value && !seen.has(value.toLowerCase()) && !!seen.add(value.toLowerCase()),
  );
  return parts.length ? (
    <span className="line-clamp-2 text-xs text-muted">{parts.join(' · ')}</span>
  ) : (
    dash
  );
}

export type Column = {
  key: string;
  title: string;
  core: boolean;
  cell: (row: TrackerRow) => ReactNode;
  className?: string;
  sort?: (row: TrackerRow) => string | number | null | undefined;
};

export const columns: Column[] = [
  {
    key: 'applied',
    title: 'Applied',
    core: true,
    className: 'whitespace-nowrap',
    sort: (row) => row.appliedOn,
    cell: (row) =>
      row.appliedOn ? (
        <span className="tabular-nums text-ink">{date(row.appliedOn)}</span>
      ) : (
        <span className="text-xs text-muted">Not applied</span>
      ),
  },
  {
    key: 'status',
    title: 'Status',
    core: true,
    className: 'min-w-[9.5rem] whitespace-nowrap',
    sort: (row) => row.status,
    cell: (row) => <StatusCell row={row} />,
  },
  {
    key: 'job',
    title: 'Job',
    core: true,
    className: 'min-w-[16rem] max-w-80',
    sort: (row) => row.role.toLowerCase(),
    cell: (row) => (
      <div className="min-w-0">
        <span className="block max-w-full truncate font-medium text-ink" title={row.role}>
          {row.role}
        </span>
        <span className="block truncate text-xs text-muted">{companyName(row.company)}</span>
      </div>
    ),
  },
  {
    key: 'details',
    title: 'Details',
    core: true,
    className: 'min-w-[10rem] max-w-60',
    cell: (row) => <Details row={row} />,
  },
  {
    key: 'resume',
    title: 'Resume',
    core: true,
    className: 'min-w-[11rem] max-w-56',
    sort: (row) => row.resumeLabel ?? row.cvTrack,
    cell: (row) => <ResumeCell row={row} />,
  },
  {
    key: 'follow',
    title: 'Follow-up',
    core: true,
    sort: (row) => row.followUpOn,
    cell: (row) => <DateCell row={row} field="follow_up_on" value={row.followUpOn} />,
  },
  {
    key: 'link',
    title: 'Posting',
    core: true,
    className: 'whitespace-nowrap',
    sort: (row) => row.url,
    cell: (row) => <PostingLink url={row.url} />,
  },
  {
    key: 'contacted',
    title: 'Contacted',
    core: false,
    sort: (row) => row.dateContacted,
    cell: (row) => <DateCell row={row} field="date_contacted" value={row.dateContacted} />,
  },
  {
    key: 'interview',
    title: 'Interview',
    core: false,
    sort: (row) => row.interviewOn,
    cell: (row) => <DateCell row={row} field="interview_on" value={row.interviewOn} />,
  },
  {
    key: 'via',
    title: 'Applied via',
    core: false,
    className: 'max-w-40 text-xs text-muted',
    sort: (row) => row.submittedVia,
    cell: (row) => row.submittedVia ?? (row.source === 'platform' ? 'JobPilot' : dash),
  },
  {
    key: 'level',
    title: 'Level',
    core: false,
    className: 'text-xs text-muted',
    sort: (row) => row.jobLevel,
    cell: (row) => row.jobLevel ?? dash,
  },
  {
    key: 'salary',
    title: 'Salary',
    core: false,
    className: 'max-w-40 text-xs tabular-nums text-muted',
    sort: (row) => row.salary,
    cell: (row) => row.salary ?? dash,
  },
  {
    key: 'actions',
    title: '',
    core: true,
    className: 'min-w-[8rem] text-right',
    cell: (row) =>
      row.archivedAt ? (
        <RestoreOrPurge id={row.id} title={row.role} />
      ) : (
        <DeleteApplication compact id={row.id} title={row.role} />
      ),
  },
];

type RowProps = {
  row: TrackerRow;
  shown: Column[];
  selected: boolean;
  compact: boolean;
  onPeek: (id: string) => void;
  onOpen: (id: string) => void;
};

// react-query keeps unchanged rows referentially stable, so a live refresh only re-renders rows that actually changed.
export const TableRow = memo(function TableRow({
  row,
  shown,
  selected,
  compact,
  onPeek,
  onOpen,
}: RowProps) {
  return (
    <tr
      data-row-id={row.id}
      tabIndex={0}
      onClick={() => onPeek(row.id)}
      onDoubleClick={() => onOpen(row.id)}
      aria-selected={selected}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === 'Enter') {
          event.preventDefault();
          onPeek(row.id);
        } else if (event.key === 'o') onOpen(row.id);
      }}
      className={`group cursor-pointer focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent align-middle transition-colors duration-150 hover:bg-raised/40 ${selected ? 'bg-accent-soft/50' : ''}`}
    >
      {shown.map((column) => (
        <td
          key={column.key}
          className={`border-b border-line/50 px-4 align-middle ${compact ? 'py-2' : 'py-3.5'} ${column.className ?? ''}`}
        >
          {column.cell(row)}
        </td>
      ))}
    </tr>
  );
});

// Phones: a table of seven columns cannot fit 375px without sideways scrolling and a clipped job title, so each
// application becomes a card with the same cells (same editing, same actions) stacked for one-thumb reading.
export const MobileCard = memo(function MobileCard({
  row,
  selected,
  onPeek,
}: Pick<RowProps, 'row' | 'selected' | 'onPeek'>) {
  return (
    <li
      data-row-id={row.id}
      className={`border-b border-line/60 px-4 py-4 last:border-b-0 ${selected ? 'bg-accent-soft/50' : ''}`}
    >
      <button
        type="button"
        onClick={() => onPeek(row.id)}
        aria-label={`${row.role} at ${companyName(row.company)}: show details`}
        className={`${ui.focus} -mx-1 block w-[calc(100%+0.5rem)] rounded-control px-1 text-left`}
      >
        <span className="block text-[15px] leading-snug font-medium text-ink [overflow-wrap:anywhere]">
          {row.role}
        </span>
        <span className="mt-0.5 block text-[13px] text-muted [overflow-wrap:anywhere]">
          {companyName(row.company)}
        </span>
      </button>
      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
        <StatusCell row={row} />
        <span className="text-xs tabular-nums text-muted">
          {row.appliedOn ? `Applied ${date(row.appliedOn)}` : 'Not applied yet'}
        </span>
      </div>
      <div className="mt-2.5 min-w-0">
        <Details row={row} />
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 text-xs">
        <div className="min-w-0">
          <dt className="text-muted">Follow-up</dt>
          <dd className="-ml-2 mt-0.5">
            <DateCell row={row} field="follow_up_on" value={row.followUpOn} />
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-muted">Resume</dt>
          <dd className="mt-1">
            <ResumeCell row={row} />
          </dd>
        </div>
      </dl>
      <div className="mt-3 flex items-center justify-between gap-3">
        <PostingLink url={row.url} />
        {row.archivedAt ? (
          <RestoreOrPurge id={row.id} title={row.role} />
        ) : (
          <DeleteApplication compact id={row.id} title={row.role} />
        )}
      </div>
    </li>
  );
});

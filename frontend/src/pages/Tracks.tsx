import { useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ChevronRight,
  Copy,
  Download,
  ExternalLink,
  History as HistoryIcon,
  Pencil,
  Plus,
  Trash2,
  Upload,
} from 'lucide-react';
import { api, type Market } from '../api/api';
import type { Track } from '../api/models';
import { date } from '../utils/format';
import { readBase64, textToBase64 } from '../utils/files';
import { useAction } from '../hooks/useAction';
import { cvFileName } from '../utils/humanize';
import {
  Button,
  Chip,
  Empty,
  ErrorMessage,
  IconButton,
  PageHeading,
  QueryState,
  ui,
} from '../components/ui';
import {
  FilterChips,
  matches,
  NoMatches,
  ResultCount,
  SearchField,
} from '../components/ui/Filters';
import { pick, useUrlParams } from '../hooks/useUrlParams';
import { CvPreview } from '../features/cv/CvPreview';
import { ConfirmDialog, Dialog } from '../components/ui/Dialog';
import { Pager, SortHeader, usePagedSort } from '../components/ui/PagedTable';

const MARKETS: { value: Market; label: string }[] = [
  { value: 'us', label: 'United States' },
  { value: 'morocco', label: 'Morocco' },
  { value: 'any', label: 'Any market' },
];
const marketLabel = (market: string) =>
  market === 'us' ? 'US CV' : market === 'morocco' ? 'Morocco CV' : 'Any market';

function MarketSelect({ value, onChange }: { value: Market; onChange: (market: Market) => void }) {
  return (
    <label className={ui.label}>
      Market
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as Market)}
        className={ui.select}
      >
        {MARKETS.map((item) => (
          <option key={item.value} value={item.value}>
            {item.label}
          </option>
        ))}
      </select>
      <span className="text-xs font-normal text-muted">
        Jobs located in Morocco get a Morocco CV; every other job (US, remote, elsewhere) gets a US
        CV.
      </span>
    </label>
  );
}

type Commit = { sha: string; subject: string; date: string };

function TrackHistory({ commits }: { commits: Commit[] }) {
  const accessors = useMemo(
    () => ({
      date: (item: Commit) => item.date,
      subject: (item: Commit) => item.subject,
      sha: (item: Commit) => item.sha,
    }),
    [],
  );
  const paged = usePagedSort(commits, {
    pageSize: 5,
    sort: { key: 'date', dir: 'desc' },
    accessors,
  });
  return (
    <div className="mt-3">
      <div className="overflow-x-auto rounded-control border border-line">
        <table className="w-full border-collapse text-left text-xs">
          <thead className="bg-raised/70">
            <tr>
              <SortHeader
                id="sha"
                title="Commit"
                sort={paged.sort}
                onToggle={paged.toggle}
                className="px-3 py-2 text-xs font-normal text-muted"
              />
              <SortHeader
                id="subject"
                title="Subject"
                sort={paged.sort}
                onToggle={paged.toggle}
                className="px-3 py-2 text-xs font-normal text-muted"
              />
              <SortHeader
                id="date"
                title="Date"
                sort={paged.sort}
                onToggle={paged.toggle}
                className="px-3 py-2 text-xs font-normal text-muted"
              />
            </tr>
          </thead>
          <tbody>
            {paged.rows.map((commit) => (
              <tr
                key={commit.sha}
                className="border-b border-line/60 transition-colors last:border-0 hover:bg-raised/40"
              >
                <td className="px-3 py-2">
                  <code title={commit.sha} className={ui.code}>
                    {commit.sha.slice(0, 7)}
                  </code>
                </td>
                <td className="max-w-[200px] truncate px-3 py-2 text-ink" title={commit.subject}>
                  {commit.subject}
                </td>
                <td className="whitespace-nowrap px-3 py-2 tabular-nums text-muted">
                  {date(commit.date)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="pt-2">
        <Pager
          page={paged.page}
          pages={paged.pages}
          total={paged.total}
          pageSize={paged.pageSize}
          onPage={paged.setPage}
          label="commits"
        />
      </div>
    </div>
  );
}

/** PDF (what employers receive) and an optional .tex source (enables tailoring). */
function FilePickers({
  pdf,
  tex,
  onPdf,
  onTex,
}: {
  pdf: File | null;
  tex: File | null;
  onPdf: (file: File | null) => void;
  onTex: (file: File | null) => void;
}) {
  return (
    <div className="grid gap-3">
      <label className={ui.label}>
        CV as PDF
        <input
          type="file"
          accept="application/pdf,.pdf"
          onChange={(event) => onPdf(event.target.files?.[0] ?? null)}
          className={ui.input}
        />
        <span className="text-xs font-normal text-muted">
          1–2 pages with selectable text, up to 3 MB. {pdf ? `Selected: ${pdf.name}` : ''}
        </span>
      </label>
      <label className={ui.label}>
        LaTeX source (optional)
        <input
          type="file"
          accept=".tex,text/plain"
          onChange={(event) => onTex(event.target.files?.[0] ?? null)}
          className={ui.input}
        />
        <span className="text-xs font-normal text-muted">
          Add the .tex to let JobPilot tailor this CV per job. Without it the PDF is sent exactly as
          uploaded. {tex ? `Selected: ${tex.name}` : ''}
        </span>
      </label>
    </div>
  );
}

async function encode(pdf: File | null, tex: File | null) {
  return {
    pdfBase64: pdf ? await readBase64(pdf) : null,
    texBase64: tex ? await readBase64(tex, 500_000) : null,
  };
}

function AddTrack({ onClose }: { onClose: () => void }) {
  const [title, setTitle] = useState('');
  const [keywords, setKeywords] = useState('');
  const [market, setMarket] = useState<Market>('us');
  const [pdf, setPdf] = useState<File | null>(null);
  const [tex, setTex] = useState<File | null>(null);
  const create = useAction(
    async () =>
      api.createTrack({
        title: title.trim(),
        keywords: keywords
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
        defaultMode: 'base',
        market,
        ...(await encode(pdf, tex)),
      }),
    'CV added.',
  );
  return (
    <Dialog
      title="Add a CV"
      onClose={() => {
        if (!create.isPending) onClose();
      }}
    >
      <form
        className={ui.stack}
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate(undefined, { onSuccess: onClose });
        }}
      >
        <label className={ui.label}>
          Name
          <input
            autoFocus
            required
            maxLength={80}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="e.g. Software engineering"
            className={ui.input}
          />
        </label>
        <label className={ui.label}>
          Matching keywords
          <input
            value={keywords}
            onChange={(event) => setKeywords(event.target.value)}
            placeholder="python, backend, api"
            className={ui.input}
          />
          <span className="text-xs font-normal text-muted">
            Comma separated. Jobs whose posting mentions these get this CV suggested.
          </span>
        </label>
        <MarketSelect value={market} onChange={setMarket} />
        <FilePickers pdf={pdf} tex={tex} onPdf={setPdf} onTex={setTex} />
        <ErrorMessage error={create.error} />
        <div className={ui.dialogActions}>
          <Button type="button" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            loading={create.isPending}
            disabled={title.trim().length < 2 || (!pdf && !tex)}
          >
            Add CV
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function ReplaceCv({ track, onClose }: { track: Track; onClose: () => void }) {
  const [pdf, setPdf] = useState<File | null>(null);
  const [tex, setTex] = useState<File | null>(null);
  const replace = useAction(
    async () => api.replaceTrackCv(track.id, await encode(pdf, tex)),
    'CV replaced. New applications use it.',
  );
  return (
    <Dialog
      title={`Replace the CV for “${track.title}”`}
      onClose={() => {
        if (!replace.isPending) onClose();
      }}
    >
      <p className="leading-relaxed text-muted">
        Applications still being prepared on this track will use the new file. Anything already
        submitted keeps the CV it was sent with.
      </p>
      <form
        className={ui.stack}
        onSubmit={(event) => {
          event.preventDefault();
          replace.mutate(undefined, { onSuccess: onClose });
        }}
      >
        <FilePickers pdf={pdf} tex={tex} onPdf={setPdf} onTex={setTex} />
        <ErrorMessage error={replace.error} />
        <div className={ui.dialogActions}>
          <Button type="button" onClick={onClose} disabled={replace.isPending}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            loading={replace.isPending}
            disabled={!pdf && !tex}
          >
            Replace CV
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** Copy the LaTeX out (to Overleaf), edit it there, paste it back: the new file is compiled and checked before it replaces the old one. */
function LatexSource({ track }: { track: Track }) {
  const source = useQuery({
    queryKey: ['track-source', track.id, track.sha256],
    queryFn: () => api.trackSource(track.id),
  });
  const [draft, setDraft] = useState('');
  const [notice, setNotice] = useState('');
  const form = useRef<HTMLFormElement>(null);
  const replace = useAction(
    () => api.replaceTrackCv(track.id, { texBase64: textToBase64(draft) }),
    'LaTeX saved and compiled. New applications use it.',
  );
  const tex = source.data?.tex ?? '';
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(tex);
      setNotice('Copied. Paste it into Overleaf.');
    } catch {
      setNotice('Copy was blocked; use Download instead.');
    }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([tex], { type: 'text/plain;charset=utf-8' }));
    const link = Object.assign(document.createElement('a'), {
      href: url,
      download: `${track.id}.tex`,
    });
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <section className="mt-5 border-t border-line pt-4" aria-label="LaTeX source">
      <h3 className="font-serif text-[14px] font-semibold text-ink">LaTeX source</h3>
      <p className="mt-1 text-xs leading-relaxed text-muted">
        Copy it to Overleaf, edit it there, then paste the result below. It is compiled and checked
        (one page, no errors) before it replaces this CV.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          type="button"
          onClick={() => {
            void copy();
          }}
          disabled={!tex}
        >
          <Copy size={14} aria-hidden="true" />
          Copy LaTeX
        </Button>
        <Button type="button" onClick={download} disabled={!tex}>
          <Download size={14} aria-hidden="true" />
          Download .tex
        </Button>
        <Button
          type="button"
          onClick={() => form.current?.submit()}
          disabled={!tex}
          title="Opens a new Overleaf project in a new tab. This sends the CV text to Overleaf."
        >
          <ExternalLink size={14} aria-hidden="true" />
          Open in Overleaf
        </Button>
      </div>
      <form
        ref={form}
        action="https://www.overleaf.com/docs"
        method="post"
        target="_blank"
        rel="noopener noreferrer"
        className="hidden"
      >
        <input type="hidden" name="snip" value={tex} readOnly />
        <input type="hidden" name="snip_name" value={`${track.id}.tex`} readOnly />
        <input type="hidden" name="engine" value="xelatex" readOnly />
      </form>
      <p className="mt-2 text-xs text-muted" aria-live="polite">
        {source.isPending ? 'Loading…' : source.error ? 'Could not load the LaTeX source.' : notice}
      </p>
      <form
        className="mt-3 grid gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          replace.mutate(undefined, { onSuccess: () => setDraft('') });
        }}
      >
        <label className={ui.label}>
          Paste edited LaTeX
          <textarea
            rows={6}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            spellCheck={false}
            placeholder="\\documentclass{article} …"
            className={`${ui.textarea} font-mono text-xs`}
          />
        </label>
        <ErrorMessage error={replace.error} />
        <div>
          <Button
            type="submit"
            variant="primary"
            loading={replace.isPending}
            disabled={draft.trim().length < 50}
          >
            Compile and save
          </Button>
        </div>
      </form>
    </section>
  );
}

function EditTrack({ track, onClose }: { track: Track; onClose: () => void }) {
  const [title, setTitle] = useState(track.title);
  const [keywords, setKeywords] = useState(track.keywords.join(', '));
  const [mode, setMode] = useState<'base' | 'tailor'>(
    track.defaultMode === 'tailor' ? 'tailor' : 'base',
  );
  const [market, setMarket] = useState<Market>(track.market);
  const save = useAction(
    () =>
      api.editTrack(track.id, {
        title: title.trim(),
        keywords: keywords
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
        defaultMode: mode,
        market,
      }),
    'CV details saved.',
  );
  return (
    <Dialog
      title="Edit CV details"
      onClose={() => {
        if (!save.isPending) onClose();
      }}
    >
      <form
        className={ui.stack}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate(undefined, { onSuccess: onClose });
        }}
      >
        <label className={ui.label}>
          Name
          <input
            autoFocus
            required
            maxLength={80}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            className={ui.input}
          />
        </label>
        <label className={ui.label}>
          Matching keywords
          <input
            value={keywords}
            onChange={(event) => setKeywords(event.target.value)}
            className={ui.input}
          />
        </label>
        <MarketSelect value={market} onChange={setMarket} />
        <label className={ui.label}>
          For each job
          <select
            value={mode}
            onChange={(event) => setMode(event.target.value as 'base' | 'tailor')}
            className={ui.select}
          >
            <option value="base">Send this CV exactly as it is</option>
            <option value="tailor" disabled={!track.tailorable}>
              Tailor it to each job{track.tailorable ? '' : ' (needs the LaTeX source)'}
            </option>
          </select>
        </label>
        <ErrorMessage error={save.error} />
        <div className={ui.dialogActions}>
          <Button type="button" onClick={onClose} disabled={save.isPending}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            loading={save.isPending}
            disabled={title.trim().length < 2}
          >
            Save
          </Button>
        </div>
      </form>
      {track.tailorable && <LatexSource track={track} />}
    </Dialog>
  );
}

function RemoveTrack({
  track,
  others,
  onClose,
}: {
  track: Track;
  others: Track[];
  onClose: () => void;
}) {
  const [target, setTarget] = useState(others[0]?.id ?? '');
  const remove = useAction(
    () => api.removeTrack(track.id, track.jobs > 0 ? target : undefined),
    (result) =>
      result.moved
        ? `CV removed. ${result.moved} application${result.moved === 1 ? '' : 's'} moved.`
        : 'CV removed.',
  );
  return (
    <ConfirmDialog
      title={`Remove “${track.title}”?`}
      confirmLabel="Remove CV"
      pending={remove.isPending}
      error={remove.error}
      disabled={track.jobs > 0 && !target}
      onClose={onClose}
      onConfirm={() => remove.mutate(undefined, { onSuccess: onClose })}
    >
      <p>
        It stops being offered for new applications. Applications already sent keep the CV they
        used.
      </p>
      {track.jobs > 0 && (
        <label className={ui.label}>
          Move {track.jobs} open application{track.jobs === 1 ? '' : 's'} to
          <select
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            className={ui.select}
          >
            {others.map((other) => (
              <option key={other.id} value={other.id}>
                {other.title}
              </option>
            ))}
          </select>
        </label>
      )}
      {track.jobs > 0 && !others.length && (
        <p className="text-[13px] text-warn">
          Add another CV first; open applications need somewhere to go.
        </p>
      )}
    </ConfirmDialog>
  );
}

const familyOf = (title: string) => title.replace(/\s·\s(US|Morocco)$/, '').trim();
const marketOrder: Record<string, number> = { us: 0, morocco: 1, any: 2 };

function TrackRow({ track, all }: { track: Track; all: Track[] }) {
  const [dialog, setDialog] = useState<'replace' | 'edit' | 'remove' | null>(null);
  const [open, setOpen] = useState(false);
  const history = useQuery({
    queryKey: ['track-history', track.id, track.sha256],
    queryFn: () => api.history(track.id),
    enabled: open,
  });
  return (
    <li className="border-t border-line/60 first:border-t-0">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
        <div className="min-w-0 flex-1 basis-56">
          <div className="flex flex-wrap items-center gap-1.5">
            <Chip tone={track.market === 'any' ? 'neutral' : 'accent'}>
              {marketLabel(track.market)}
            </Chip>
            <span className="text-[13px] font-medium text-ink [overflow-wrap:anywhere]">
              {track.market === 'any' ? track.title : familyOf(track.title)}
            </span>
          </div>
          <p className="mt-1 truncate text-xs text-muted" title={track.keywords.join(', ')}>
            {track.keywords.length ? track.keywords.join(' · ') : 'No keywords yet'}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5 text-xs text-muted">
          <Chip tone={track.defaultMode === 'tailor' ? 'accent' : 'neutral'}>
            {track.defaultMode === 'tailor' ? 'Tailored per job' : 'Sent as is'}
          </Chip>
          {!track.tailorable && <Chip>PDF only</Chip>}
          <span className="tabular-nums" title="Open applications using this CV">
            {track.jobs} {track.jobs === 1 ? 'application' : 'applications'}
          </span>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          {track.sha256 ? (
            <CvPreview track={track.id} />
          ) : (
            <span className="text-xs text-warn">No CV file yet</span>
          )}
          <Button
            onClick={() => setDialog('replace')}
            aria-label={`Replace file for ${track.title}`}
          >
            <Upload size={14} aria-hidden="true" />
            Replace
          </Button>
          <Button onClick={() => setDialog('edit')} aria-label={`Edit details of ${track.title}`}>
            <Pencil size={14} aria-hidden="true" />
            Edit
          </Button>
          <IconButton
            aria-expanded={open}
            label={`${open ? 'Hide' : 'Show'} history of ${track.title}`}
            title="History"
            onClick={() => setOpen((value) => !value)}
          >
            <HistoryIcon size={15} aria-hidden="true" />
          </IconButton>
          <IconButton
            tone="danger"
            label={`Remove ${track.title}`}
            title="Remove"
            onClick={() => setDialog('remove')}
          >
            <Trash2 size={15} aria-hidden="true" />
          </IconButton>
        </div>
      </div>
      {open && (
        <div className="border-t border-line/50 bg-raised/30 px-4 py-3">
          <QueryState
            pending={history.isPending}
            error={history.error}
            retry={() => {
              void history.refetch();
            }}
          >
            {history.data?.length ? (
              <TrackHistory commits={history.data} />
            ) : (
              <Empty>No history yet.</Empty>
            )}
          </QueryState>
        </div>
      )}
      {dialog === 'replace' && <ReplaceCv track={track} onClose={() => setDialog(null)} />}
      {dialog === 'edit' && <EditTrack track={track} onClose={() => setDialog(null)} />}
      {dialog === 'remove' && (
        <RemoveTrack
          track={track}
          others={all.filter((item) => item.id !== track.id && item.sha256)}
          onClose={() => setDialog(null)}
        />
      )}
    </li>
  );
}

const marketFilters = ['all', 'us', 'morocco', 'any'] as const;
type MarketFilter = (typeof marketFilters)[number];

export function Tracks() {
  const tracks = useQuery({ queryKey: ['tracks'], queryFn: api.tracks });
  const [adding, setAdding] = useState(false);
  const url = useUrlParams();
  const query = url.get('q');
  const market = pick(url.get('market'), marketFilters, 'all');
  const all = useMemo(() => tracks.data ?? [], [tracks.data]);
  const counts = useMemo(
    () => ({
      all: all.length,
      us: all.filter((t) => t.market === 'us').length,
      morocco: all.filter((t) => t.market === 'morocco').length,
      any: all.filter((t) => t.market === 'any').length,
    }),
    [all],
  );
  const groups = useMemo(() => {
    const matching = all.filter(
      (track) =>
        (market === 'all' || track.market === market) &&
        matches(query, track.title, track.id, ...track.keywords),
    );
    const byFamily = new Map<string, Track[]>();
    for (const track of matching)
      byFamily.set(familyOf(track.title), [...(byFamily.get(familyOf(track.title)) ?? []), track]);
    return [...byFamily.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(
        ([family, items]) =>
          [
            family,
            [...items].sort(
              (a, b) =>
                (marketOrder[a.market] ?? 9) - (marketOrder[b.market] ?? 9) ||
                a.title.localeCompare(b.title),
            ),
          ] as const,
      );
  }, [all, query, market]);
  const shown = groups.reduce((total, [, items]) => total + items.length, 0);
  const filtered = !!query.trim() || market !== 'all';
  const clear = () => url.set({ q: null, market: null });
  const options = [
    { id: 'all', label: 'All' },
    { id: 'us', label: 'US' },
    { id: 'morocco', label: 'Morocco' },
    { id: 'any', label: 'Any market' },
  ].map((item) => ({
    ...item,
    id: item.id as MarketFilter,
    count: counts[item.id as MarketFilter],
  }));
  return (
    <>
      <PageHeading
        title="CV tracks"
        description="Your CVs, grouped by role type. Employers always receive the file as “Abdelhadi Marjane.pdf”."
        action={
          <Button
            variant="primary"
            className="whitespace-nowrap"
            data-return-focus
            onClick={() => setAdding(true)}
          >
            <Plus size={15} aria-hidden="true" />
            Add a CV
          </Button>
        }
      />
      <QueryState
        pending={tracks.isPending}
        error={tracks.error}
        retry={() => {
          void tracks.refetch();
        }}
      >
        {all.length ? (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <SearchField
                primary
                label="Search CVs"
                placeholder="Search by name, keyword or id…"
                value={query}
                onChange={(value) => url.set({ q: value })}
              />
              <FilterChips
                label="Filter by market"
                options={options}
                value={market}
                onChange={(value) => url.set({ market: value === 'all' ? null : value })}
              />
            </div>
            <div className="mb-3">
              <ResultCount
                shown={shown}
                total={all.length}
                noun={['CV', 'CVs']}
                filtered={filtered}
                onClear={clear}
              />
            </div>
            {groups.length ? (
              <div className="space-y-5">
                {groups.map(([family, items]) => (
                  <section
                    key={family}
                    aria-label={family}
                    className="overflow-hidden rounded-card border border-line bg-surface shadow-card"
                  >
                    <h2 className="flex items-center gap-2 border-b border-line bg-raised/60 px-4 py-2.5 font-serif text-[15px] font-semibold tracking-tight text-ink">
                      <ChevronRight size={14} className="text-muted" aria-hidden="true" />
                      {family}
                      <span className="text-xs font-normal text-muted">{items.length}</span>
                    </h2>
                    <ul className="list-none">
                      {items.map((track) => (
                        <TrackRow key={track.id} track={track} all={all} />
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            ) : (
              <section className={ui.card}>
                <NoMatches noun="CVs" query={query} onClear={clear} />
              </section>
            )}
          </>
        ) : (
          <section className={ui.card}>
            <Empty
              action={
                <Button variant="primary" onClick={() => setAdding(true)}>
                  <Plus size={15} aria-hidden="true" />
                  Add a CV
                </Button>
              }
            >
              No CVs yet. Add one to start applying.
            </Empty>
          </section>
        )}
      </QueryState>
      {adding && <AddTrack onClose={() => setAdding(false)} />}
    </>
  );
}

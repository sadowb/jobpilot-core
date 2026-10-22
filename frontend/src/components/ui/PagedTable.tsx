import { useMemo, useState, type ReactNode } from 'react';
import { cn } from '../../utils/cn';
import { Button } from './Button';
import { ui } from './styles';
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight } from 'lucide-react';

export type SortDirection = 'asc' | 'desc';

// Client-side sort + pagination for tables of a few hundred rows (the API already caps pages).
// `resetKey` is the active filter (search text, chip…): when it changes the view returns to page 1. Row identity alone is
// not used for that, because live refreshes replace the array several times a minute and must not move the reader.
export function usePagedSort<T>(
  rows: T[],
  options: {
    pageSize?: number;
    sort?: { key: string; dir: SortDirection };
    accessors: Record<string, (row: T) => string | number | null | undefined>;
    resetKey?: string;
    sortValue?: string;
    onSortChange?: (value: string) => void;
  },
) {
  const [pageSize, setPageSize] = useState(options.pageSize ?? 25);
  // `sortValue` ("applied.desc") makes the sort controlled, so a page can keep it in the URL next to its filters.
  const [ownSort, setOwnSort] = useState<{ key: string; dir: SortDirection } | undefined>(
    options.sort,
  );
  const [urlKey, urlDir] = (options.sortValue ?? '').split('.');
  const controlled = options.onSortChange !== undefined;
  const sort = useMemo(
    () =>
      controlled && urlKey && options.accessors[urlKey]
        ? { key: urlKey, dir: urlDir === 'desc' ? ('desc' as const) : ('asc' as const) }
        : controlled
          ? options.sort
          : ownSort,
    [controlled, urlKey, urlDir, options.accessors, options.sort, ownSort],
  );
  const [page, setPage] = useState(0);
  const [seenKey, setSeenKey] = useState(options.resetKey);
  if (seenKey !== options.resetKey) {
    setSeenKey(options.resetKey);
    setPage(0);
  } // adjust state during render, no extra effect pass
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const get = options.accessors[sort.key];
    if (!get) return rows;
    const factor = sort.dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const left = get(a),
        right = get(b);
      if (left === right) return 0;
      if (left === null || left === undefined || left === '') return 1; // empty values always last
      if (right === null || right === undefined || right === '') return -1;
      return (
        (typeof left === 'number' && typeof right === 'number'
          ? left - right
          : String(left).localeCompare(String(right), undefined, {
              numeric: true,
              sensitivity: 'base',
            })) * factor
      );
    });
  }, [rows, sort, options.accessors]);
  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const current = Math.min(page, pages - 1);
  const slice = sorted.slice(current * pageSize, current * pageSize + pageSize);
  const changeSize = (size: number) => {
    setPageSize(size);
    setPage(0);
  };
  const toggle = (key: string) => {
    setPage(0);
    const next =
      sort?.key === key
        ? { key, dir: sort.dir === 'asc' ? ('desc' as const) : ('asc' as const) }
        : { key, dir: 'asc' as const };
    if (options.onSortChange)
      options.onSortChange(
        next.key === options.sort?.key && next.dir === options.sort.dir
          ? ''
          : `${next.key}.${next.dir}`,
      );
    else setOwnSort(next);
  };
  return {
    rows: slice,
    all: sorted,
    total: sorted.length,
    page: current,
    pages,
    pageSize,
    setPage,
    setPageSize: changeSize,
    sort,
    toggle,
  };
}

export function SortHeader({
  title,
  id,
  sort,
  onToggle,
  className = '',
}: {
  title: string;
  id: string;
  sort?: { key: string; dir: SortDirection };
  onToggle: (key: string) => void;
  className?: string;
}) {
  const active = sort?.key === id;
  return (
    <th
      scope="col"
      aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
      className={`whitespace-nowrap border-b border-line px-3 py-2.5 text-xs font-normal text-muted ${className}`}
    >
      <button
        type="button"
        onClick={() => onToggle(id)}
        className={cn(ui.focus, ui.linkMuted, 'text-muted no-underline hover:no-underline px-1')}
      >
        {title}
        {active ? (
          sort.dir === 'asc' ? (
            <ArrowUp size={12} aria-hidden="true" />
          ) : (
            <ArrowDown size={12} aria-hidden="true" />
          )
        ) : (
          <span className="w-3" aria-hidden="true" />
        )}
      </button>
    </th>
  );
}

export function Pager({
  page,
  pages,
  total,
  pageSize,
  onPage,
  onPageSize,
  label = 'items',
}: {
  page: number;
  pages: number;
  total: number;
  pageSize: number;
  onPage: (page: number) => void;
  onPageSize?: (size: number) => void;
  label?: string;
}): ReactNode {
  if (!total) return null;
  const from = page * pageSize + 1,
    to = Math.min(total, (page + 1) * pageSize);
  return (
    <nav
      aria-label="Pagination"
      className="flex flex-wrap items-center justify-between gap-3 text-[13px] text-muted"
    >
      <span className="tabular-nums">
        {from}–{to} of {total} {label}
      </span>
      <span className="flex flex-wrap items-center gap-3">
        {onPageSize && (
          <label className="flex items-center gap-2">
            Rows
            <select
              value={pageSize}
              onChange={(event) => onPageSize(Number(event.target.value))}
              className={cn(
                ui.focus,
                'min-h-9 rounded-control border border-line bg-surface px-2 text-ink max-[560px]:min-h-[44px]',
              )}
            >
              {[10, 25, 50, 100].map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
        )}
        {pages > 1 && (
          <span className="flex items-center gap-1.5">
            <Button disabled={page === 0} onClick={() => onPage(page - 1)} size="sm">
              <ChevronLeft size={15} aria-hidden="true" />
              Previous
            </Button>
            <span className="px-1 tabular-nums">
              Page {page + 1} of {pages}
            </span>
            <Button disabled={page >= pages - 1} onClick={() => onPage(page + 1)} size="sm">
              Next
              <ChevronRight size={15} aria-hidden="true" />
            </Button>
          </span>
        )}
      </span>
    </nav>
  );
}

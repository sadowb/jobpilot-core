import { useId, type ReactNode } from 'react';
import { Search, X } from 'lucide-react';
import { cn } from '../../utils/cn';
import { Button, IconButton, LinkButton } from './Button';
import { Empty } from './Feedback';
import { Kbd } from './Kbd';
import { chipToggle, ui } from './styles';

/**
 * The one search box every list uses. Esc clears it (then blurs when already empty). Marking it `primary` lets the
 * dashboard-wide "/" shortcut (App.tsx) focus it; when several are marked, the first one on the page wins.
 */
export function SearchField({
  value,
  onChange,
  label,
  placeholder,
  primary = false,
  hint = primary,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  label: string;
  placeholder?: string;
  primary?: boolean;
  hint?: boolean;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={cn('relative min-w-[min(14rem,100%)] flex-1', className)}>
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <Search
        size={15}
        className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted"
        aria-hidden="true"
      />
      <input
        id={id}
        type="search"
        value={value}
        placeholder={placeholder ?? `${label}…`}
        data-search-shortcut={primary ? '' : undefined}
        aria-keyshortcuts={primary ? '/' : undefined}
        autoComplete="off"
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== 'Escape') return;
          event.stopPropagation(); // a peek panel or lane listening for Esc must not also fire
          if (value) onChange('');
          else event.currentTarget.blur();
        }}
        className={cn(ui.input, 'pr-9 pl-9 [&::-webkit-search-cancel-button]:hidden')}
      />
      {value ? (
        <IconButton
          label={`Clear ${label.toLowerCase()}`}
          onClick={() => onChange('')}
          className="absolute top-1/2 right-1 size-8 -translate-y-1/2 max-[560px]:size-[44px]"
        >
          <X size={14} aria-hidden="true" />
        </IconButton>
      ) : (
        hint && (
          <Kbd className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 max-[860px]:hidden">
            /
          </Kbd>
        )
      )}
    </div>
  );
}

export type FilterOption<T extends string> = { id: T; label: string; count?: number };

/** Single-select filter pills with live counts. Options with a zero count are kept only while selected. */
export function FilterChips<T extends string>({
  options,
  value,
  onChange,
  label,
  hideEmpty = false,
}: {
  options: FilterOption<T>[];
  value: T;
  onChange: (value: T) => void;
  label: string;
  hideEmpty?: boolean;
}) {
  const shown = hideEmpty
    ? options.filter((option, index) => index === 0 || option.count || option.id === value)
    : options;
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-1.5">
      {shown.map((option) => {
        const active = option.id === value;
        return (
          <button
            key={option.id}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.id)}
            className={chipToggle(active)}
          >
            {option.label}
            {option.count !== undefined && (
              <span className={cn('tabular-nums', active ? 'text-accent/80' : 'text-muted/80')}>
                {option.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/** "3 of 12 CVs · Clear filters", announced politely so screen-reader users hear the result of typing. */
export function ResultCount({
  shown,
  total,
  noun,
  filtered,
  onClear,
  children,
}: {
  shown: number;
  total: number;
  noun: string | readonly [singular: string, plural: string];
  filtered: boolean;
  onClear: () => void;
  children?: ReactNode;
}) {
  const word = typeof noun === 'string' ? noun : total === 1 ? noun[0] : noun[1];
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-xs text-muted">
      <p aria-live="polite" className="flex min-h-8 flex-wrap items-center gap-x-3 tabular-nums">
        <span>{filtered ? `${shown} of ${total} ${word}` : `${total} ${word}`}</span>
        {filtered && (
          <LinkButton onClick={onClear}>
            <X size={12} aria-hidden="true" />
            Clear filters
          </LinkButton>
        )}
      </p>
      {children}
    </div>
  );
}

/** The empty state for "your filters hid everything", always with a way out. */
export function NoMatches({
  query,
  noun,
  onClear,
}: {
  query?: string;
  noun: string;
  onClear: () => void;
}) {
  return (
    <Empty action={<Button onClick={onClear}>Clear filters</Button>}>
      {query?.trim() ? (
        <>
          No {noun} match “{query.trim()}”.
        </>
      ) : (
        <>No {noun} match these filters.</>
      )}
    </Empty>
  );
}

/** Case-insensitive "every word appears somewhere" match, so "varda intern" finds "Cybersecurity Internship · Varda". */
export function matches(needle: string, ...haystack: (string | null | undefined)[]): boolean {
  const words = needle.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const text = haystack.filter(Boolean).join(' ').toLowerCase();
  return words.every((word) => text.includes(word));
}

import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown } from 'lucide-react';
import { label } from '../../utils/format';
import { cn } from '../../utils/cn';
import { statusTone, type Tone } from './Chip';
import { ui } from './styles';

const tones: Record<Tone, { pill: string; dot: string }> = {
  neutral: { pill: 'bg-raised text-secondary ring-line', dot: 'bg-muted' },
  accent: { pill: 'bg-accent-soft text-accent ring-accent-line/60', dot: 'bg-accent' },
  ok: { pill: 'bg-ok-soft text-ok ring-ok/25', dot: 'bg-ok' },
  warn: { pill: 'bg-warn-soft text-warn ring-warn/30', dot: 'bg-warn' },
  danger: { pill: 'bg-danger-soft text-danger ring-danger/25', dot: 'bg-danger' },
  info: { pill: 'bg-info-soft text-info ring-info/25', dot: 'bg-info' },
};

// Grouped the way an application actually moves: saved -> in motion -> over.
const groups: { name: string; statuses: string[] }[] = [
  { name: 'Pipeline', statuses: ['discovered', 'submitted'] },
  { name: 'In progress', statuses: ['screening', 'interview', 'offer'] },
  { name: 'Closed', statuses: ['rejected', 'withdrawn', 'closed'] },
];
const order = groups.flatMap((group) => group.statuses);

type Place = { left: number; top?: number; bottom?: number };

/** Click-to-change status pill. Menu renders in a portal so table scroll containers never clip it. */
export function StatusPicker({
  value,
  onChange,
  subject,
  disabled = false,
}: {
  value: string;
  onChange: (next: string) => void;
  subject: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<Place>({ left: 0 });
  const [active, setActive] = useState(0);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const tone = tones[statusTone(value)];
  const options = order;

  useLayoutEffect(() => {
    if (!open || !trigger.current) return;
    const box = trigger.current.getBoundingClientRect();
    const height = menu.current?.offsetHeight ?? 280;
    const below = window.innerHeight - box.bottom;
    const left = Math.min(box.left, window.innerWidth - 232);
    setPlace(
      below < height + 12 && box.top > below
        ? { left, bottom: window.innerHeight - box.top + 6 }
        : { left, top: box.bottom + 6 },
    );
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    menu.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus();
    return () => {
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [open]);

  const choose = (next: string) => {
    setOpen(false);
    trigger.current?.focus();
    if (next !== value) onChange(next);
  };
  // Type-to-jump: a printable key focuses the next option whose label starts with it.
  const jump = (key: string) => {
    const start = options.findIndex(
      (status, index) => index > active && label(status).toLowerCase().startsWith(key),
    );
    const next =
      start >= 0
        ? start
        : options.findIndex((status) => label(status).toLowerCase().startsWith(key));
    if (next >= 0) {
      setActive(next);
      menu.current?.querySelectorAll<HTMLElement>('[role="option"]')[next]?.focus();
    }
  };
  const move = (delta: number) => {
    const next = (active + delta + options.length) % options.length;
    setActive(next);
    menu.current?.querySelectorAll<HTMLElement>('[role="option"]')[next]?.focus();
  };

  return (
    <>
      <button
        ref={trigger}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`Status for ${subject}: ${label(value)}. Change`}
        aria-disabled={disabled || undefined}
        onClick={(event) => {
          event.stopPropagation();
          if (disabled) return;
          setActive(Math.max(0, options.indexOf(value)));
          setOpen(!open);
        }}
        className={cn(
          ui.focus,
          'group/status inline-flex min-h-7 max-[560px]:min-h-[44px] items-center gap-1.5 rounded-full py-0.5 pr-1.5 pl-2.5 text-[12px] leading-5 font-medium ring-1 ring-inset transition-[box-shadow,background-color] hover:ring-2 aria-disabled:opacity-60',
          tones[statusTone(value)].pill,
        )}
      >
        <span className={cn('size-1.5 shrink-0 rounded-full', tone.dot)} aria-hidden="true" />
        {label(value)}
        <ChevronDown
          size={13}
          aria-hidden="true"
          className={cn(
            'shrink-0 opacity-50 transition-[transform,opacity] group-hover/status:opacity-100',
            open && 'rotate-180 opacity-100',
          )}
        />
      </button>
      {open &&
        createPortal(
          <>
            <div
              className="fixed inset-0 z-[60]"
              onClick={(event) => {
                event.stopPropagation();
                setOpen(false);
              }}
            />
            <div
              ref={menu}
              id={menuId}
              role="listbox"
              aria-label="Set status"
              style={place}
              onClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => {
                if (event.key === 'Escape') {
                  event.preventDefault();
                  setOpen(false);
                  trigger.current?.focus();
                } else if (event.key === 'ArrowDown') {
                  event.preventDefault();
                  move(1);
                } else if (event.key === 'ArrowUp') {
                  event.preventDefault();
                  move(-1);
                } else if (event.key === 'Tab') {
                  trigger.current?.focus();
                  setOpen(false);
                } else if (
                  event.key.length === 1 &&
                  /\S/.test(event.key) &&
                  !event.ctrlKey &&
                  !event.metaKey &&
                  !event.altKey
                ) {
                  event.preventDefault();
                  jump(event.key.toLowerCase());
                }
              }}
              className="fixed z-[61] w-56 rounded-card border border-line bg-surface p-1.5 shadow-floating motion-safe:animate-appear"
            >
              {groups.map((group) => (
                <div
                  key={group.name}
                  role="group"
                  aria-label={group.name}
                  className="[&:not(:first-child)]:mt-1 [&:not(:first-child)]:border-t [&:not(:first-child)]:border-line/70 [&:not(:first-child)]:pt-1"
                >
                  <p className="px-2.5 pt-1.5 pb-1 text-[11px] font-medium text-muted">
                    {group.name}
                  </p>
                  {group.statuses.map((status) => {
                    const index = options.indexOf(status);
                    const selected = status === value;
                    return (
                      <div
                        key={status}
                        role="option"
                        aria-selected={selected}
                        tabIndex={index === active ? 0 : -1}
                        onClick={() => choose(status)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            choose(status);
                          }
                        }}
                        className={cn(
                          ui.focus,
                          'flex min-h-8 cursor-pointer items-center gap-2.5 rounded-control px-2.5 text-[13px] text-ink transition-colors hover:bg-raised focus-visible:bg-raised',
                          selected && 'font-medium',
                        )}
                      >
                        <span
                          className={cn(
                            'size-2 shrink-0 rounded-full',
                            tones[statusTone(status)].dot,
                          )}
                          aria-hidden="true"
                        />
                        <span className="flex-1">{label(status)}</span>
                        {selected && <Check size={14} className="text-accent" aria-hidden="true" />}
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </>,
          document.body,
        )}
    </>
  );
}

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { FileSpreadsheet, Plus, SquarePen } from 'lucide-react';
import { Button, ui } from '../../components/ui';

export type DialogId = 'applied' | 'import' | 'add';
const items: { id: DialogId; title: string; text: string; icon: ReactNode }[] = [
  {
    id: 'add',
    title: 'Apply for me',
    text: 'Paste links. JobPilot fills forms and waits for your approval.',
    icon: <Plus size={16} aria-hidden="true" />,
  },
  {
    id: 'applied',
    title: 'I applied myself',
    text: 'Quick save: paste a link and Haiku fills in the details.',
    icon: <SquarePen size={16} aria-hidden="true" />,
  },
  {
    id: 'import',
    title: 'Import my sheet',
    text: 'Bring your spreadsheet in one go.',
    icon: <FileSpreadsheet size={16} aria-hidden="true" />,
  },
];

export function AddMenu({ onPick }: { onPick: (dialog: DialogId) => void }) {
  const [open, setOpen] = useState(false);
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open) menu.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [open]);
  return (
    <div
      className="relative"
      onKeyDown={(event) => {
        if (event.key === 'Escape') setOpen(false);
      }}
    >
      <Button
        variant="primary"
        aria-haspopup="menu"
        aria-expanded={open}
        data-return-focus=""
        onClick={() => setOpen(!open)}
        className="shadow-sm"
      >
        <Plus size={15} aria-hidden="true" />
        Add applications
      </Button>
      {open && (
        <>
          <button
            type="button"
            tabIndex={-1}
            aria-hidden="true"
            aria-label="Close menu"
            className="fixed inset-0 z-30 cursor-default"
            onClick={() => setOpen(false)}
          />
          <div
            ref={menu}
            role="menu"
            className="absolute right-0 top-full z-40 mt-2 w-[min(22rem,calc(100vw-2rem))] rounded-card border border-line bg-surface p-1.5 shadow-floating motion-safe:animate-appear"
          >
            {items.map((item) => (
              <button
                key={item.id}
                type="button"
                role="menuitem"
                className={`${ui.focus} flex w-full items-start gap-3 rounded-control p-2.5 text-left transition-colors hover:bg-raised/80 max-[560px]:min-h-[44px]`}
                onClick={() => {
                  setOpen(false);
                  onPick(item.id);
                }}
              >
                <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-control bg-accent-soft text-accent ring-1 ring-accent-line/40">
                  {item.icon}
                </span>
                <span className="min-w-0">
                  <span className="block text-[13px] font-semibold text-ink">{item.title}</span>
                  <span className="mt-0.5 block text-[12px] leading-snug text-muted">
                    {item.text}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

import { Button, IconButton } from './Button';
import { ErrorMessage } from './Feedback';
import { ui } from './styles';
import { useEffect, useId, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';

export function Dialog({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const heading = useId();
  useEffect(() => {
    const previous = document.activeElement;
    const dialog = ref.current;
    dialog?.showModal();
    return () => {
      dialog?.close();
      // The opener (e.g. a menu item) may be gone by now: fall back to the control marked as the focus anchor.
      const target =
        previous instanceof HTMLElement && previous.isConnected
          ? previous
          : document.querySelector<HTMLElement>('[data-return-focus]');
      target?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby={heading}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      // A dialog opened from inside a clickable row must not leak its clicks/keys to that row (React bubbles through the tree).
      onClick={(event) => event.stopPropagation()}
      onDoubleClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key !== 'Tab') return;
        const elements = ref.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href]',
        );
        if (!elements?.length) return;
        const first = elements[0],
          last = elements[elements.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        }
        if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }}
      className={`m-auto ${wide ? 'w-[min(860px,calc(100%_-_32px))]' : 'w-[min(540px,calc(100%_-_32px))]'} max-h-[calc(100dvh_-_48px)] overflow-y-auto rounded-card border border-line bg-surface p-6 text-ink shadow-floating backdrop:bg-ink/30 dark:backdrop:bg-black/70 backdrop:backdrop-blur-md motion-safe:animate-appear [&>p]:mb-4 max-[560px]:p-4.5`}
    >
      <div className="mb-5 flex items-center justify-between gap-4">
        <h2
          id={heading}
          className="font-serif text-[18px] leading-snug font-semibold tracking-tight text-ink text-balance"
        >
          {title}
        </h2>
        <IconButton label="Close dialog" className="size-8" onClick={onClose}>
          <X size={16} aria-hidden="true" />
        </IconButton>
      </div>
      {children}
    </dialog>
  );
}

/**
 * Every "are you sure?" in the dashboard: body, the mutation's error, Cancel, and one confirm button. While the action is
 * running the dialog cannot be dismissed, so the result is never lost behind a closed modal.
 */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  pendingLabel,
  tone = 'destructive',
  pending,
  error,
  disabled = false,
  onConfirm,
  onClose,
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  pendingLabel?: string;
  tone?: 'destructive' | 'primary';
  pending: boolean;
  error?: unknown;
  disabled?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const close = () => {
    if (!pending) onClose();
  };
  return (
    <Dialog title={title} onClose={close}>
      <div className="flex flex-col gap-3 text-[13.5px] leading-relaxed [overflow-wrap:anywhere]">
        {children}
      </div>
      {error ? (
        <div className="mt-4">
          <ErrorMessage error={error} />
        </div>
      ) : null}
      <div className={ui.dialogActions}>
        <Button onClick={close} disabled={pending}>
          Cancel
        </Button>
        <Button variant={tone} loading={pending} disabled={disabled || pending} onClick={onConfirm}>
          {pending && pendingLabel ? pendingLabel : confirmLabel}
        </Button>
      </div>
    </Dialog>
  );
}

import { type ReactNode } from 'react';
import { Check } from 'lucide-react';
import { friendlyError } from '../../utils/humanize';
import { cn } from '../../utils/cn';
import { Button } from './Button';
import { ui } from './styles';

export function ErrorMessage({ error }: { error: unknown }) {
  if (!error) return null;
  const raw = error instanceof Error ? error.message : String(error);
  return (
    <div className={ui.error} role="alert">
      <p>{friendlyError(error)}</p>
      <details className="mt-1.5">
        <summary
          className={cn(
            ui.focus,
            'flex min-h-8 cursor-pointer items-center text-xs opacity-80 underline hover:opacity-100',
          )}
        >
          Show details
        </summary>
        <p className="mt-1 whitespace-pre-wrap font-mono text-[11px]">{raw}</p>
      </details>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn('rounded-control bg-line/60 motion-safe:animate-pulse', className)}
    />
  );
}

export function QueryState({
  pending,
  error,
  retry,
  children,
}: {
  pending: boolean;
  error: unknown;
  retry?: () => void;
  children: ReactNode;
}) {
  if (pending)
    return (
      <div className="grid min-w-0 gap-4 py-4" role="status" aria-label="Loading dashboard">
        <span className="sr-only">Loading…</span>
        <div aria-hidden="true" className="grid grid-cols-2 gap-4">
          <Skeleton className="h-20 rounded-card" />
          <Skeleton className="h-20 rounded-card" />
        </div>
        <div
          className="flex flex-col gap-4 rounded-card border border-line bg-surface p-6 shadow-card"
          aria-hidden="true"
        >
          <Skeleton className="h-4 w-1/4" />
          {[0, 1, 2].map((row) => (
            <div key={row} className="flex gap-4 border-t border-line/60 pt-4">
              <Skeleton className="size-9 shrink-0 rounded-control" />
              <div className="flex flex-1 flex-col gap-2">
                <Skeleton className="h-3 w-3/4" />
                <Skeleton className="h-3 w-1/3" />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  if (error)
    return (
      <div className={ui.state}>
        <ErrorMessage error={error} />
        {retry && <Button onClick={retry}>Try again</Button>}
      </div>
    );
  return <>{children}</>;
}

export function Empty({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3.5 px-4 py-12 text-center">
      <p className="max-w-[380px] text-[13.5px] leading-relaxed text-pretty text-muted">
        {children}
      </p>
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

export function MutationFeedback({
  error,
  success,
  message = 'Saved.',
}: {
  error: unknown;
  success: boolean;
  message?: string;
}) {
  return (
    <>
      <ErrorMessage error={error} />
      {success && (
        <p className={ui.success}>
          <Check size={14} aria-hidden="true" />
          {message}
        </p>
      )}
    </>
  );
}

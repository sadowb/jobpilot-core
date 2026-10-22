import { type ReactNode } from 'react';
import { cn } from '../../utils/cn';

export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        'inline-grid min-w-5 place-items-center rounded border border-line bg-raised/60 px-1 font-sans text-[11px] leading-4 text-muted',
        className,
      )}
    >
      {children}
    </kbd>
  );
}

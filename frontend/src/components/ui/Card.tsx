import { useId, type ComponentPropsWithoutRef, type ReactNode } from 'react';
import { CheckCheck } from 'lucide-react';
import { cn } from '../../utils/cn';
import { ui } from './styles';

export function Card({ children, className, ...props }: ComponentPropsWithoutRef<'section'>) {
  return (
    <section {...props} className={cn(ui.card, className)}>
      {children}
    </section>
  );
}

export function Panel({
  title,
  description,
  children,
  action,
  emphasis = false,
  id,
  className,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  action?: ReactNode;
  emphasis?: boolean;
  id?: string;
  className?: string;
}) {
  const generated = useId();
  const heading = id ? `${id}-title` : generated;
  return (
    <Card
      id={id}
      aria-labelledby={heading}
      className={cn(
        '[&>p]:mb-4',
        emphasis && 'border-accent-line/80 ring-1 ring-accent-line/30 shadow-md',
        className,
      )}
    >
      <div
        className={cn(
          'mb-5 flex flex-wrap items-center justify-between gap-4 max-[560px]:[&>button]:w-full',
          emphasis &&
            '-mx-6 -mt-6 rounded-t-card border-b border-accent-line/60 bg-accent-soft/60 px-6 py-4.5 max-[560px]:-mx-4.5 max-[560px]:-mt-4.5 max-[560px]:px-4.5',
        )}
      >
        <div className="min-w-0">
          <h2
            id={heading}
            className="flex items-center gap-2 font-serif text-[18px] leading-snug font-semibold tracking-tight text-ink text-balance"
          >
            {emphasis && (
              <CheckCheck className="size-4.5 shrink-0 text-accent" aria-hidden="true" />
            )}
            {title}
          </h2>
          {description && (
            <p className="mt-0.5 text-[13px] leading-relaxed text-pretty text-muted">
              {description}
            </p>
          )}
        </div>
        {action}
      </div>
      {children}
    </Card>
  );
}

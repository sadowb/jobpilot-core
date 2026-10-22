import { type ComponentPropsWithoutRef } from 'react';
import { LoaderCircle } from 'lucide-react';
import { cn } from '../../utils/cn';
import { useInView } from '../../hooks/useInView';
import { button, buttonSmall, segment, ui } from './styles';

function LoadingIndicator() {
  const { ref, visible } = useInView<SVGSVGElement>();
  return (
    <LoaderCircle
      ref={ref}
      className={cn('motion-safe:animate-spin', !visible && '[animation-play-state:paused]')}
      aria-hidden="true"
    />
  );
}

type ButtonProps = ComponentPropsWithoutRef<'button'> & {
  variant?: 'default' | 'secondary' | 'primary' | 'ghost' | 'danger' | 'destructive';
  size?: 'md' | 'sm';
  loading?: boolean;
};

export function Button({
  variant = 'default',
  size = 'md',
  loading = false,
  type = 'button',
  disabled,
  className,
  children,
  ...props
}: ButtonProps) {
  const tones = {
    default:
      'border-line bg-surface text-ink shadow-control enabled:hover:border-control enabled:hover:bg-raised/70',
    secondary:
      'border-line bg-surface text-ink shadow-control enabled:hover:border-control enabled:hover:bg-raised/70',
    primary:
      'border-transparent bg-accent text-accent-ink shadow-control enabled:hover:bg-accent-hover font-medium',
    ghost:
      'border-transparent bg-transparent text-muted shadow-none enabled:hover:bg-raised/80 enabled:hover:text-ink',
    danger:
      'border-danger/20 bg-danger-soft text-danger enabled:hover:border-danger/30 enabled:hover:bg-danger enabled:hover:text-accent-ink',
    // The confirm button of an irreversible or removing action (inside a confirmation dialog), never a row action.
    destructive:
      'border-transparent bg-danger text-accent-ink shadow-control enabled:hover:bg-danger-hover font-medium',
  };
  return (
    <button
      {...props}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(button, size === 'sm' && buttonSmall, tones[variant], className)}
    >
      {loading && <LoadingIndicator />}
      {children}
    </button>
  );
}

/** Square icon-only button. `label` becomes both the accessible name and the tooltip, so the two never drift apart. */
export function IconButton({
  label,
  tone = 'muted',
  className,
  children,
  ...props
}: Omit<ButtonProps, 'variant' | 'aria-label'> & { label: string; tone?: 'muted' | 'danger' }) {
  return (
    <Button
      {...props}
      variant="ghost"
      aria-label={label}
      title={props.title ?? label}
      className={cn(
        'size-9 min-h-0 min-w-0 shrink-0 p-0 max-[560px]:size-[44px]',
        tone === 'danger'
          ? 'text-muted enabled:hover:text-danger'
          : 'text-muted enabled:hover:text-ink',
        className,
      )}
    >
      {children}
    </Button>
  );
}

/** Inline text-link action that changes state rather than navigating (use `Link` + `ui.link` to navigate). */
export function LinkButton({
  muted = false,
  loading = false,
  type = 'button',
  disabled,
  className,
  children,
  ...props
}: ComponentPropsWithoutRef<'button'> & { muted?: boolean; loading?: boolean }) {
  return (
    <button
      {...props}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(muted ? ui.linkMuted : ui.link, className)}
    >
      {children}
    </button>
  );
}

/** Pill inside a `ui.segmented` track. Pass `role="radio"` + `aria-checked` or `aria-pressed` as the semantics require. */
export function Segment({
  active,
  type = 'button',
  className,
  children,
  ...props
}: ComponentPropsWithoutRef<'button'> & { active: boolean }) {
  return (
    <button {...props} type={type} className={cn(segment(active), className)}>
      {children}
    </button>
  );
}

import { type ReactNode } from 'react';
import { cn } from '../../utils/cn';
import { label } from '../../utils/format';
import { chip, ui } from './styles';

export function Chip({
  children,
  tone = 'neutral',
  dot = false,
  className,
}: {
  children: ReactNode;
  tone?: Tone;
  dot?: boolean;
  className?: string;
}) {
  const tones = {
    neutral: 'bg-raised text-muted ring-line',
    accent: 'bg-accent-soft text-accent ring-accent-line/60',
    ok: 'bg-ok-soft text-ok ring-ok/25',
    warn: 'bg-warn-soft text-warn ring-warn/30',
    danger: 'bg-danger-soft text-danger ring-danger/25',
    info: 'bg-info-soft text-info ring-info/25',
  };
  return (
    <span className={cn(chip, tones[tone], className)}>
      {dot && <span className={ui.dot} aria-hidden="true" />}
      {children}
    </span>
  );
}

export type Tone = 'neutral' | 'accent' | 'ok' | 'warn' | 'danger' | 'info';
export function statusTone(status: string): Tone {
  if (['submitted', 'offer', 'confirmed'].includes(status)) return 'ok';
  if (['rejected', 'failed'].includes(status)) return 'danger';
  if (['awaiting_approval', 'you', 'blocked', 'unconfirmed'].includes(status)) return 'warn';
  if (['preparing', 'running', 'screening', 'interview', 'ready'].includes(status)) return 'info';
  return 'neutral';
}

export function StatusChip({ status }: { status: string }) {
  return (
    <Chip tone={statusTone(status)} dot>
      {label(status)}
    </Chip>
  );
}

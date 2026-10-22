export function label(value: string) {
  const names: Record<string, string> = {
    awaiting_approval: 'Ready for you to submit',
    you: 'Needs you',
    blocked: 'Needs help',
    stopped: 'Paused',
  };
  const text = value.replaceAll('_', ' ');
  return names[value] ?? text.charAt(0).toUpperCase() + text.slice(1);
}
export function date(value: string | null, withTime = false) {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString(
        undefined,
        withTime ? { dateStyle: 'medium', timeStyle: 'short' } : { dateStyle: 'medium' },
      );
}
export function summarize(value: unknown): string {
  if (value === null || value === undefined) return '—';
  return typeof value === 'string' ? value : JSON.stringify(value);
}
export function safeUrl(value: string): string | undefined {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}
export const finalStatuses = [
  'submitted',
  'screening',
  'interview',
  'offer',
  'rejected',
  'withdrawn',
  'closed',
];

// Display ordering; the API accepts and returns status strings.
export const statuses = [
  'discovered',
  'screened',
  'preparing',
  'ready',
  'awaiting_approval',
  'submitted',
  'screening',
  'interview',
  'offer',
  'rejected',
  'withdrawn',
  'closed',
] as const;

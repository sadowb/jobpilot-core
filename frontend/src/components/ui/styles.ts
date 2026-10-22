const focus =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 focus-visible:ring-offset-surface';
const transition =
  'transition-[color,background-color,border-color,box-shadow,opacity,transform] duration-150 ease-out motion-reduce:transition-none motion-reduce:transform-none';
export const button = `inline-flex min-h-10 min-w-10 max-[560px]:min-h-[44px] items-center justify-center gap-2 rounded-control border px-3.5 py-2 text-[13px] font-medium tracking-tight cursor-pointer active:scale-[0.985] disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100 [&>svg]:size-4 [&>svg]:shrink-0 ${transition} ${focus}`;
export const buttonSmall = 'min-h-8 px-3 py-1 text-xs';
const field = `min-h-10 w-full min-w-0 rounded-control border border-line bg-surface px-3 py-2 text-[13px] text-ink shadow-control placeholder:text-muted/70 hover:border-control focus-visible:border-accent disabled:opacity-60 max-[560px]:min-h-[44px] max-[560px]:text-base ${transition} ${focus}`;
const actions = 'flex flex-wrap items-center gap-2.5';
export const chip =
  'inline-flex w-fit max-w-full items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-medium leading-5 tracking-tight ring-1 ring-inset ring-current/15 [overflow-wrap:anywhere]';

/** Selectable pill inside the `ui.segmented` track. */
export const segment = (active: boolean) =>
  `min-h-9 max-[560px]:min-h-[44px] rounded-full px-4 text-[13px] font-medium cursor-pointer disabled:cursor-not-allowed disabled:opacity-60 ${transition} ${focus} ${active ? 'bg-surface font-semibold text-ink shadow-sm' : 'text-muted hover:text-ink'}`;
/** Small bordered pill that toggles a filter. */
export const chipToggle = (active: boolean) =>
  `inline-flex min-h-8 max-[560px]:min-h-[44px] items-center gap-1.5 rounded-full border px-3 text-xs whitespace-nowrap cursor-pointer ${transition} ${focus} ${active ? 'border-accent-line bg-accent-soft font-medium text-accent' : 'border-line bg-surface text-muted hover:border-control hover:text-ink'}`;

export const ui = {
  focus,
  transition,
  link: `inline-flex min-h-10 max-[560px]:min-h-[44px] items-center gap-1 rounded-sm text-accent underline-offset-4 hover:underline disabled:cursor-not-allowed disabled:opacity-60 disabled:no-underline ${transition} ${focus}`,
  /** Quiet text control (muted until hovered) for secondary actions such as table-footer toggles. */
  linkMuted: `inline-flex min-h-10 max-[560px]:min-h-[44px] items-center gap-1 rounded-sm text-xs font-normal text-muted underline-offset-4 hover:text-ink hover:underline disabled:cursor-not-allowed disabled:opacity-60 ${transition} ${focus}`,
  /** Track holding a row of Segment buttons (radio group or pressed toggles). */
  segmented: 'inline-flex rounded-full border border-line/70 bg-raised/70 p-0.5',
  stat: `group relative flex min-w-0 flex-col gap-1.5 rounded-card border border-line bg-surface p-5 text-ink shadow-card hover:border-control hover:bg-raised/40 hover:no-underline [&>span]:text-xs [&>span]:font-normal [&>span]:text-muted [&_strong]:font-serif [&_strong]:text-[32px] [&_strong]:leading-tight [&_strong]:font-semibold [&_strong]:tracking-tight [&_strong]:tabular-nums [&_small]:font-normal [&_small]:text-muted [&_strong_small]:text-xs max-[560px]:p-4 ${transition} ${focus}`,
  buttonLinkPrimary: `${button} border-transparent bg-accent text-accent-ink font-medium shadow-control hover:bg-accent-hover hover:no-underline`,
  buttonLink: `${button} border-line bg-surface text-ink shadow-control hover:border-control hover:bg-raised/60 hover:no-underline`,
  compactLabel:
    'flex min-w-[130px] flex-1 flex-col gap-1.5 text-xs font-normal text-muted [&_select]:text-[13px] max-[560px]:min-w-0',
  code: 'font-mono text-xs tabular-nums text-muted bg-raised/80 px-1.5 py-0.5 rounded border border-line/40 [overflow-wrap:anywhere]',
  input: field,
  select: field,
  textarea: `${field} resize-y leading-relaxed`,
  checkbox: `m-0 size-[17px] shrink-0 rounded accent-accent cursor-pointer disabled:opacity-60 ${focus}`,
  label: 'flex min-w-0 flex-col gap-1.5 text-[13px] font-normal text-ink',
  checkLabel:
    'inline-flex min-h-10 min-w-0 cursor-pointer flex-row items-center gap-2.5 text-[13px] font-normal text-ink select-none',
  stack: 'flex flex-col gap-4',
  row: 'flex flex-wrap items-center justify-between gap-4',
  actions,
  dialogActions: `${actions} mt-5 justify-end`,
  card: 'mb-6 min-w-0 rounded-card border border-line bg-surface p-6 shadow-card max-[560px]:p-4.5',
  chip: `${chip} bg-raised text-muted ring-line`,
  dot: 'inline-block size-1.5 shrink-0 rounded-full bg-current opacity-85',
  tableScroll: `max-h-[65dvh] max-w-full overflow-auto rounded-card border border-line bg-surface shadow-card ${focus}`,
  table: `w-full border-separate border-spacing-0 text-left text-[13px] tabular-nums [&_tbody_tr]:bg-surface [&_tbody_tr]:hover:bg-raised/50 [&_tbody_tr]:focus-within:bg-raised/60 [&_tbody_tr]:transition-colors [&_tbody_tr]:duration-150 motion-reduce:[&_tbody_tr]:transition-none [&_tbody_tr:last-child_td]:border-b-0 [&_tbody_tr:last-child_th]:border-b-0 [&_thead_th]:sticky [&_thead_th]:top-0 [&_thead_th]:z-10 [&_thead_th]:bg-surface/95 [&_thead_th]:backdrop-blur-sm`,
  th: 'border-b border-line px-4 py-3 align-middle text-[12px] font-normal whitespace-nowrap text-muted select-none [&[scope=row]]:text-[13px] [&[scope=row]]:font-semibold [&[scope=row]]:text-ink',
  td: '[&_a]:inline-flex [&_a]:min-h-10 [&_a]:items-center border-b border-line/60 px-4 py-3.5 align-middle whitespace-nowrap text-ink',
  jobsList:
    'list-none [&_li]:flex [&_li]:flex-wrap [&_li]:items-center [&_li]:gap-4 [&_li]:border-t [&_li]:border-line/60 [&_li]:py-3.5 [&_li:first-child]:border-t-0 [&_li:first-child]:pt-0 [&_li:last-child]:pb-0 [&_li>a]:min-h-10 [&_li>a]:inline-flex [&_li>a]:items-center [&_li>a:first-child]:min-w-[120px] [&_li>a:first-child]:flex-1 [&_li>a:first-child]:flex-col [&_li>a:first-child]:items-start [&_li>a:first-child]:justify-center [&_li>a:first-child]:text-ink [&_strong]:block [&_strong]:font-semibold [&_a_span]:mt-0.5 [&_a_span]:block [&_a_span]:text-muted [&_a_small]:mt-0.5 [&_a_small]:block [&_a_small]:text-muted [&_label]:flex-1 max-[560px]:[&_li]:gap-x-4 max-[560px]:[&_li]:gap-y-2 max-[560px]:[&_li>code]:ml-[30px] max-[560px]:[&_li>code]:w-[calc(100%_-_30px)]',
  progress:
    'my-2 flex flex-wrap gap-x-8 gap-y-4 [&_dt]:mb-1 [&_dt]:text-xs [&_dt]:font-normal [&_dt]:text-muted [&_dd]:text-[26px] [&_dd]:font-light [&_dd]:tracking-tight [&_dd]:tabular-nums [&_dd]:text-ink',
  twoColumns: 'grid grid-cols-2 gap-6 max-[1120px]:grid-cols-1 max-[1120px]:gap-0',
  cardsGrid: 'grid grid-cols-2 gap-5 max-[1120px]:grid-cols-1',
  state: 'mx-auto flex w-full max-w-[560px] min-w-0 flex-col items-start gap-4 px-6 py-14',
  error:
    'rounded-control border border-danger/25 bg-danger-soft px-3.5 py-2.5 text-[13px] text-pretty text-danger [overflow-wrap:anywhere]',
  success: 'inline-flex items-center gap-1.5 text-[13px] font-medium text-ok',
  resultList:
    'list-none [&_li]:flex [&_li]:flex-col [&_li]:gap-1.5 [&_li]:border-t [&_li]:border-line/60 [&_li]:py-3.5 [&_a]:inline-flex [&_a]:min-h-10 [&_a]:items-center',
  notice:
    'mb-6 flex gap-3.5 rounded-card border border-stone/50 bg-raised/50 p-4.5 text-ink [&>svg]:mt-0.5 [&>svg]:shrink-0 [&>svg]:text-muted [&_p]:text-[13px] [&_p]:leading-relaxed [&_p]:text-pretty max-[560px]:p-4',
} as const satisfies Record<string, string>;

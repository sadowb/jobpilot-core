import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/api';
import type { SubmitMode } from '../../api/models';
import { useAction } from '../../hooks/useAction';
import { ErrorMessage, Panel, Segment, ui } from '../../components/ui';

const modes: { value: SubmitMode; title: string; hint: string }[] = [
  {
    value: 'review',
    title: 'Review first',
    hint: 'The agent fills the application and stops. You read it in Neo and click Submit.',
  },
  {
    value: 'auto',
    title: 'Auto-submit',
    hint: 'The agent fills the application, clicks Submit once and reports the confirmation.',
  },
];

/** The global submit-mode switch (ADR 0026). It applies to runs that start after the change. */
export function SubmitModeToggle() {
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.settings });
  const current: SubmitMode = settings.data?.submitMode ?? 'review';
  const save = useAction(
    (mode: SubmitMode) => api.saveSettings({ submitMode: mode }),
    'Submit mode saved. It applies to runs that start from now on.',
  );
  const choose = (mode: SubmitMode) => {
    if (!save.isPending && settings.data && mode !== current) save.mutate(mode);
  };
  return (
    <Panel title="Who clicks Submit?">
      <div
        role="radiogroup"
        aria-label="Submit mode"
        className={ui.segmented}
        onKeyDown={(event) => {
          const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[
            event.key as 'ArrowRight'
          ];
          if (!step) return;
          event.preventDefault();
          const next =
            modes[
              (modes.findIndex((m) => m.value === current) + step + modes.length) % modes.length
            ];
          event.currentTarget
            .querySelectorAll<HTMLButtonElement>('[role=radio]')
            [modes.indexOf(next)]?.focus();
          choose(next.value);
        }}
      >
        {modes.map((item) => (
          <Segment
            key={item.value}
            role="radio"
            aria-checked={current === item.value}
            tabIndex={current === item.value ? 0 : -1}
            disabled={!settings.data}
            aria-disabled={save.isPending || undefined}
            active={current === item.value}
            onClick={() => choose(item.value)}
          >
            {item.title}
          </Segment>
        ))}
      </div>
      <p className="mt-3 text-[13px] leading-relaxed text-muted text-pretty">
        {modes.find((m) => m.value === current)?.hint}
        {current === 'auto' &&
          ' Jobs fall back to review on their own when a required question was guessed, the CV was picked without reading the posting, an email code failed, or the board is not set to Auto; the job page says which.'}
      </p>
      <ErrorMessage error={settings.error ?? save.error} />
    </Panel>
  );
}

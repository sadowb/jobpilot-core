import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { CheckCheck } from 'lucide-react';
import { api } from '../../api/api';
import { LinkButton, ui } from '../../components/ui';

const KEY = 'jobpilot-checklist';
const read = () => {
  try {
    return localStorage.getItem(KEY) === 'done';
  } catch {
    return false;
  }
};

/** First-run setup card. Owns its own queries so a finished setup costs the Tracker nothing. */
export function Checklist({ hasRows, loaded }: { hasRows: boolean; loaded: boolean }) {
  const [dismissed, setDismissed] = useState(read);
  const profile = useQuery({ queryKey: ['profile'], queryFn: api.profile, enabled: !dismissed });
  const tracks = useQuery({ queryKey: ['tracks'], queryFn: api.tracks, enabled: !dismissed });
  if (dismissed || !loaded || !profile.isSuccess || !tracks.isSuccess) return null;
  const keys = new Set(
    profile.data.filter((item) => item.value !== null && item.value !== '').map((item) => item.key),
  );
  const steps = [
    {
      done: ['first_name', 'last_name', 'email', 'phone'].every((key) => keys.has(key)),
      title: '1. Fill in About me',
      text: 'Your name, email and phone, used to fill forms.',
      to: '/about',
      cta: 'Open About me',
    },
    {
      done: tracks.data.length > 0,
      title: '2. Check your CVs',
      text: 'JobPilot always sends “Abdelhadi Marjane.pdf”.',
      to: '/tracks',
      cta: 'Open CV tracks',
    },
    {
      done: hasRows,
      title: '3. Add your first applications',
      text: 'Import your sheet or paste a link above.',
      to: '',
      cta: '',
    },
  ];
  if (steps.every((step) => step.done)) return null;
  return (
    <section
      className="mb-6 rounded-card border border-line bg-surface p-5 shadow-card"
      aria-label="Getting started"
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="font-serif text-[15px] font-semibold tracking-tight text-ink">
          Get set up in three steps
        </h2>
        <LinkButton
          muted
          onClick={() => {
            try {
              localStorage.setItem(KEY, 'done');
            } catch {
              /* storage unavailable */
            }
            setDismissed(true);
          }}
        >
          Hide
        </LinkButton>
      </div>
      <ol className="grid list-none gap-3 sm:grid-cols-3">
        {steps.map((step) => (
          <li
            key={step.title}
            className={`rounded-control border p-4 transition-colors ${step.done ? 'border-sage/50 bg-sage/20 dark:bg-sage/10' : 'border-line bg-raised/40'}`}
          >
            <p className="flex items-center gap-2 text-[13px] font-medium text-ink">
              {step.done && <CheckCheck size={16} className="text-ok" aria-hidden="true" />}
              {step.title}
            </p>
            <p className="mt-1 text-xs leading-relaxed text-muted">{step.text}</p>
            {!step.done && step.to && (
              <Link className={`${ui.link} mt-2 text-xs`} to={step.to}>
                {step.cta}
              </Link>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

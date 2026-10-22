import { lazy, Suspense, useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import {
  Activity,
  BriefcaseBusiness,
  CircleHelp,
  FileText,
  History,
  Inbox,
  Moon,
  Sun,
  UserRound,
  Settings2,
} from 'lucide-react';
import { api, initSession, SessionRequiredError } from '../api/api';
import { useTheme } from './theme';
import { useLiveUpdates } from './live';
import { Button, Card, IconButton, ui, QueryState, ToastProvider } from '../components/ui';
import { Tracker } from '../features/tracker';

const JobDetail = lazy(() => import('../pages/JobDetail').then((m) => ({ default: m.JobDetail })));
const NeedsYou = lazy(() => import('../pages/NeedsYou').then((m) => ({ default: m.NeedsYou })));
const Automation = lazy(() =>
  import('../pages/Automation').then((m) => ({ default: m.Automation })),
);
const About = lazy(() => import('../pages/About').then((m) => ({ default: m.About })));
const Tracks = lazy(() => import('../pages/Tracks').then((m) => ({ default: m.Tracks })));
const Settings = lazy(() => import('../pages/Settings').then((m) => ({ default: m.Settings })));
const Updates = lazy(() => import('../pages/Updates').then((m) => ({ default: m.Updates })));

const navigation = [
  { to: '/', title: 'Applications', icon: BriefcaseBusiness },
  { to: '/needs-you', title: 'Needs you', icon: Inbox },
  { to: '/updates', title: 'Updates', icon: History },
  { to: '/automation', title: 'Automation', icon: Activity },
  { to: '/about', title: 'About me', icon: UserRound },
  { to: '/tracks', title: 'CV tracks', icon: FileText },
  { to: '/settings', title: 'Settings', icon: Settings2 },
];

function Dashboard() {
  const { theme, toggle } = useTheme();
  const connected = useLiveUpdates();
  const location = useLocation();
  const main = useRef<HTMLElement>(null);
  const firstRender = useRef(true);
  // The two numbers worth seeing from any page: how much is waiting on the owner, and whether agents are busy.
  const needs = useQuery({ queryKey: ['needs-you'], queryFn: api.needsYou });
  const automation = useQuery({ queryKey: ['automation'], queryFn: api.automation });
  const waiting = needs.data
    ? needs.data.blocked.length +
      needs.data.readyToApprove.filter(
        (job) => !needs.data.blocked.some((item) => item.id === job.id),
      ).length
    : 0;
  const working = automation.data?.running ? automation.data.active.length : 0;
  const badges: Record<string, { text: string; label: string; tone: string } | undefined> = {
    '/needs-you': waiting
      ? {
          text: waiting > 99 ? '99+' : String(waiting),
          label: `${waiting} waiting for you`,
          tone: 'bg-accent text-accent-ink',
        }
      : undefined,
    '/automation': working
      ? {
          text: String(working),
          label: `${working} agent${working === 1 ? '' : 's'} working`,
          tone: 'bg-accent-soft text-accent ring-1 ring-inset ring-accent-line/70',
        }
      : undefined,
  };

  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    main.current?.focus();
    if (!location.hash) window.scrollTo(0, 0);
  }, [location.pathname, location.hash]);

  // "/" jumps to the page's main search box (SearchField primary), as in GitHub, Gmail or Linear.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (
        event.key !== '/' ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        event.defaultPrevented
      )
        return;
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
      )
        return;
      if (document.querySelector('dialog[open]')) return;
      const field = document.querySelector<HTMLInputElement>('[data-search-shortcut]');
      if (!field) return;
      event.preventDefault();
      field.focus();
      field.select();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const currentPage =
    navigation.find((item) => item.to === location.pathname)?.title ??
    (location.pathname.startsWith('/jobs/') ? 'Application' : 'Page not found');
  useEffect(() => {
    document.title = `${currentPage} · JobPilot`;
  }, [currentPage]);

  return (
    <div className="grid min-h-dvh grid-cols-[220px_minmax(0,1fr)] max-[860px]:block bg-ground">
      <a
        className={`${ui.focus} fixed -top-20 left-4 z-50 rounded-control border border-accent bg-surface p-3 text-[13px] font-medium text-accent shadow-floating focus:top-4`}
        href="#main-content"
      >
        Skip to content
      </a>
      <aside className="sticky top-0 flex h-dvh flex-col gap-6 overflow-y-auto border-r border-line bg-sidebar px-3.5 pt-5 pb-[max(20px,env(safe-area-inset-bottom))] max-[860px]:static max-[860px]:h-auto max-[860px]:gap-4 max-[860px]:border-r-0 max-[860px]:border-b max-[860px]:px-6 max-[860px]:py-4 max-[560px]:px-4">
        <div className="flex items-center justify-between gap-3 px-1.5">
          <Link
            className={`${ui.focus} flex min-h-10 max-[560px]:min-h-[44px] items-center gap-2.5 rounded-control text-ink hover:no-underline`}
            to="/"
            aria-label="JobPilot home"
          >
            <span className="grid size-7 shrink-0 place-items-center rounded-[8px] bg-accent text-accent-ink shadow-sm">
              <BriefcaseBusiness size={14} aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <span className="block font-serif text-[18px] leading-tight font-semibold tracking-tight text-ink">
                JobPilot
              </span>
            </div>
          </Link>
          <div className="hidden items-center gap-2 max-[860px]:flex">
            {!connected && (
              <span className="flex items-center gap-1.5 text-xs text-muted" role="status">
                <span className={`${ui.dot} text-warn`} aria-hidden="true" />
                Reconnecting…
              </span>
            )}
            <IconButton
              label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
              onClick={toggle}
            >
              {theme === 'dark' ? (
                <Sun size={15} aria-hidden="true" />
              ) : (
                <Moon size={15} aria-hidden="true" />
              )}
            </IconButton>
          </div>
        </div>
        <nav
          aria-label="Main navigation"
          className="flex flex-col gap-5 max-[860px]:flex-row max-[860px]:flex-wrap max-[860px]:gap-1"
        >
          {[navigation.slice(0, 4), navigation.slice(4)].map((items, index) => (
            <div key={index} className="max-[860px]:contents">
              <p className="mb-1.5 px-3 text-[11px] font-normal text-muted max-[860px]:hidden">
                {index === 0 ? 'Workspace' : 'Manage'}
              </p>
              <div className="grid gap-0.5 max-[860px]:contents">
                {items.map(({ to, title, icon: Icon }) => (
                  <NavLink
                    end={to === '/'}
                    key={to}
                    to={to}
                    className={`${ui.focus} relative flex min-h-9 items-center gap-2.5 rounded-full px-3 py-1.5 text-[13px] font-normal text-muted transition-[background-color,color,box-shadow] duration-150 ease-out hover:bg-raised/60 hover:text-ink hover:no-underline [&.active]:bg-sage/40 dark:[&.active]:bg-sage/20 [&.active]:font-medium [&.active]:text-ink [&.active_svg]:text-accent max-[860px]:shrink-0 max-[860px]:justify-center max-[860px]:gap-2 max-[860px]:whitespace-nowrap max-[860px]:px-3 max-[560px]:flex-col max-[560px]:gap-1 max-[560px]:py-2 max-[560px]:text-[11px]`}
                  >
                    <Icon size={15} className="shrink-0 transition-colors" aria-hidden="true" />
                    <span>{title}</span>
                    {badges[to] && (
                      <span
                        className={`ml-auto min-w-5 rounded-full px-1.5 text-center text-[11px] leading-5 font-medium tabular-nums max-[860px]:ml-0 max-[560px]:absolute max-[560px]:top-0.5 max-[560px]:right-1.5 max-[560px]:min-w-4 max-[560px]:px-1 max-[560px]:text-[10px] max-[560px]:leading-4 ${badges[to].tone}`}
                      >
                        <span aria-hidden="true">{badges[to].text}</span>
                        <span className="sr-only">{badges[to].label}</span>
                      </span>
                    )}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>
      </aside>
      <main
        id="main-content"
        ref={main}
        tabIndex={-1}
        className="min-w-0 [overflow-wrap:anywhere] focus:outline-none bg-ground"
      >
        <div className="sticky top-0 z-20 flex min-h-12 items-center justify-between gap-4 border-b border-line/70 bg-ground/85 px-8 backdrop-blur-md max-[1120px]:px-6 max-[860px]:hidden">
          <p className="text-[13px] font-normal text-muted">{currentPage}</p>
          <div className="flex items-center gap-3">
            {!connected && (
              <span className="flex items-center gap-1.5 text-xs text-muted" role="status">
                <span className={`${ui.dot} text-warn`} aria-hidden="true" />
                Reconnecting…
              </span>
            )}
            <IconButton
              label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
              className="size-8"
              onClick={toggle}
            >
              {theme === 'dark' ? (
                <Sun size={15} aria-hidden="true" />
              ) : (
                <Moon size={15} aria-hidden="true" />
              )}
            </IconButton>
          </div>
        </div>
        <div className="mx-auto max-w-[1280px] px-8 py-8 max-[1120px]:px-6 max-[1120px]:py-7 max-[560px]:px-4 max-[560px]:py-5">
          <Suspense
            fallback={
              <QueryState pending error={null}>
                {null}
              </QueryState>
            }
          >
            <Routes>
              <Route path="/" element={<Tracker />} />
              <Route path="/tracker" element={<Navigate to="/" replace />} />
              <Route path="/today" element={<Navigate to="/" replace />} />
              <Route path="/pipeline" element={<Navigate to="/" replace />} />
              <Route path="/approvals" element={<Navigate to="/needs-you" replace />} />
              <Route path="/jobs/:id" element={<JobDetail />} />
              <Route path="/needs-you" element={<NeedsYou />} />
              <Route path="/updates" element={<Updates />} />
              <Route path="/automation" element={<Automation />} />
              <Route path="/about" element={<About />} />
              <Route path="/tracks" element={<Tracks />} />
              <Route path="/settings" element={<Settings />} />
              <Route
                path="*"
                element={
                  <section className={ui.card}>
                    <CircleHelp size={22} className="text-muted" />
                    <h1 className="mt-3 font-serif text-[24px] leading-tight font-semibold tracking-tight text-ink text-balance">
                      Page not found
                    </h1>
                    <p className="mt-1 leading-relaxed text-muted">
                      This page isn’t part of the dashboard.
                    </p>
                    <div className="mt-4">
                      <Link className={ui.buttonLink} to="/">
                        Go to Applications
                      </Link>
                    </div>
                  </section>
                }
              />
            </Routes>
          </Suspense>
        </div>
      </main>
    </div>
  );
}

export function App() {
  const session = useQuery({
    queryKey: ['session'],
    queryFn: async () => {
      await initSession();
      return true;
    },
    staleTime: Infinity,
    retry: false,
    refetchInterval: false,
  });
  if (session.error)
    return (
      <main className="mx-auto max-w-xl px-6 py-12">
        <Card role="alert">
          <p className="text-pretty leading-relaxed">
            {session.error instanceof SessionRequiredError
              ? 'Open JobPilot at http://127.0.0.1:4100 in a normal browser window.'
              : 'JobPilot could not connect. Please try again.'}
          </p>
          {!(session.error instanceof SessionRequiredError) && (
            <Button
              className="mt-4"
              onClick={() => {
                void session.refetch();
              }}
            >
              Try again
            </Button>
          )}
        </Card>
      </main>
    );
  return (
    <ToastProvider>
      <QueryState
        pending={session.isPending}
        error={session.error}
        retry={() => {
          void session.refetch();
        }}
      >
        <Dashboard />
      </QueryState>
    </ToastProvider>
  );
}

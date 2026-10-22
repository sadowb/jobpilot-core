# frontend

Vite + React + TypeScript. Dashboard only; no business rules here.

- Talks only to the FastAPI backend (`/api/*`, same origin in production; Vite proxy in dev). Cookie session + `X-CSRF-Token` for writes.
- Render posting/site text as inert text. No inline scripts (strict CSP); no `dangerouslySetInnerHTML`.
- Approvals are buttons that call the authenticated approvals route; the UI never fabricates approval state.
- Screens: Today, Pipeline (table + kanban), Job detail, Needs you, Automation, About me, CV tracks. Needs You groups every unresolved question for a job into one batch with a single send action.
- Styling uses Tailwind CSS v4 utility classes, with design tokens in the `@theme` block in `src/styles/tailwind.css`. Shared controls live in `src/components/ui/`; style strings live in `src/components/ui/styles.ts`; keep component styling in utilities.

- `app/` owns routing, providers, theme and live cache updates; `pages/` composes screens; `features/` owns feature components and hooks; `hooks/` is for reusable React hooks; `utils/` is for ordinary helper functions.
- Use `pnpm --filter @jobpilot/frontend lint` for React hook rules and `format:check` for Prettier. Buttons default to `type="button"`; form submission buttons must explicitly use `type="submit"`.

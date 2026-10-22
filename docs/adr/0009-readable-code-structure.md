# ADR 0009 — Feature UI and layered FastAPI code

Status: superseded by [ADR 0010](0010-spring-style-layers-api.md) (2026-10-11)

## Context

The owner requested a structure that frontend and backend engineers can navigate. Shared React controls, feature components and hooks were mixed in `components/` and `lib/`. The FastAPI module mixed startup, security, Pydantic models, SQL reads and all endpoint implementations. The service implementation was nearly 2,000 lines.

## Decision

The frontend uses `app/`, `pages/`, `features/`, `components/ui/`, `hooks/`, `utils/`, `api/` and `styles/`. The former UI module is split into styles, controls, cards, feedback, chips and toasts. JSX is formatted by Prettier; ESLint checks React hook rules and dependencies.

The backend uses `config/`, `routes/`, `controllers/`, `middlewares/`, `models/`, `repositories/` and `services/`, alongside the existing pure domain and IO adapters. FastAPI routers map paths to controller functions. Controllers validate/shapes HTTP input/output; Pydantic contracts live in models. Dashboard read SQL lives in a named repository; its connection implementation retains pooling and nested transaction semantics.

A typed Services facade remains the single application entry point for HTTP, CLI and workers. Feature service objects contain the original implementations and share that facade's DB connection, budget/lease context and CV store. Transaction and context-manager decorators remain on the facade; feature implementations are internal and callers must not bypass it. Existing service write SQL remains with the transactions it participates in; splitting every write into repositories is a separate future change, not a second write path.

PostgreSQL schema remains in the existing forward-only SQL migrations. Pydantic models are HTTP contracts, not ORM table entities. No ORM, database migration, additional application process or MVC view layer is introduced. The React dashboard is the view. URLs, operation IDs and OpenAPI schema remain unchanged.

## Consequences

Engineers can follow a URL through a route, controller and named application operation. CLI and workers keep the same Services API. There are more focused modules and explicit forwarding methods, with no dynamic attribute delegation. The service facade is deliberately larger than other files because its signatures document the supported toolbox. HTTP-only global wiring is isolated in `config/runtime.py`; the database pool opens at API lifespan startup. Agent approval gates, secret handling and the projection writer are preserved.

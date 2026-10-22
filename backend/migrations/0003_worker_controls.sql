-- Human-only control flag; process heartbeat/status is separate from desired state.
CREATE TABLE automation (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    enabled boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now()
);
--> statement-breakpoint
INSERT INTO automation(singleton) VALUES(true);
--> statement-breakpoint
CREATE TABLE worker_processes (
    name text PRIMARY KEY,
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    status jsonb NOT NULL DEFAULT '{}'
);
--> statement-breakpoint
CREATE TABLE domain_pacing (
    domain text PRIMARY KEY,
    last_hit timestamptz NOT NULL
);
--> statement-breakpoint
-- Unsettled reservations stay charged on crashes/timeouts: unknown spend fails closed.
CREATE TABLE budget_reservations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id text NOT NULL REFERENCES jobs(id),
    day date NOT NULL DEFAULT (now() AT TIME ZONE 'UTC')::date,
    usd numeric NOT NULL CHECK (usd >= 0),
    tokens bigint NOT NULL CHECK (tokens >= 0),
    settled boolean NOT NULL DEFAULT false
);
--> statement-breakpoint
CREATE INDEX budget_reservations_open_day ON budget_reservations(day) WHERE NOT settled;

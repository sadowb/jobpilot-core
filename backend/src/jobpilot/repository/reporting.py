"""PostgreSQL operations for reporting. Transactions are owned by application services."""

from __future__ import annotations

from typing import Any

from .database import Database, Row


class ReportingRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def insert_job_events_for_event(self, job_id: str | None, kind: str, actor: str, value: Any) -> int:
        return self.db.run("INSERT INTO job_events(job_id,kind,actor,payload) VALUES(%s,%s,%s,%s)", (job_id, kind, actor, value))

    def insert_cost_ledger_for_cost(
        self, job_id: str | None, model: str, tokens_in: int, tokens_out: int, usd: float, step: str, from_cache: bool
    ) -> int:
        return self.db.run(
            "INSERT INTO cost_ledger(job_id,model,tokens_in,tokens_out,usd,step,from_cache) VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (job_id, model, tokens_in, tokens_out, usd, step, from_cache),
        )

    def list_jobs_for_stats(self) -> list[Row]:
        return self.db.all("SELECT status, waiting_on, count(*)::int AS n FROM jobs GROUP BY 1,2")

    def find_cost_ledger_for_stats(self) -> Row | None:
        return self.db.one(
            # Historical unknown_model_usage entries are reservation estimates, not metered spend.
            "SELECT COALESCE(sum(usd),0)::float AS usd, COALESCE(sum(tokens_in+tokens_out),0)::bigint::text AS tokens "
            "FROM cost_ledger WHERE ts > now() - interval '24 hours' AND step IS DISTINCT FROM 'unknown_model_usage'"
        )

    def find_applications_for_stats(self) -> Row | None:
        return self.db.one("SELECT count(*)::int AS n FROM applications WHERE applied_on = current_date")

    def list_unapproved_submissions(self) -> list[Row]:
        return self.db.all("SELECT id FROM jobs WHERE blocked_reason='SUBMITTED WITHOUT APPROVAL' ORDER BY id")

    def find_job_events_for_event_watermark(self) -> Row | None:
        return self.db.one("SELECT COALESCE(max(id),0)::bigint AS n FROM job_events")

    def find_automation_for_automation_status(self) -> Row | None:
        return self.db.one("""SELECT enabled,
               (SELECT payload->>'reason' FROM job_events WHERE kind='automation_changed' AND job_id IS NULL
                ORDER BY id DESC LIMIT 1) AS stop_reason
               FROM automation WHERE singleton""")

    def list_fresh_worker_processes(self, fresh_seconds: float) -> list[Row]:
        """Heartbeats inside the freshness window (control and jobs rows of every node, ADR 0012)."""
        return self.db.all(
            """SELECT name, node, status FROM worker_processes
               WHERE name !~ '^worker-[0-9]+$' AND heartbeat_at > now() - make_interval(secs => %s)""",
            (fresh_seconds,),
        )

    def find_task_outcomes_today(self) -> Row | None:
        """Runs recorded today (UTC) by `task_finished`, and runs started today that are still running."""
        return self.db.one(
            """SELECT
                 (SELECT count(*) FROM job_events WHERE kind='task_finished'
                    AND ts >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')::int AS finished_total,
                 (SELECT count(*) FROM job_events WHERE kind='task_finished'
                    AND payload->>'outcome' IN ('done','blocked')
                    AND ts >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')::int AS finished,
                 (SELECT count(*) FROM job_events WHERE kind='task_finished'
                    AND payload->>'outcome'='queued' AND payload->>'refunded'='false'
                    AND ts >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')::int AS failed,
                 (SELECT count(*) FROM tasks WHERE state='leased' AND visible_at>now() AND started_at >=
                    date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')::int AS running"""
        )

    def count_parked_forms(self, current_node: str) -> int:
        """Filled forms keeping a browser while they wait for the owner (no agent holds them)."""
        row = self.db.one(
            """SELECT count(*)::int AS n FROM browser_sessions b JOIN jobs j ON j.id=b.job_id
               WHERE NOT b.close_requested AND b.node IS NOT NULL AND (b.node<>%s OR b.target_id IS NOT NULL)
                 AND j.status='awaiting_approval' AND j.archived_at IS NULL
                 AND NOT EXISTS(SELECT 1 FROM tasks t WHERE t.job_id=b.job_id AND t.state='leased' AND t.visible_at>now())""",
            (current_node,),
        )
        return int(row["n"]) if row else 0

    def count_cap_blocked_jobs(self, current_node: str) -> int:
        """Queued preparation jobs that only the open-browser cap keeps from starting."""
        row = self.db.one(
            """SELECT count(*)::int AS n FROM tasks t JOIN jobs j ON j.id=t.job_id
               WHERE t.state IN ('queued','leased') AND t.visible_at<=now() AND t.attempts<5
                 AND j.waiting_on='agent' AND j.archived_at IS NULL
                 AND j.status <> 'awaiting_approval'
                 AND NOT EXISTS(SELECT 1 FROM browser_sessions s WHERE s.job_id=j.id AND NOT s.close_requested
                                AND s.node IS NOT NULL AND (s.node<>%s OR s.target_id IS NOT NULL))""",
            (current_node,),
        )
        return int(row["n"]) if row else 0

    def list_active_workers(self) -> list[Row]:
        return self.db.all("""SELECT t.leased_by AS worker, t.job_id AS job,
                 (extract(epoch FROM COALESCE(t.leased_at, t.heartbeat_at))*1000)::bigint AS since,
                 j.role, c.name AS company, j.url, t.leased_node AS node,
                 t.started_at IS NOT NULL AS started,
                 greatest(0,extract(epoch FROM now()-t.started_at))::int AS "elapsedSeconds",
                 (SELECT e.kind FROM job_events e WHERE e.job_id=t.job_id ORDER BY e.id DESC LIMIT 1) AS step
               FROM tasks t JOIN jobs j ON j.id=t.job_id LEFT JOIN companies c ON c.id=j.company_id
               WHERE t.state='leased' AND t.visible_at>now() ORDER BY t.leased_by""")

    def usage_windows(self) -> list[Row]:
        """UTC today, today plus six previous UTC dates, and all retained history. No admission decisions."""
        return self.db.all("""WITH periods(period, since) AS (
                 VALUES ('today', date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'),
                        ('last7Days', (date_trunc('day',now() AT TIME ZONE 'UTC') - interval '6 days') AT TIME ZONE 'UTC'),
                        ('allTime', '-infinity'::timestamptz)
               )
               SELECT p.period, c.usd, c.tokens, c.jobs, c.job_usd, r.runs
               FROM periods p CROSS JOIN LATERAL (
                 SELECT COALESCE(sum(usd),0)::float AS usd,
                   COALESCE(sum(tokens_in::bigint+tokens_out),0)::bigint AS tokens,
                   count(DISTINCT job_id)::int AS jobs,
                   COALESCE(sum(usd) FILTER (WHERE job_id IS NOT NULL),0)::float AS job_usd
                 FROM cost_ledger WHERE ts>=p.since AND ts<=now() AND step IS DISTINCT FROM 'unknown_model_usage'
               ) c CROSS JOIN LATERAL (
                 SELECT count(*)::int AS runs FROM (
                   SELECT payload->>'task' FROM job_events WHERE kind='task_finished' AND ts>=p.since AND ts<=now()
                   UNION ALL
                   SELECT leased_by FROM tasks t WHERE state='leased' AND visible_at>now()
                     AND started_at>=p.since AND started_at<=now()
                     AND NOT EXISTS (SELECT 1 FROM job_events e WHERE e.kind='task_finished'
                                     AND e.job_id=t.job_id AND e.payload->>'task'=t.leased_by)
                 ) started
               ) r""")

    def find_recent_requeue(self) -> Row | None:
        """The most frequent reason runs were released for retry in the last 10 minutes (`run_requeued` events)."""
        return self.db.one(
            """SELECT payload->>'reason' AS reason, count(*)::int AS n FROM job_events
               WHERE kind='run_requeued' AND ts > now() - interval '10 minutes'
               GROUP BY 1 ORDER BY n DESC, 1 LIMIT 1"""
        )

    def queue_depth(self) -> int:
        row = self.db.one("""SELECT count(*)::int AS n FROM tasks t JOIN jobs j ON j.id=t.job_id
               WHERE j.archived_at IS NULL AND j.waiting_on='agent' AND t.attempts<5
                 AND (t.state='queued' OR (t.state='leased' AND t.visible_at<=now()))""")
        return int(row["n"]) if row else 0

    def recent_failed_runs(self) -> list[Row]:
        # Bound errors to this run's event interval; honor a task id when present.
        return self.db.all("""SELECT f.payload->>'task' AS task, f.job_id AS job, j.role, c.name AS company,
                 f.ts AS at, f.payload->>'node' AS node,
                 left(COALESCE(err.payload->>'detail', err.payload->>'message', err.payload->>'reason', err.kind),500)
                   AS "lastError"
               FROM job_events f JOIN jobs j ON j.id=f.job_id LEFT JOIN companies c ON c.id=j.company_id
               JOIN LATERAL (
                 SELECT e.kind,e.payload FROM job_events e WHERE e.job_id=f.job_id
                   AND e.kind IN ('error','agent_run_failed','agent_timeout','agent_stalled','agent_unavailable')
                   AND e.id<f.id AND e.id>COALESCE((SELECT max(prev.id) FROM job_events prev
                     WHERE prev.job_id=f.job_id AND prev.kind='task_finished' AND prev.id<f.id),0)
                   AND (e.payload->>'task' IS NULL OR e.payload->>'task'=f.payload->>'task')
                 ORDER BY e.id DESC LIMIT 1
               ) err ON true
               WHERE f.kind='task_finished' AND f.payload->>'outcome' IN ('queued','blocked')
                 AND j.archived_at IS NULL ORDER BY f.id DESC LIMIT 10""")

    def job_usage(self, job_id: str) -> Row:
        row = self.db.one(
            """SELECT COALESCE(sum(usd),0)::float AS usd,
                 COALESCE(sum(tokens_in::bigint+tokens_out),0)::bigint AS tokens
               FROM cost_ledger WHERE job_id=%s AND step IS DISTINCT FROM 'unknown_model_usage'""",
            (job_id,),
        )
        assert row is not None  # aggregate without GROUP BY always returns one row
        return row

    def list_jobs(
        self, status: str | None = None, waiting_on: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Row]:
        where: list[str] = []
        args: list[Any] = []
        if status:
            where.append("j.status=%s::job_status")
            args.append(status)
        if waiting_on:
            where.append("j.waiting_on=%s::waiting_on")
            args.append(waiting_on)
        if q:
            where.append("(j.role ILIKE %s OR c.name ILIKE %s)")
            args += [f"%{q}%", f"%{q}%"]
        args += [min(limit, 200), offset]
        sql = f"""SELECT j.id, c.name AS company, j.role, j.url, j.board_id AS board, j.status, j.waiting_on AS "waitingOn",
                         j.cv_track_id AS "cvTrack", j.cv_mode AS "cvMode", j.match_score AS "matchScore", a.applied_on::text AS "appliedOn",
                         j.updated_at AS "updatedAt", j.blocked_reason AS "blockedReason"
                  FROM jobs j LEFT JOIN companies c ON c.id=j.company_id LEFT JOIN applications a ON a.job_id=j.id
                  {"WHERE " + " AND ".join(where) if where else ""} ORDER BY j.updated_at DESC LIMIT %s OFFSET %s"""
        return self.db.all(sql, args)

    def event_feed(self, kinds: list[str], limit: int, before: int | None) -> list[Row]:
        args: list[Any] = [kinds]
        cursor = ""
        if before is not None:
            cursor = "AND e.id < %s"
            args.append(before)
        args.append(limit + 1)
        return self.db.all(
            f"""SELECT e.id, e.ts, e.kind, e.actor, e.payload, e.job_id AS "jobId", j.role, c.name AS company
                    FROM job_events e LEFT JOIN jobs j ON j.id=e.job_id LEFT JOIN companies c ON c.id=j.company_id
                    WHERE e.kind = ANY(%s) AND (j.archived_at IS NULL) {cursor}
                    ORDER BY e.id DESC LIMIT %s""",
            args,
        )

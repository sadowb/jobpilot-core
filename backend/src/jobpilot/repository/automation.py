"""PostgreSQL operations for automation. Transactions are owned by application services."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .database import Database, Row


class AutomationRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def find_task_attempts(self, job_id: str) -> Row | None:
        return self.db.one("SELECT attempts FROM tasks WHERE job_id=%s", (job_id,))

    def update_tasks_for_heartbeat(self, lease_seconds: int, job_id: str, worker: str) -> int:
        # Never inherit a caller's transaction or keep a connection during model execution.
        with self.db.independent_conn() as connection:
            connection.execute("SET statement_timeout = '5s'")
            return connection.execute(
                """UPDATE tasks SET heartbeat_at=now(), visible_at=now() + make_interval(secs => %s)
                   WHERE job_id=%s AND state='leased' AND leased_by=%s AND visible_at>now()""",
                (lease_seconds, job_id, worker),
            ).rowcount

    def update_tasks_for_release(
        self, state: str, refund_attempt: bool, state_3: str, delay: int, job_id: str, worker: str
    ) -> int:
        return self.db.run(
            """UPDATE tasks SET state=%s::task_state, leased_by=NULL, started_at=NULL,
                 attempts=greatest(0,attempts-CASE WHEN %s THEN 1 ELSE 0 END),
                 visible_at=CASE WHEN %s::text='queued' THEN now() + make_interval(secs => %s) ELSE visible_at END
               WHERE job_id=%s AND state='leased' AND leased_by=%s AND visible_at>now()""",
            (state, refund_attempt, state_3, delay, job_id, worker),
        )

    def lock_run_for_finish(self, task_id: str) -> None:
        # Completion also works after `jp done` releases the task, so the task lease alone cannot serialize retries.
        self.db.run("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"jobpilot:finish:{task_id}",))

    def has_finished_run(self, job_id: str, task_id: str) -> bool:
        return (
            self.db.one(
                "SELECT 1 FROM job_events WHERE job_id=%s AND kind='task_finished' AND payload->>'task'=%s LIMIT 1",
                (job_id, task_id),
            )
            is not None
        )

    def find_task_lease(self, job_id: str, task_id: str) -> Row | None:
        """The lease `task_id` holds on the job (locked for the rest of the transaction), or None when it is not ours."""
        return self.db.one(
            """SELECT leased_node AS node, started_at FROM tasks
               WHERE job_id=%s AND state='leased' AND leased_by=%s FOR UPDATE""",
            (job_id, task_id),
        )

    def update_tasks_for_finish_run(self, state: str, refund_attempt: bool, delay: int, job_id: str, task_id: str) -> int:
        """Release a lease owned by `task_id`. Unlike a heartbeat-bound release it also closes an expired but unclaimed lease."""
        return self.db.run(
            """UPDATE tasks SET state=%s::task_state, leased_by=NULL, started_at=NULL,
                 attempts=greatest(0,attempts-CASE WHEN %s THEN 1 ELSE 0 END),
                 visible_at=CASE WHEN %s::text='queued' THEN now() + make_interval(secs => %s) ELSE visible_at END
               WHERE job_id=%s AND state='leased' AND leased_by=%s""",
            (state, refund_attempt, state, delay, job_id, task_id),
        )

    def update_tasks_for_dispatch_failed(self, job_id: str, task_id: str, retry_in: int) -> int:
        return self.db.run(
            """UPDATE tasks SET state='queued', leased_by=NULL, started_at=NULL, leased_node=NULL,
                 visible_at=now() + make_interval(secs => %s)
               WHERE job_id=%s AND state='leased' AND leased_by=%s AND started_at IS NULL""",
            (retry_in, job_id, task_id),
        )

    def update_tasks_for_start_run(self, job_id: str, task_id: str, node: str, lease_seconds: int) -> Row | None:
        """Atomic start: only the delivery that holds the unstarted, unexpired lease wins; duplicates, stale ids and
        deliveries that outlived their dispatch lease update 0 rows (dispatch may already have admitted a replacement,
        so starting this one too would exceed workerConcurrency). The attempt is charged here, not at dispatch: a
        lease that expires because no worker ever picked it up must not burn the job's retries."""
        return self.db.one(
            """UPDATE tasks SET started_at=now(), leased_node=%s, heartbeat_at=now(), attempts=attempts+1,
                 visible_at=now() + make_interval(secs => %s)
               WHERE job_id=%s AND state='leased' AND leased_by=%s AND started_at IS NULL AND visible_at>now()
               RETURNING job_id,
                 EXISTS(SELECT 1 FROM browser_sessions s WHERE s.job_id=tasks.job_id AND NOT s.close_requested
                        AND s.target_id IS NOT NULL AND s.node=%s) AS has_session""",
            (node, lease_seconds, job_id, task_id, node),
        )

    def lock_node_for_start(self, node: str) -> None:
        """Serialize start_run's count-then-start per node: concurrent prefork children must not all pass the browser
        cap check against the same count. Transaction-scoped; released on commit/rollback."""
        self.db.run("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"jobpilot:node-start:{node}",))

    def release_unstarted_for_cap(self, job_id: str, task_id: str, delay: int) -> int:
        """start_run found this node full: hand the job back, undoing the attempt it just charged."""
        return self.db.run(
            """UPDATE tasks SET state='queued', leased_by=NULL, started_at=NULL, leased_node=NULL,
                 attempts=greatest(0,attempts-1), visible_at=now() + make_interval(secs => %s)
               WHERE job_id=%s AND state='leased' AND leased_by=%s""",
            (delay, job_id, task_id),
        )

    def count_active_leases(self) -> int:
        """Dispatched or running leases across the cluster; they are what workerConcurrency bounds."""
        row = self.db.one("SELECT count(*)::int AS n FROM tasks WHERE state='leased' AND visible_at>now()")
        return int(row["n"]) if row else 0

    def sum_job_slots(self, fresh_seconds: float) -> int:
        """Pool slots of every job worker whose heartbeat is fresh and not offline: the cluster's real capacity."""
        row = self.db.one(
            """SELECT COALESCE(sum((status->>'slots')::int), 0)::int AS n FROM worker_processes
               WHERE name !~ '^worker-[0-9]+$' AND node IS NOT NULL AND status->>'role'='jobs'
                 AND NOT COALESCE((status->>'offline')::boolean,false)
                 AND heartbeat_at > now() - make_interval(secs => %s)""",
            (fresh_seconds,),
        )
        return int(row["n"]) if row else 0

    def count_open_browsers(self, live_nodes: Sequence[str], current_node: str) -> int:
        """Jobs that hold or are about to hold a browser: open sessions plus live leases (running agents). A session on a
        node outside `live_nodes` (offline past the takeover window) occupies nothing here. NULL-node rows and
        this node's tabless rows are legacy; remote rows remain the owning node's responsibility."""
        row = self.db.one(
            """SELECT count(*)::int AS n FROM (
                 SELECT job_id FROM browser_sessions WHERE NOT close_requested AND node = ANY(%s::text[])
                   AND (node<>%s OR target_id IS NOT NULL)
                 UNION SELECT job_id FROM tasks WHERE state='leased' AND visible_at>now()) s""",
            (list(live_nodes), current_node),
        )
        return int(row["n"]) if row else 0

    def count_node_browsers(self, node: str, exclude_job: str) -> int:
        """Tabs this node holds or runs, excluding `exclude_job` and legacy session rows."""
        row = self.db.one(
            """SELECT count(*)::int AS n FROM (
                 SELECT job_id FROM browser_sessions WHERE NOT close_requested AND target_id IS NOT NULL AND node=%s
                 UNION SELECT job_id FROM tasks WHERE state='leased' AND visible_at>now() AND started_at IS NOT NULL
                       AND leased_node=%s) s WHERE job_id<>%s""",
            (node, node, exclude_job),
        )
        return int(row["n"]) if row else 0

    def list_node_liveness(self, fresh_seconds: float, takeover_seconds: float) -> list[Row]:
        """Per node: alive = a non-offline job worker heartbeat is fresh; recent = the worker was last healthy inside the
        takeover window. A heartbeat the control worker writes with offline=true (missed ping) keeps the previous healthy
        time in status.okAt (publish_worker), so one missed ping starts the takeover clock instead of ending it."""
        return self.db.all(
            """SELECT node,
                 COALESCE(bool_or(NOT offline AND heartbeat_at > now() - make_interval(secs => %s)), false) AS alive,
                 COALESCE(bool_or(ok_at > now() - make_interval(secs => %s)), false) AS recent
               FROM (SELECT node, heartbeat_at, COALESCE((status->>'offline')::boolean,false) AS offline,
                       CASE WHEN COALESCE((status->>'offline')::boolean,false)
                            THEN (status->>'okAt')::timestamptz ELSE heartbeat_at END AS ok_at
                     FROM worker_processes WHERE name !~ '^worker-[0-9]+$'
                       AND node IS NOT NULL AND status->>'role'='jobs') w GROUP BY node""",
            (fresh_seconds, takeover_seconds),
        )

    def list_browser_online_nodes(self, fresh_seconds: float) -> list[str]:
        """Nodes whose Neo answered the control worker's last heartbeat probe (role=browser, fresh, not offline)."""
        rows = self.db.all(
            """SELECT DISTINCT node FROM worker_processes
               WHERE name !~ '^worker-[0-9]+$' AND node IS NOT NULL AND status->>'role'='browser'
                 AND NOT COALESCE((status->>'offline')::boolean,false)
                 AND heartbeat_at > now() - make_interval(secs => %s)""",
            (fresh_seconds,),
        )
        return [str(r["node"]) for r in rows]

    def find_runtime_settings_for_runtime_settings(self) -> Row | None:
        return self.db.one("SELECT value FROM runtime_settings WHERE singleton")

    def insert_runtime_settings_for_save_runtime_settings(self, value: Any) -> int:
        return self.db.run(
            """INSERT INTO runtime_settings(singleton,value) VALUES(true,%s)
               ON CONFLICT (singleton) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""",
            (value,),
        )

    def update_automation_for_set_automation(self, enabled: bool) -> int:
        return self.db.run(
            "UPDATE automation SET enabled=%s, updated_at=now() WHERE singleton AND enabled IS DISTINCT FROM %s",
            (enabled, enabled),
        )

    def find_automation_enabled(self) -> bool:
        """Unlocked read of the persisted switch (claimers that must serialize with Stop use the FOR UPDATE variant)."""
        # The live subprocess monitor must not inherit the job's IO lock connection or block indefinitely.
        with self.db.independent_conn() as connection:
            connection.execute("SET statement_timeout = '5s'")
            row = connection.execute("SELECT enabled FROM automation WHERE singleton").fetchone()
        return bool(row and row["enabled"])

    def find_automation_for_configure_runtime(self) -> Row | None:
        return self.db.one("SELECT enabled FROM automation WHERE singleton FOR UPDATE")

    def find_tasks_for_configure_runtime(self) -> Row | None:
        return self.db.one("SELECT 1 FROM tasks WHERE state='leased' AND visible_at>now() LIMIT 1")

    def insert_worker_processes_for_publish_worker(self, name: str, value: Any, node: str | None = None) -> int:
        return self.db.run(
            """INSERT INTO worker_processes(name,status,node) VALUES(%s,%s,%s) ON CONFLICT(name)
               DO UPDATE SET node=EXCLUDED.node, heartbeat_at=now(),
                 status=CASE WHEN COALESCE((EXCLUDED.status->>'offline')::boolean,false)
                             THEN EXCLUDED.status || jsonb_build_object('okAt',
                                    CASE WHEN COALESCE((worker_processes.status->>'offline')::boolean,false)
                                         THEN worker_processes.status->>'okAt'
                                         ELSE to_char(worker_processes.heartbeat_at AT TIME ZONE 'UTC',
                                                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END)
                             ELSE EXCLUDED.status - 'okAt' END""",
            (name, value, node),
        )

    def count_requeues_since_progress(self, job_id: str, reason: str) -> int:
        """`run_requeued` events with this reason since the job last made progress (a transition, resume, saved answer,
        chosen CV, agent result or owner guidance): the length of the current identical-failure streak."""
        row = self.db.one(
            """SELECT count(*)::int AS n FROM job_events e WHERE e.job_id=%s AND e.kind='run_requeued'
                 AND e.payload->>'reason'=%s
                 AND e.id > COALESCE((SELECT max(p.id) FROM job_events p WHERE p.job_id=%s AND p.kind IN
                     ('transition','resume','answers_saved','cv_track_selected','direct_result','agent_guidance',
                      'questions_try_anyway')), 0)""",
            (job_id, reason, job_id),
        )
        return int(row["n"]) if row else 0

    def find_last_requeue_reason(self, job_id: str) -> str | None:
        row = self.db.one(
            "SELECT payload->>'reason' AS reason FROM job_events WHERE job_id=%s AND kind='run_requeued' ORDER BY id DESC LIMIT 1",
            (job_id,),
        )
        return str(row["reason"]) if row and row["reason"] else None

    def block_exhausted_tasks(self) -> list[Row]:
        return self.db.all("""UPDATE tasks SET state='blocked', leased_by=NULL WHERE attempts >= 5
                   AND state IN ('queued','leased') AND visible_at<=now() RETURNING job_id""")

    def claim_next_task(
        self,
        worker: str,
        lease_seconds: int,
        capture_only: Any,
        *,
        current_node: str,
        node: str | None = None,
        allow_new_browser: bool = True,
        skip_nodes: Sequence[str] = (),
        session_nodes: Sequence[str] = (),
        charge_attempt: bool = True,
    ) -> Row | None:
        """Lease the next admissible task. `charge_attempt` False leaves `attempts` alone (a dispatched lease is charged
        by start_run, once an agent actually begins). `allow_new_browser` False admits only jobs whose tab-backed session the
        run will reuse on one of `session_nodes` (where the run is routed; start_run applies the same predicate). A job at
        awaiting_approval is never claimed: the owner submits it (ADR 0026) - except for the one open emailed-code
        verification request (`job_forms.verify_requested_at`, ADR 0026 amendment). `skip_nodes` excludes jobs whose parked
        browser lives on a node that is offline but inside the takeover window."""
        return self.db.one(
            """UPDATE tasks SET state='leased', leased_by=%s, heartbeat_at=now(), leased_at=now(), started_at=NULL,
                     leased_node=%s, visible_at=now() + make_interval(secs => %s), attempts=attempts+%s
                   WHERE id = (SELECT t.id FROM tasks t JOIN jobs j ON j.id=t.job_id
                       WHERE t.state IN ('queued','leased') AND t.visible_at<=now()
                         AND j.waiting_on='agent' AND t.attempts<5 AND j.archived_at IS NULL
                         AND (j.status <> 'awaiting_approval' OR EXISTS(
                                SELECT 1 FROM job_forms vf WHERE vf.job_id=j.id
                                  AND vf.verify_requested_at IS NOT NULL AND vf.verify_done_at IS NULL))
                         AND NOT EXISTS(SELECT 1 FROM job_preflights p WHERE p.job_id=j.id
                                        AND p.state IN ('pending','running') AND p.deadline>now())
                         AND (NOT %s OR j.source <> 'platform')
                         AND (%s
                              OR EXISTS(SELECT 1 FROM browser_sessions s WHERE s.job_id=j.id AND NOT s.close_requested
                                        AND s.node = ANY(%s::text[]) AND (s.node<>%s OR s.target_id IS NOT NULL)))
                         AND NOT EXISTS(SELECT 1 FROM browser_sessions s WHERE s.job_id=j.id AND NOT s.close_requested
                                        AND s.node = ANY(%s::text[]) AND (s.node<>%s OR s.target_id IS NOT NULL))
                       ORDER BY t.priority DESC, t.id FOR UPDATE OF t SKIP LOCKED LIMIT 1)
                   RETURNING job_id,
                     (SELECT s.node FROM browser_sessions s WHERE s.job_id=tasks.job_id AND NOT s.close_requested
                        AND s.node IS NOT NULL AND (s.node<>%s OR s.target_id IS NOT NULL)) AS session_node,
                     EXISTS(SELECT 1 FROM browser_sessions s WHERE s.job_id=tasks.job_id AND NOT s.close_requested
                            AND s.node = ANY(%s::text[]) AND (s.node<>%s OR s.target_id IS NOT NULL)) AS has_session""",
            (
                worker,
                node,
                lease_seconds,
                int(charge_attempt),
                capture_only,
                allow_new_browser,
                list(session_nodes),
                current_node,
                list(skip_nodes),
                current_node,
                current_node,
                list(session_nodes),
                current_node,
            ),
        )

    def reserve_domain_hit(self, domain: str, gap: float) -> Row | None:
        with self.db.independent_conn() as connection:
            row = connection.execute(
                """INSERT INTO domain_pacing(domain,last_hit) VALUES(%s,clock_timestamp())
                       ON CONFLICT(domain) DO UPDATE
                         SET last_hit=greatest(domain_pacing.last_hit+make_interval(secs => %s),clock_timestamp())
                       RETURNING greatest(0,extract(epoch FROM last_hit-clock_timestamp()))::float AS delay""",
                (domain, gap),
            ).fetchone()
        return row

"""PostgreSQL operations for browser. Transactions are owned by application services."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..domain.state_machine import PRE_SUBMIT_ORDER
from .database import Database, Row

_PRE_SUBMIT = ", ".join(f"'{status}'" for status in PRE_SUBMIT_ORDER)

# A job that is waiting on the owner before submission keeps its tab, whatever it is waiting for (an answer, a CV
# choice, a review, an agent note): the owner will want to look at the page the agent worked on, and a filled page
# cannot be rebuilt cheaply. The same holds for ten minutes after a successful account step (ADR 0031) or a re-asked
# answer that the server settled itself: the run is queued again, and a Workday-style site keeps its sign-up, verification and sign-in state in that very page, so a new
# blank tab would start the account over. Archive, done, a status past the pre-submit order, a submit click, or the
# owner resuming the job release it. Python (`_defer_close`) and the sweep SQL below share this one predicate.
_NO_SUBMIT = "j.archived_at IS NULL AND NOT EXISTS (SELECT 1 FROM job_events e WHERE e.job_id=j.id AND e.kind='submit_clicked')"
PARKED_FOR_OWNER = f"""((j.waiting_on='you' AND j.status IN ({_PRE_SUBMIT}) AND {_NO_SUBMIT})
    OR (j.status IN ({_PRE_SUBMIT}) AND {_NO_SUBMIT} AND EXISTS (SELECT 1 FROM job_events w WHERE w.job_id=j.id
        AND ((w.kind='account_wall' AND w.payload->>'status' IN ('created','verify_email','signed_in'))
             OR (w.kind='answers_reasked' AND w.payload->>'retry'='true'))
        AND w.ts > now() - interval '10 minutes')))"""


class BrowserRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def find_automation_for_require_browser_worker(self) -> Row | None:
        return self.db.one("SELECT enabled FROM automation WHERE singleton")

    def find_automation_for_review_recipe(self) -> Row | None:
        return self.db.one("SELECT enabled FROM automation WHERE singleton FOR UPDATE")

    def find_recipe_suggestions_for_review_recipe(self, suggestion_id: int) -> Row | None:
        return self.db.one("SELECT * FROM recipe_suggestions WHERE id=%s FOR UPDATE", (suggestion_id,))

    def update_recipe_suggestions_for_review_recipe(self, value_1: Any, suggestion_id: int) -> int:
        return self.db.run("UPDATE recipe_suggestions SET status=%s, reviewed_at=now() WHERE id=%s", (value_1, suggestion_id))

    def update_browser_sessions_for_defer_close(self, job_id: str) -> int:
        return self.db.run("UPDATE browser_sessions SET close_requested=true WHERE job_id=%s", (job_id,))

    def insert_browser_sessions_for_browser_opened(
        self, job_id: str, node: str, target_id: str | None = None, context_id: str | None = None
    ) -> int:
        """Record (or refresh) the session. A NULL target/context never erases the recorded tab."""
        return self.db.run(
            """INSERT INTO browser_sessions(job_id,node,target_id,context_id) VALUES(%s,%s,%s,%s) ON CONFLICT (job_id)
               DO UPDATE SET opened_at=now(), close_requested=false, node=EXCLUDED.node,
                 target_id=COALESCE(EXCLUDED.target_id, browser_sessions.target_id),
                 context_id=COALESCE(EXCLUDED.context_id, browser_sessions.context_id)""",
            (job_id, node, target_id, context_id),
        )

    def find_session_tab(self, job_id: str, node: str) -> Row | None:
        """Local or legacy row, including lifecycle flags, for reconciliation before use."""
        return self.db.one(
            "SELECT node, target_id, context_id, close_requested FROM browser_sessions WHERE job_id=%s AND (node IS NULL OR node=%s)",
            (job_id, node),
        )

    def list_open_tab_job_ids(self, node: str, job_ids: list[str]) -> set[str]:
        """Which of `job_ids` have a recorded live tab on this node (not already being closed)."""
        if not job_ids:
            return set()
        rows = self.db.all(
            """SELECT job_id FROM browser_sessions WHERE job_id = ANY(%s) AND target_id IS NOT NULL
               AND NOT close_requested AND (node IS NULL OR node=%s)""",
            (job_ids, node),
        )
        return {str(row["job_id"]) for row in rows}

    def list_node_session_tabs(self, node: str) -> list[Row]:
        """Local and NULL-node legacy rows, for tab-mode reconciliation."""
        return self.db.all(
            "SELECT job_id, node, target_id, opened_at FROM browser_sessions WHERE node IS NULL OR node=%s", (node,)
        )

    def list_browser_sessions_for_sweep_browsers(self, node: str) -> list[Row]:
        # A process only closes browsers it owns: sessions of other nodes are theirs to sweep; NULL = legacy, any node.
        return self.db.all(
            """SELECT b.job_id FROM browser_sessions b JOIN jobs j ON j.id=b.job_id
               LEFT JOIN tasks t ON t.job_id=b.job_id
               WHERE (b.node IS NULL OR b.node=%s)
                 AND (b.close_requested OR j.archived_at IS NOT NULL OR (
                   j.status<>'awaiting_approval' AND NOT """
            + PARKED_FOR_OWNER
            + """ AND (
                   j.waiting_on IN ('you','site')
                   OR t.state IN ('done','blocked') OR (t.state='leased' AND t.visible_at<=now()))))
               ORDER BY b.opened_at LIMIT 100""",
            (node,),
        )

    def find_parked_for_owner(self, job_id: str) -> bool:
        return self.db.one("SELECT 1 AS parked FROM jobs j WHERE j.id=%s AND " + PARKED_FOR_OWNER, (job_id,)) is not None

    def find_jobs_for_require_browser_worker(self, job_id: str) -> Row | None:
        return self.db.one("SELECT source FROM jobs WHERE id=%s", (job_id,))

    def find_tasks_for_review_recipe(self) -> Row | None:
        return self.db.one("SELECT 1 FROM tasks WHERE state='leased' AND visible_at>now() LIMIT 1")

    def find_browser_sessions_for_close_browser_locked(self, job_id: str, node: str) -> Row | None:
        return self.db.one(
            """SELECT b.job_id, b.node, b.target_id, b.context_id FROM browser_sessions b JOIN jobs j ON j.id=b.job_id
                       LEFT JOIN tasks t ON t.job_id=b.job_id
                       WHERE b.job_id=%s AND (b.node IS NULL OR b.node=%s)
                         AND (b.close_requested OR j.archived_at IS NOT NULL OR (
                           j.status<>'awaiting_approval' AND NOT """
            + PARKED_FOR_OWNER
            + """ AND (
                           j.waiting_on IN ('you','site') OR t.state IN ('done','blocked')
                           OR (t.state='leased' AND t.visible_at<=now()))))""",
            (job_id, node),
        )

    def delete_browser_sessions_for_close_browser_locked(self, job_id: str, node: str) -> int:
        return self.db.run("DELETE FROM browser_sessions WHERE job_id=%s AND (node IS NULL OR node=%s)", (job_id, node))

    def delete_legacy_session(self, job_id: str, node: str) -> int:
        """Only rows incapable of representing a local tab; never delete a replacement tab."""
        return self.db.run(
            "DELETE FROM browser_sessions WHERE job_id=%s AND (node IS NULL OR (node=%s AND target_id IS NULL))",
            (job_id, node),
        )

    def delete_tabless_session(self, job_id: str, node: str, target_id: str) -> int:
        """Drop a row whose recorded tab no longer exists in Chrome (the same target only: a reopened job has a new one)."""
        return self.db.run("DELETE FROM browser_sessions WHERE job_id=%s AND node=%s AND target_id=%s", (job_id, node, target_id))

    @contextmanager
    def cleanup_lock(self, job_id: str) -> Iterator[bool]:
        with self.db.conn() as connection:
            held = connection.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,3)) AS ok", (job_id,)).fetchone()
            acquired = bool(held and held["ok"])
            try:
                yield acquired
            finally:
                if acquired:
                    connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s,3))", (job_id,))

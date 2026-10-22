"""PostgreSQL operations for the parked-tab watcher (ADR 0026, amended). Transactions are owned by services."""

from __future__ import annotations

from typing import Any

from .database import Database, J, Row


class ParkWatchRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_watchable(self, node: str, limit: int) -> list[Row]:
        """Filled applications parked for the owner whose tab is on this node and has a baseline. A job already flagged
        `tab_closed` has nothing left to watch."""
        return self.db.all(
            """SELECT j.id, j.board_id, b.target_id, f.park_baseline, f.park_signal, f.inventory,
                      f.verify_requested_at
               FROM jobs j JOIN job_forms f ON f.job_id=j.id JOIN browser_sessions b ON b.job_id=j.id
               WHERE j.status='awaiting_approval' AND j.archived_at IS NULL AND f.park_baseline IS NOT NULL
                 AND (f.park_signal IS NULL OR f.park_signal<>'tab_closed')
                 AND b.target_id IS NOT NULL AND NOT b.close_requested AND (b.node IS NULL OR b.node=%s)
                 AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id=j.id)
               ORDER BY f.updated_at, j.id LIMIT %s""",
            (node, limit),
        )

    def list_unbaselined(self, node: str, limit: int) -> list[Row]:
        """Parked applications whose baseline could not be stored when they parked (Neo was unreachable for a moment):
        without one the watcher never sees them, so it takes the baseline on a later tick."""
        return self.db.all(
            """SELECT j.id, b.target_id
               FROM jobs j JOIN job_forms f ON f.job_id=j.id JOIN browser_sessions b ON b.job_id=j.id
               WHERE j.status='awaiting_approval' AND j.archived_at IS NULL AND f.park_baseline IS NULL
                 AND f.park_signal IS NULL AND b.target_id IS NOT NULL AND NOT b.close_requested
                 AND (b.node IS NULL OR b.node=%s)
                 AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id=j.id)
               ORDER BY f.updated_at, j.id LIMIT %s""",
            (node, limit),
        )

    def find_form_for_baseline(self, job_id: str) -> Row | None:
        return self.db.one("SELECT inventory FROM job_forms WHERE job_id=%s", (job_id,))

    def update_baseline(self, job_id: str, baseline: dict[str, Any]) -> int:
        """Store the parked-tab baseline and clear any earlier signal. Deliberately leaves `updated_at` and the draft
        alone: this is bookkeeping about the tab, not a change to the application."""
        return self.db.run(
            "UPDATE job_forms SET park_baseline=%s, park_signal=NULL WHERE job_id=%s",
            (J(baseline), job_id),
        )

    def update_signal(self, job_id: str, signal: str | None, expected: str | None) -> int:
        """Set the signal only if it still has the value the caller saw (two watcher runs never double-record)."""
        return self.db.run(
            "UPDATE job_forms SET park_signal=%s WHERE job_id=%s AND park_signal IS NOT DISTINCT FROM %s",
            (signal, job_id, expected),
        )

    def clear_signal(self, job_id: str) -> int:
        return self.db.run("UPDATE job_forms SET park_signal=NULL WHERE job_id=%s", (job_id,))

    def find_signal(self, job_id: str) -> Row | None:
        return self.db.one("SELECT park_signal FROM job_forms WHERE job_id=%s", (job_id,))

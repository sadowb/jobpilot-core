"""PostgreSQL operations for approvals. Transactions are owned by application services."""

from __future__ import annotations

from typing import Any, Literal

from .database import Database, Row


class ApprovalsRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def lock_job(self, job_id: str) -> Row | None:
        return self.db.one("SELECT id FROM jobs WHERE id=%s FOR UPDATE", (job_id,))

    def insert_applications_for_record_submitted(
        self, job_id: str, cv_version_id: Any, kind: Literal["live-verified", "user-confirmed"], value_4: Any
    ) -> int:
        return self.db.run(
            """INSERT INTO applications(job_id, applied_on, applied_at, cv_version_id, confirmation, confirmation_text)
               VALUES(%s, current_date, now(), %s, %s::confirmation_kind, %s) ON CONFLICT (job_id) DO NOTHING""",
            (job_id, cv_version_id, kind, value_4),
        )

    def update_approvals_for_record_submitted(self, job_id: str) -> int:
        return self.db.run(
            "UPDATE approvals SET consumed_at=now() WHERE job_id=%s AND kind='submit' AND consumed_at IS NULL", (job_id,)
        )

    def update_jobs_for_record_submitted(self, job_id: str) -> int:
        return self.db.run("UPDATE jobs SET waiting_on='site', blocked_reason=NULL WHERE id=%s", (job_id,))

    def find_job_events_for_verification_gates(self, job_id: str) -> Row | None:
        return self.db.one("SELECT payload FROM job_events WHERE job_id=%s AND kind='check' ORDER BY id DESC LIMIT 1", (job_id,))

    def find_job_events_for_submit_clicked(self, job_id: str) -> Row | None:
        return self.db.one("SELECT 1 FROM job_events WHERE job_id=%s AND kind='submit_clicked' LIMIT 1", (job_id,))

    def find_applications_for_record_submitted(self, job_id: str) -> Row | None:
        return self.db.one("SELECT 1 FROM applications WHERE job_id=%s", (job_id,))

    def delete_auto_submit_fence(self, job_id: str) -> int:
        """Remove only the fence an auto run wrote before it started (payload auto=true). The one place a job_events
        row is deleted: the fence is a lock, not history, and `submit_fence_released` records the release."""
        return self.db.run(
            "DELETE FROM job_events WHERE job_id=%s AND kind='submit_clicked' AND payload->>'auto'='true'", (job_id,)
        )

    def insert_approvals_for_approve_submit(self, jid: Any, draft_hash: Any, scope: str) -> int:
        return self.db.run(
            "INSERT INTO approvals(kind, job_id, draft_hash, scope) VALUES('submit',%s,%s,%s)", (jid, draft_hash, scope)
        )

    def find_unconsumed_submit_approval(self, job_id: str, draft_hash: str) -> Row | None:
        return self.db.one(
            """SELECT id, approved_at FROM approvals
               WHERE job_id=%s AND kind='submit' AND draft_hash=%s AND consumed_at IS NULL
               ORDER BY id DESC LIMIT 1""",
            (job_id, draft_hash),
        )

    def insert_reported_application(self, job_id: str, cv_version_id: Any, kind: str, text: str) -> None:
        self.db.run(
            """INSERT INTO applications(job_id, applied_on, applied_at, cv_version_id, confirmation, confirmation_text)
               VALUES(%s,current_date,now(),%s,%s::confirmation_kind,%s) ON CONFLICT(job_id) DO NOTHING""",
            (job_id, cv_version_id, kind, text),
        )

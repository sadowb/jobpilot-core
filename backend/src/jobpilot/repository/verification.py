"""PostgreSQL operations for the emailed-code verification run (ADR 0026, amended). Transactions are owned by services."""

from __future__ import annotations

from .database import Database, Row


class VerificationRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def lock_job(self, job_id: str) -> Row | None:
        return self.db.one("SELECT id FROM jobs WHERE id=%s FOR UPDATE", (job_id,))

    def claim_request(self, job_id: str, *, retry: bool) -> int:
        """Set the one-shot request. `retry` (the owner's explicit button) may ask again after an earlier run finished;
        the watcher never can. Returns 0 when a request is already open or already used."""
        return self.db.run(
            """UPDATE job_forms SET verify_requested_at=now(), verify_done_at=NULL
               WHERE job_id=%s AND (verify_requested_at IS NULL OR (%s AND verify_done_at IS NOT NULL))""",
            (job_id, retry),
        )

    def queue_run(self, job_id: str) -> int:
        """Same task row as every run, handed to the agent. Deliberately not `JobsService.enqueue`, which refuses
        awaiting_approval: this is the one sanctioned way to run an agent on a parked job."""
        self.db.run("UPDATE jobs SET waiting_on='agent', blocked_reason=NULL, updated_at=now() WHERE id=%s", (job_id,))
        return self.db.run(
            """INSERT INTO tasks(job_id, step, state) VALUES(%s,'run','queued')
               ON CONFLICT (job_id, step) DO UPDATE SET state='queued', visible_at=now(), leased_by=NULL, attempts=0
               WHERE tasks.state <> 'leased'""",
            (job_id,),
        )

    def find_open_request(self, job_id: str) -> Row | None:
        return self.db.one(
            "SELECT verify_requested_at FROM job_forms WHERE job_id=%s AND verify_requested_at IS NOT NULL AND verify_done_at IS NULL",
            (job_id,),
        )

    def update_done(self, job_id: str) -> int:
        return self.db.run(
            "UPDATE job_forms SET verify_done_at=now() WHERE job_id=%s AND verify_requested_at IS NOT NULL AND verify_done_at IS NULL",
            (job_id,),
        )

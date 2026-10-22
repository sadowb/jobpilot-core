"""Control-queue outbox and admission fence for ingest preparation."""

from .database import Database


class PreflightRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def enqueue(self, job_id: str) -> None:
        self.db.run("INSERT INTO job_preflights(job_id) VALUES(%s) ON CONFLICT DO NOTHING", (job_id,))

    def publishable(self) -> list[str]:
        rows = self.db.all(
            """UPDATE job_preflights SET published_at=now() WHERE job_id IN (
                 SELECT job_id FROM job_preflights WHERE state='pending' AND deadline>now()
                   AND (published_at IS NULL OR published_at<now()-interval '30 seconds')
                 ORDER BY deadline LIMIT 20 FOR UPDATE SKIP LOCKED) RETURNING job_id"""
        )
        return [str(r["job_id"]) for r in rows]

    def start(self, job_id: str) -> bool:
        return bool(
            self.db.run(
                """UPDATE job_preflights p SET state='running', deadline=now()+interval '4 minutes'
               FROM jobs j WHERE p.job_id=%s AND j.id=p.job_id AND p.state='pending' AND p.deadline>now()
                 AND j.archived_at IS NULL AND j.waiting_on='agent'
                 AND j.status IN ('discovered','screened','preparing')
                 AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.job_id=j.id AND t.state='leased')""",
                (job_id,),
            )
        )

    def active(self, job_id: str) -> bool:
        return (
            self.db.one(
                """SELECT 1 FROM job_preflights p JOIN jobs j ON j.id=p.job_id
               WHERE p.job_id=%s AND p.state='running' AND p.deadline>now()
                 AND j.archived_at IS NULL AND j.waiting_on='agent'
                 AND j.status IN ('discovered','screened','preparing')
                 AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.job_id=j.id AND t.state='leased')""",
                (job_id,),
            )
            is not None
        )

    def finish(self, job_id: str, success: bool) -> None:
        self.db.run(
            "UPDATE job_preflights SET state=%s WHERE job_id=%s AND state='running'",
            ("done" if success else "fallback", job_id),
        )

    def done(self, job_id: str) -> bool:
        return self.db.one("SELECT 1 FROM job_preflights WHERE job_id=%s AND state='done'", (job_id,)) is not None

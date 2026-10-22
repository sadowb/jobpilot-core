"""Server-owned conversation identity; job run locks serialize writers."""

from .database import Database, J, Row


class AgentSessionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, job_id: str) -> Row | None:
        return self.db.one(
            "SELECT agent_session_id::text AS id, agent_session_context AS context FROM jobs WHERE id=%s", (job_id,)
        )

    def create(self, job_id: str, session_id: str) -> Row:
        row = self.db.one(
            """UPDATE jobs SET agent_session_id=COALESCE(agent_session_id,%s::uuid)
               WHERE id=%s RETURNING agent_session_id::text AS id, agent_session_context AS context""",
            (session_id, job_id),
        )
        if not row:
            raise LookupError("job no longer exists")
        return row

    def reset(self, job_id: str, session_id: str) -> None:
        self.db.run(
            "UPDATE jobs SET agent_session_id=%s::uuid, agent_session_context='{}'::jsonb WHERE id=%s", (session_id, job_id)
        )

    def remember(self, job_id: str, session_id: str, context: dict[str, str]) -> None:
        self.db.run(
            "UPDATE jobs SET agent_session_context=%s WHERE id=%s AND agent_session_id=%s::uuid",
            (J(context), job_id, session_id),
        )

    def active_jobs(self) -> set[str]:
        return {
            str(row["id"])
            for row in self.db.all(
                """SELECT id FROM jobs WHERE archived_at IS NULL
               AND status IN ('discovered','screened','preparing','ready','awaiting_approval')"""
            )
        }

    def clear(self, job_id: str) -> None:
        self.db.run("UPDATE jobs SET agent_session_id=NULL, agent_session_context='{}'::jsonb WHERE id=%s", (job_id,))

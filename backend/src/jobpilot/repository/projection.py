"""PostgreSQL operations for projection. Transactions are owned by application services."""

from __future__ import annotations

from typing import Any

from .database import Database, Row


class ProjectionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def insert_projection_dirty_for_project(self, job_id: str) -> int:
        return self.db.run(
            """INSERT INTO projection_dirty(job_id) VALUES(%s) ON CONFLICT (job_id)
               DO UPDATE SET generation=projection_dirty.generation+1, requested_at=now()""",
            (job_id,),
        )

    def list_projection_dirty_for_sweep_projections(self, limit: int) -> list[Row]:
        return self.db.all("SELECT job_id FROM projection_dirty ORDER BY requested_at LIMIT %s", (limit,))

    def find_jobs_for_write_projection(self, job_id: str) -> Row | None:
        return self.db.one(
            """SELECT j.*, c.name AS company_name, a.applied_on::text AS applied_on, a.applied_at, a.confirmation, v.sha256 AS cv_sha
               FROM jobs j LEFT JOIN companies c ON c.id=j.company_id LEFT JOIN applications a ON a.job_id=j.id
               LEFT JOIN cv_versions v ON v.id=j.cv_version_id WHERE j.id=%s""",
            (job_id,),
        )

    def find_projection_dirty_for_reproject(self, job_id: str) -> Row | None:
        return self.db.one("SELECT generation FROM projection_dirty WHERE job_id=%s", (job_id,))

    def delete_projection_dirty_for_reproject(self, job_id: str, generation: Any) -> int:
        return self.db.run("DELETE FROM projection_dirty WHERE job_id=%s AND generation=%s", (job_id, generation))

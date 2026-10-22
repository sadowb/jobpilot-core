"""Projection operations behind the shared Services facade."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..projection.vault import JobNote, ensure_vault, write_job_note
from ..repository.projection import ProjectionRepository
from ._support import log

if TYPE_CHECKING:
    from .application import Services


class ProjectionService:
    def __init__(self, application: Services) -> None:
        self.application = application
        self.repository = ProjectionRepository(application.db)

    def project(self, job_id: str) -> None:
        self.repository.insert_projection_dirty_for_project(job_id)

    def reproject(self, job_id: str) -> bool:
        try:
            # A session advisory lock serializes projectors, without an open transaction during FS IO.
            with self.application.db.session_lock(job_id, 1):
                dirty = self.repository.find_projection_dirty_for_reproject(job_id)
                self.application._write_projection(job_id)
                if dirty:
                    self.repository.delete_projection_dirty_for_reproject(job_id, dirty["generation"])
            return True
        except Exception:  # noqa: BLE001 - projection is retried independently of committed writes
            log.exception("vault projection failed job=%s", job_id)
            return False

    def sweep_projections(self, limit: int = 100) -> int:
        rows = self.repository.list_projection_dirty_for_sweep_projections(limit)
        projected = sum(self.application.reproject(row["job_id"]) for row in rows)
        self.application.sweep_cv_commits(limit)
        return projected

    def _write_projection(self, job_id: str) -> None:
        ensure_vault(self.application.cfg.vault)
        j = self.repository.find_jobs_for_write_projection(job_id)
        if not j:
            return
        write_job_note(
            self.application.cfg.vault,
            JobNote(
                id=j["id"],
                company=j["company_name"] or "Unknown",
                role=j["role"],
                url=j["url"],
                board=j["board_id"],
                status=j["status"],
                waiting_on=j["waiting_on"],
                blocked_reason=j["blocked_reason"],
                cv_track=j["cv_track_id"],
                cv_mode=j["cv_mode"],
                cv_sha=j["cv_sha"],
                match_score=j["match_score"],
                location=j["location"],
                work_mode=j["work_mode"],
                pay=j["pay"],
                deadline=str(j["deadline"])[:10] if j["deadline"] else None,
                applied_on=j["applied_on"],
                applied_at=j["applied_at"].astimezone(UTC).isoformat() if j["applied_at"] else None,
                confirmation=j["confirmation"],
                updated_at=(j["updated_at"] or datetime.now(UTC)).astimezone(UTC).isoformat(),
                posting=j["posting_text"],
            ),
        )

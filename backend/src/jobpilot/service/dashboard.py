"""Read operations for controllers. Database access belongs to DashboardRepository."""

from __future__ import annotations

from typing import Any

from ..domain.answers import JobContext, Known
from ..domain.reask import classify_reported
from ..repository.dashboard import DashboardRepository
from ..repository.database import Database, Row


class DashboardService:
    def __init__(self, db: Database) -> None:
        self.repository = DashboardRepository(db)

    def job_events(self, job_id: str) -> list[Row]:
        return self.repository.job_events(job_id)

    def answer_bank(self) -> Known:
        """Saved answers only (no profile): what the owner has already told JobPilot, keyed by normalised question."""
        answers: dict[str, dict[str, str]] = {}
        for row in self.repository.answer_bank():
            answers.setdefault(row["question_key"], {})[row["scope"]] = row["answer"]
        return Known(answers=answers)

    def unsettled(self, job_id: str, company_slug: str, views: list[dict[str, Any]], bank: Known) -> list[dict[str, Any]]:
        """Question views that still need the owner. `bank` carries saved answers only (no profile), so a question is hidden
        only when an answer the owner gave (job, company or global scope) or a tab-completion covers its label."""
        found = classify_reported(
            [{"label": v["label"], "kind": v["type"], "options": v["options"]} for v in views],
            bank,
            job_id,
            company_slug,
            JobContext(),
            self.repository.owner_completed_labels(job_id),
        )
        settled = {index for index, _ in found.settled}
        return [v for index, v in enumerate(views) if index not in settled]

    def blocked_jobs(self, limit: int, offset: int) -> list[Row]:
        return self.repository.blocked_jobs(limit, offset)

    def blocked_jobs_ready(self, limit: int, offset: int) -> list[Row]:
        return self.repository.blocked_jobs_ready(limit, offset)

    def track_exists_for_ingest(self, cv_track: str) -> Row | None:
        return self.repository.track_exists_for_ingest(cv_track)

    def tracks(self, marker_hash: str) -> list[Row]:
        return self.repository.tracks(marker_hash)

    def track_exists(self, track: str) -> Row | None:
        return self.repository.track_exists(track)

    def track_metadata(self, track_id: str) -> Row | None:
        return self.repository.track_metadata(track_id)

    def track_cv(self, track_id: str) -> Row | None:
        return self.repository.track_cv(track_id)

    def job_cv(self, job_id: str) -> Row | None:
        return self.repository.job_cv(job_id)

    def profile(self) -> list[Row]:
        return self.repository.profile()

    def facts(self) -> list[Row]:
        return self.repository.facts()

    def answers(self) -> list[Row]:
        return self.repository.answers()

    def boards(self) -> list[Row]:
        return self.repository.boards()

    def recipe_suggestions(self, limit: int, offset: int) -> list[Row]:
        return self.repository.recipe_suggestions(limit, offset)

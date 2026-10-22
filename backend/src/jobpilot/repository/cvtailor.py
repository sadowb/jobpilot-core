"""PostgreSQL operations for cvtailor. Transactions are owned by application services."""

from __future__ import annotations

from typing import Any

from .database import Database, Row


class CvtailorRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_facts_for_tailor_cv(self) -> list[Row]:
        return self.db.all("SELECT id, claim FROM facts WHERE confirmed_at IS NOT NULL ORDER BY id")

    def find_cv_tracks_for_tailor_cv(self, cv_track_id: Any) -> Row | None:
        return self.db.one(
            "SELECT v.id, v.sha256 FROM cv_tracks t JOIN cv_versions v ON v.id=t.current_version_id WHERE t.id=%s", (cv_track_id,)
        )

    def find_cv_versions_for_tailor_cv(self, value_1: Any) -> Row | None:
        return self.db.one("SELECT id, sha256 FROM cv_versions WHERE summary=%s", (value_1,))

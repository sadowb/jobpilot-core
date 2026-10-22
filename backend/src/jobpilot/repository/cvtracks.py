"""PostgreSQL operations for cvtracks. Transactions are owned by application services."""

from __future__ import annotations

from typing import Any

from .database import Database, Row


class CvtracksRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def update_cv_tracks_for_save_version(self, track: str) -> int:
        return self.db.run("UPDATE cv_tracks SET archived_at=NULL WHERE id=%s", (track,))

    def find_cv_tracks_for_update_meta(self, track: str) -> Row | None:
        return self.db.one(
            "SELECT title, keywords, default_mode, market FROM cv_tracks WHERE id=%s AND archived_at IS NULL", (track,)
        )

    def update_cv_tracks_for_update_meta(self, new_title: Any, value: Any, new_mode: Any, value_4: Any, track: str) -> int:
        return self.db.run(
            "UPDATE cv_tracks SET title=%s, keywords=%s, default_mode=%s, market=%s WHERE id=%s",
            (new_title, value, new_mode, value_4, track),
        )

    def find_cv_tracks_for_remove(self, track: str) -> Row | None:
        return self.db.one("SELECT id FROM cv_tracks WHERE id=%s AND archived_at IS NULL FOR UPDATE", (track,))

    def list_jobs_for_remove(self, track: str) -> list[Row]:
        return self.db.all(
            """SELECT id FROM jobs WHERE cv_track_id=%s AND archived_at IS NULL
           AND status IN ('discovered','screened','preparing','ready','awaiting_approval')
           AND NOT EXISTS (SELECT 1 FROM job_events e WHERE e.job_id=jobs.id AND e.kind='submit_clicked')""",
            (track,),
        )

    def update_cv_tracks_for_remove(self, track: str) -> int:
        return self.db.run("UPDATE cv_tracks SET archived_at=now() WHERE id=%s", (track,))

    def find_replacement_track(self, reassign_to: str | None) -> Row | None:
        return self.db.one(
            "SELECT id FROM cv_tracks WHERE id=%s AND archived_at IS NULL AND current_version_id IS NOT NULL", (reassign_to,)
        )

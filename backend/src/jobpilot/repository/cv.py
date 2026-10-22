"""PostgreSQL operations for cv. Transactions are owned by application services."""

from __future__ import annotations

from typing import Any

from .database import Database, Row


class CvRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def lock_job(self, job_id: str) -> Row | None:
        return self.db.one("SELECT id FROM jobs WHERE id=%s FOR UPDATE", (job_id,))

    def find_cv_tracks_for_cv_use_base(self, cv_track_id: Any) -> Row | None:
        return self.db.one(
            "SELECT v.sha256, v.id AS vid FROM cv_tracks t JOIN cv_versions v ON v.id = t.current_version_id WHERE t.id=%s",
            (cv_track_id,),
        )

    def update_jobs_for_cv_use_base(self, vid: Any, job_id: str) -> int:
        return self.db.run("UPDATE jobs SET cv_version_id=%s, cv_mode='base', updated_at=now() WHERE id=%s", (vid, job_id))

    def find_cv_tracks_for_read_track_tex(self, track: str) -> Row | None:
        return self.db.one(
            "SELECT v.sha256 FROM cv_tracks t JOIN cv_versions v ON v.id=t.current_version_id WHERE t.id=%s", (track,)
        )

    def insert_cv_commit_jobs_for_queue_cv_job(self, job_id: str, include_pdf: bool) -> int:
        return self.db.run(
            """INSERT INTO cv_commit_jobs(job_id,include_pdf) VALUES(%s,%s) ON CONFLICT (job_id)
               DO UPDATE SET include_pdf=cv_commit_jobs.include_pdf OR EXCLUDED.include_pdf, generation=cv_commit_jobs.generation+1""",
            (job_id, include_pdf),
        )

    def insert_cv_commit_versions_for_queue_cv_version(self, version: int, include_pdf: bool) -> int:
        return self.db.run(
            """INSERT INTO cv_commit_versions(version_id,include_pdf) VALUES(%s,%s) ON CONFLICT (version_id)
               DO UPDATE SET include_pdf=cv_commit_versions.include_pdf OR EXCLUDED.include_pdf, generation=cv_commit_versions.generation+1""",
            (version, include_pdf),
        )

    def insert_cv_tracks_for_register_track(self, track: str, title: str, value: Any, mode: str, market: str) -> int:
        return self.db.run(
            """INSERT INTO cv_tracks(id,title,keywords,default_mode,market) VALUES(%s,%s,%s,%s,%s)
               ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title, keywords=EXCLUDED.keywords,
                 default_mode=EXCLUDED.default_mode, market=EXCLUDED.market""",
            (track, title, value, mode, market),
        )

    def insert_cv_versions_for_register_track(self, track: str, obj_sha: str, tex_sha: str, value_4: Any) -> Row | None:
        return self.db.one(
            """INSERT INTO cv_versions(track_id,sha256,tex_sha256,summary) VALUES(%s,%s,%s,%s)
               ON CONFLICT (sha256) DO UPDATE SET sha256=EXCLUDED.sha256 RETURNING id""",
            (track, obj_sha, tex_sha, value_4),
        )

    def update_cv_tracks_for_register_track(self, version: Any, track: str) -> int:
        return self.db.run("UPDATE cv_tracks SET current_version_id=%s WHERE id=%s", (version, track))

    def insert_cv_versions_for_register_tailored(
        self, cv_track_id: Any, obj_sha: str, tex_sha: str, value_4: Any, job_id: str
    ) -> Row | None:
        return self.db.one(
            """INSERT INTO cv_versions(track_id,sha256,tex_sha256,summary,tailored_for_job_id) VALUES(%s,%s,%s,%s,%s)
               ON CONFLICT (sha256) DO UPDATE SET sha256=EXCLUDED.sha256 RETURNING id""",
            (cv_track_id, obj_sha, tex_sha, value_4, job_id),
        )

    def update_jobs_for_use_tailored(self, version_id: int, job_id: str) -> int:
        return self.db.run(
            "UPDATE jobs SET cv_version_id=%s, cv_mode='tailor', updated_at=now() WHERE id=%s", (version_id, job_id)
        )

    def list_cv_commit_versions_for_sweep_cv_commits(self, limit: int) -> list[Row]:
        return self.db.all(
            """SELECT q.*, v.sha256, v.track_id FROM cv_commit_versions q JOIN cv_versions v ON v.id=q.version_id LIMIT %s""",
            (limit,),
        )

    def list_cv_commit_jobs_for_sweep_cv_commits(self, limit: int) -> list[Row]:
        return self.db.all("SELECT * FROM cv_commit_jobs LIMIT %s", (limit,))

    def find_cv_versions_for_cv_check(self, cv_version_id: Any) -> Row | None:
        return self.db.one("SELECT sha256 FROM cv_versions WHERE id=%s", (cv_version_id,))

    def delete_cv_commit_jobs_for_sweep_cv_commits(self, id: Any, generation: Any) -> int:
        return self.db.run("DELETE FROM cv_commit_jobs WHERE job_id=%s AND generation=%s", (id, generation))

    def list_facts_for_cv_check(self) -> list[Row]:
        return self.db.all("SELECT id FROM facts WHERE confirmed_at IS NOT NULL")

    def list_cv_tracks_for_sweep_cv_commits(self, version: Any) -> list[Row]:
        return self.db.all("SELECT id FROM cv_tracks WHERE current_version_id=%s", (version,))

    def update_cv_versions_for_sweep_cv_commits(self, commit: Any, version: Any) -> int:
        return self.db.run("UPDATE cv_versions SET git_commit=COALESCE(%s,git_commit) WHERE id=%s", (commit, version))

    def delete_cv_commit_versions_for_sweep_cv_commits(self, version: Any, generation: Any) -> int:
        return self.db.run("DELETE FROM cv_commit_versions WHERE version_id=%s AND generation=%s", (version, generation))

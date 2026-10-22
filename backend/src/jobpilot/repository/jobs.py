"""PostgreSQL operations for jobs. Transactions are owned by application services."""

from __future__ import annotations

from typing import Any

from .database import Database, Row

JOB_SELECT = "\n  SELECT j.id, c.name AS company_name, c.slug AS company_slug, j.role, j.url, j.apply_url, j.pacing_hostname, j.board_id, j.tenant, j.requisition_id,\n         j.status, j.waiting_on, j.blocked_reason, j.cv_track_id, j.cv_mode, j.cv_version_id, j.match_score, j.location,\n         j.work_mode, j.pay, j.deadline::text AS deadline, (j.posting_sha256 IS NOT NULL) AS meta_captured,\n         j.created_at, j.updated_at, j.source, j.try_questions_anyway, j.submit_mode\n  FROM jobs j LEFT JOIN companies c ON c.id = j.company_id"


class JobsRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def insert_companies_for_upsert_company(self, name: Any, name_2: str) -> Row | None:
        return self.db.one(
            "INSERT INTO companies(slug,name) VALUES(%s,%s) ON CONFLICT (slug) DO UPDATE SET name = companies.name RETURNING id",
            (name, name_2),
        )

    def find_transaction_lock_for_ingest(self, jid: Any) -> Row | None:
        return self.db.one("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (jid,))

    def find_jobs_for_ingest(self, jid: Any, h: Any, value_3: Any, value_4: Any, value_5: Any, value_6: Any) -> Row | None:
        return self.db.one(
            """SELECT id FROM jobs WHERE id=%s OR canonical_url_hash=%s
               OR (board_id=%s AND tenant=%s AND requisition_id=%s AND %s::text IS NOT NULL) LIMIT 1""",
            (jid, h, value_3, value_4, value_5, value_6),
        )

    def insert_boards_for_ingest(self, value_1: Any, value_2: Any, value_3: Any) -> int:
        return self.db.run(
            "INSERT INTO boards(id, display_name, automation) VALUES(%s,%s,%s) ON CONFLICT (id) DO NOTHING",
            (value_1, value_2, value_3),
        )

    def insert_jobs_for_ingest(
        self,
        jid: Any,
        cid: Any,
        role_name: Any,
        value_4: Any,
        h: Any,
        value_6: Any,
        value_7: Any,
        value_8: Any,
        company_name: Any,
        value_10: Any,
        cv_track: str | None,
        cv_mode: str,
        value_13: Any,
    ) -> int:
        return self.db.run(
            """INSERT INTO jobs(id, company_id, role, url, canonical_url_hash, board_id, tenant, requisition_id,
                                company_norm, title_norm, cv_track_id, cv_mode, pacing_hostname, waiting_on)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'agent')""",
            (jid, cid, role_name, value_4, h, value_6, value_7, value_8, company_name, value_10, cv_track, cv_mode, value_13),
        )

    def insert_tasks_for_enqueue(self, job_id: str) -> int:
        return self.db.run(
            """INSERT INTO tasks(job_id, step, state) VALUES(%s,'run','queued')
               ON CONFLICT (job_id, step) DO UPDATE SET state='queued', visible_at=now(), leased_by=NULL, attempts=0 WHERE tasks.state <> 'leased'""",
            (job_id,),
        )

    def find_job_view(self, job_id: str) -> Row | None:
        return self.db.one(f"{JOB_SELECT} WHERE j.id=%s", (job_id,))

    def find_jobs_for_live(self, job_id: str) -> Row | None:
        return self.db.one("SELECT archived_at, waiting_on, status FROM jobs WHERE id=%s", (job_id,))

    def find_jobs_for_posting(self, job_id: str) -> Row | None:
        return self.db.one("SELECT posting_text FROM jobs WHERE id=%s", (job_id,))

    def find_job_forms_for_form(self, job_id: str) -> Row | None:
        return self.db.one("SELECT * FROM job_forms WHERE job_id=%s", (job_id,))

    def find_approvals_for_approval_for(self, job_id: str) -> Row | None:
        return self.db.one(
            "SELECT draft_hash FROM approvals WHERE kind='submit' AND job_id=%s AND consumed_at IS NULL ORDER BY id DESC LIMIT 1",
            (job_id,),
        )

    def find_boards_for_board_policy(self, board_id: str | None) -> Row | None:
        return self.db.one("SELECT automation FROM boards WHERE id=%s", (board_id,))

    def find_order_state_job(self, job_id: str) -> Row | None:
        return self.db.one(f"""{JOB_SELECT} WHERE j.id=%s""", (job_id,))

    def find_approvals_for_order_state(self, job_id: str, job_id_2: str, board_id: Any, job_id_4: str) -> Row | None:
        return self.db.one(
            """SELECT f.*,
               (SELECT draft_hash FROM approvals WHERE kind='submit' AND job_id=%s
                AND consumed_at IS NULL ORDER BY id DESC LIMIT 1) AS approved,
               EXISTS(SELECT 1 FROM job_events WHERE job_id=%s AND kind='submit_clicked') AS clicked,
               (SELECT automation FROM boards WHERE id=%s) AS automation
               FROM (SELECT 1) seed LEFT JOIN job_forms f ON f.job_id=%s""",
            (job_id, job_id_2, board_id, job_id_4),
        )

    def lock_job(self, job_id: str) -> Row | None:
        return self.db.one("SELECT id FROM jobs WHERE id=%s FOR UPDATE", (job_id,))

    def find_tasks_for_done(self, job_id: str, worker: str) -> Row | None:
        return self.db.one(
            "SELECT id FROM tasks WHERE job_id=%s AND state='leased' AND leased_by=%s AND visible_at>now() FOR UPDATE",
            (job_id, worker),
        )

    def find_jobs_for_set_meta(self, job_id: str, co: Any, ro: Any, loc: Any) -> Row | None:
        return self.db.one(
            "SELECT id FROM jobs WHERE id <> %s AND company_norm=%s AND title_norm=%s AND COALESCE(location_norm,'')=%s LIMIT 1",
            (job_id, co, ro, loc),
        )

    def update_jobs_for_set_meta(
        self,
        cid: Any,
        ro: Any,
        location: str | None,
        work_mode: str | None,
        pay: str | None,
        deadline: str | None,
        posting_text: Any,
        sha: Any,
        co: Any,
        ro_10: Any,
        loc: Any,
        job_id: str,
    ) -> int:
        return self.db.run(
            """UPDATE jobs SET company_id=COALESCE(%s,company_id), role=%s, location=COALESCE(%s,location), work_mode=COALESCE(%s,work_mode),
                 pay=COALESCE(%s,pay), deadline=COALESCE(%s::date,deadline), posting_text=%s, posting_sha256=%s,
                 company_norm=%s, title_norm=%s, location_norm=%s, updated_at=now() WHERE id=%s""",
            (cid, ro, location, work_mode, pay, deadline, posting_text, sha, co, ro_10, loc, job_id),
        )

    def list_cv_tracks_for_suggest_track(self) -> list[Row]:
        return self.db.all(
            "SELECT id, keywords, market FROM cv_tracks WHERE archived_at IS NULL AND current_version_id IS NOT NULL"
        )

    def update_jobs_for_wait(self, who: str, value_2: Any, job_id: str) -> int:
        return self.db.run(
            """UPDATE jobs SET waiting_on=%s, blocked_reason=%s, updated_at=now() WHERE id=%s
               AND (waiting_on IS DISTINCT FROM %s::waiting_on OR blocked_reason IS DISTINCT FROM %s::text)""",
            (who, value_2, job_id, who, value_2),
        )

    def find_unsent_answers(self, job_id: str) -> bool:
        """Answers saved from the dashboard since the job's last resume (see `answers_saved`)."""
        return (
            self.db.one(
                """SELECT 1 AS unsent FROM job_events e WHERE e.job_id=%s AND e.kind='answers_saved'
                   AND e.id > COALESCE((SELECT max(r.id) FROM job_events r WHERE r.job_id=%s AND r.kind='resume'), 0)
                   LIMIT 1""",
                (job_id, job_id),
            )
            is not None
        )

    def update_jobs_for_resume(self, job_id: str) -> int:
        return self.db.run("UPDATE jobs SET waiting_on='agent', blocked_reason=NULL, updated_at=now() WHERE id=%s", (job_id,))

    def update_job_forms_clear_missing(self, job_id: str) -> int:
        """A job that left preparation has no open question: stale `missing` must not render a question card."""
        return self.db.run(
            "UPDATE job_forms SET missing='[]'::jsonb, updated_at=now() WHERE job_id=%s AND missing IS DISTINCT FROM '[]'::jsonb",
            (job_id,),
        )

    def find_job_events_for_kind(self, job_id: str, kind: str) -> Row | None:
        return self.db.one("SELECT 1 FROM job_events WHERE job_id=%s AND kind=%s LIMIT 1", (job_id, kind))

    def update_jobs_for_submit_mode(self, job_id: str, mode: str | None) -> int:
        return self.db.run("UPDATE jobs SET submit_mode=%s WHERE id=%s", (mode, job_id))

    def update_for_acknowledge_unapproved_submit(self, job_id: str) -> int:
        """Close the alert's task and hand the job back to the employer wait. Never touches automation."""
        self.db.run("UPDATE tasks SET state='done', leased_by=NULL WHERE job_id=%s AND state IN ('blocked','queued')", (job_id,))
        return self.db.run(
            "UPDATE jobs SET waiting_on='site', blocked_reason=NULL, updated_at=now() WHERE id=%s AND waiting_on='you'", (job_id,)
        )

    def update_jobs_for_transition(self, to: str, job_id: str) -> int:
        return self.db.run("UPDATE jobs SET status=%s::job_status, updated_at=now() WHERE id=%s", (to, job_id))

    def update_jobs_for_set_job_settings(self, track: str | None, mode: str | None, job_id: str, job_id_4: str) -> Row | None:
        return self.db.one(
            """UPDATE jobs SET cv_track_id=COALESCE(%s,cv_track_id), cv_mode=COALESCE(%s::cv_mode,cv_mode),
                 cv_version_id=NULL, updated_at=now()
               WHERE id=%s AND status IN ('discovered','screened','preparing','ready','awaiting_approval')
                 AND NOT EXISTS (SELECT 1 FROM job_events WHERE job_id=%s AND kind='submit_clicked') RETURNING status""",
            (track, mode, job_id, job_id_4),
        )

    def insert_tasks_for_ingest(self, id: Any) -> int:
        return self.db.run(
            "INSERT INTO tasks(job_id,step,state) VALUES(%s,'run','queued') ON CONFLICT (job_id,step) DO NOTHING", (id,)
        )

    def update_job_classification(self, job_level: str | None, employment_type: str | None, job_id: str) -> int:
        return self.db.run(
            "UPDATE jobs SET job_level=COALESCE(%s,job_level), employment_type=COALESCE(%s,employment_type) WHERE id=%s",
            (job_level, employment_type, job_id),
        )

    def update_companies_for_set_meta(self, company_description: str | None, cid: Any) -> int:
        return self.db.run("UPDATE companies SET description=COALESCE(description, %s) WHERE id=%s", (company_description, cid))

    def set_suggested_cv_track(self, value_1: Any, value_2: Any, job_id: str) -> int:
        return self.db.run("UPDATE jobs SET cv_track_id=%s, match_score=%s WHERE id=%s", (value_1, value_2, job_id))

    def update_match_score(self, value_1: Any, job_id: str) -> int:
        return self.db.run("UPDATE jobs SET match_score=%s WHERE id=%s", (value_1, job_id))

    def find_cv_tracks_for_set_job_settings(self, track: str | None) -> Row | None:
        return self.db.one("SELECT 1 FROM cv_tracks WHERE id=%s AND archived_at IS NULL", (track,))

    def allow_unknown_questions(self, job_id: str) -> None:
        self.db.run("UPDATE jobs SET try_questions_anyway=true WHERE id=%s", (job_id,))

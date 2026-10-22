"""PostgreSQL operations for tracker. Transactions are owned by application services."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..entity.job import Job
from .database import Database, Row


class TrackerRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def find_jobs_for_detail_extra(self, job_id: str) -> Row | None:
        return self.db.one(
            """SELECT j.source, j.pay, j.location, j.job_level AS "jobLevel", j.employment_type AS "employmentType", j.submitted_via AS "submittedVia",
                      j.resume_label AS "resumeLabel", j.date_contacted::text AS "dateContacted", j.interview_on::text AS "interviewOn",
                      j.follow_up_on::text AS "followUpOn", j.user_status AS "userStatus", j.notes, j.work_mode AS "workMode",
                      a.applied_on::text AS "appliedOn", c.description AS "companyDescription"
               FROM jobs j LEFT JOIN companies c ON c.id=j.company_id LEFT JOIN applications a ON a.job_id=j.id WHERE j.id=%s""",
            (job_id,),
        )

    def insert_applications_for_set_applied_on(self, job_id: str, parsed: Any) -> int:
        return self.db.run(
            """INSERT INTO applications(job_id, applied_on, applied_at, confirmation, confirmation_text)
               VALUES(%s,%s,now(),'user-confirmed','Date set by you') ON CONFLICT (job_id) DO UPDATE SET applied_on=EXCLUDED.applied_on""",
            (job_id, parsed),
        )

    def list_jobs_for_duplicates(self) -> list[Row]:
        return self.db.all("""SELECT j.id, c.name AS company, j.role, j.company_norm AS cn, j.title_norm AS tn, j.url, j.status,
                      a.applied_on::text AS "appliedOn", j.updated_at AS "updatedAt"
               FROM jobs j LEFT JOIN companies c ON c.id=j.company_id LEFT JOIN applications a ON a.job_id=j.id
               WHERE j.archived_at IS NULL AND j.title_norm <> '' ORDER BY j.company_norm, j.title_norm, j.created_at""")

    def lock_job(self, job_id: str) -> Row | None:
        return self.db.one("SELECT id FROM jobs WHERE id=%s FOR UPDATE", (job_id,))

    def find_job_tracking_state(self, job_id: str) -> Row | None:
        return self.db.one("SELECT source, status, archived_at FROM jobs WHERE id=%s", (job_id,))

    def find_transaction_lock_for_add_external(self, jid: Any) -> Row | None:
        return self.db.one("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (jid,))

    def find_jobs_for_add_external(
        self,
        jid: Any,
        canonical: Any,
        board_id: Any,
        tenant: Any,
        req: Any,
        req_6: Any,
        url: str | None,
        value_8: Any,
        value_9: Any,
    ) -> Row | None:
        return self.db.one(
            """SELECT id, source FROM jobs WHERE id=%s OR canonical_url_hash=%s
                   OR (board_id=%s AND tenant=%s AND requisition_id=%s AND %s::text IS NOT NULL)
                   OR (%s::text IS NULL AND company_norm=%s AND title_norm=%s) LIMIT 1""",
            (jid, canonical, board_id, tenant, req, req_6, url, value_8, value_9),
        )

    def find_tasks_for_archive(self, job_id: str) -> Row | None:
        return self.db.one(
            "SELECT leased_by FROM tasks WHERE job_id=%s AND state='leased' AND visible_at>now() LIMIT 1", (job_id,)
        )

    def find_jobs_for_archive(self, job_id: str) -> Row | None:
        return self.db.one("SELECT archived_at FROM jobs WHERE id=%s", (job_id,))

    def update_jobs_for_archive(self, job_id: str) -> int:
        return self.db.run("UPDATE jobs SET archived_at=now(), updated_at=now() WHERE id=%s", (job_id,))

    def update_tasks_for_archive(self, job_id: str) -> int:
        return self.db.run("UPDATE tasks SET state='blocked' WHERE job_id=%s AND state='queued'", (job_id,))

    def update_browser_sessions_for_archive(self, job_id: str) -> int:
        return self.db.run("UPDATE browser_sessions SET close_requested=true WHERE job_id=%s", (job_id,))

    def update_jobs_for_restore(self, job_id: str) -> int:
        return self.db.run("UPDATE jobs SET archived_at=NULL, updated_at=now() WHERE id=%s", (job_id,))

    def find_jobs_for_purge(self, job_id: str) -> Row | None:
        return self.db.one("SELECT archived_at, source FROM jobs WHERE id=%s FOR UPDATE", (job_id,))

    def update_cost_ledger_for_purge(self, job_id: str) -> int:
        return self.db.run("UPDATE cost_ledger SET job_id=NULL WHERE job_id=%s", (job_id,))

    def delete_jobs_for_purge(self, job_id: str) -> int:
        return self.db.run("DELETE FROM jobs WHERE id=%s", (job_id,))

    def find_jobs_for_merge(self, keep_id: str) -> Row | None:
        return self.db.one("SELECT * FROM jobs WHERE id=%s FOR UPDATE", (keep_id,))

    def insert_facts_for_fact_create(self, fact_id: Any, text: Any, source: str) -> int:
        return self.db.run(
            "INSERT INTO facts(id, claim, source, confirmed_at) VALUES(%s,%s,%s,now()) ON CONFLICT (id) DO NOTHING",
            (fact_id, text, source),
        )

    def delete_facts_for_fact_delete(self, fact_id: str) -> int:
        return self.db.run("DELETE FROM facts WHERE id=%s", (fact_id,))

    def delete_answers_for_answer_delete(self, question_key: str, scope: str) -> int:
        return self.db.run("DELETE FROM answers WHERE question_key=%s AND scope=%s", (question_key, scope))

    def update_jobs_for_update(self, status: Any, job_id: str) -> int:
        return self.db.run("UPDATE jobs SET status=%s::job_status, updated_at=now() WHERE id=%s", (status, job_id))

    def insert_boards_for_add_external(self, board_id: Any, board_id_2: Any) -> int:
        return self.db.run(
            "INSERT INTO boards(id, display_name, automation) VALUES(%s,%s,'assisted') ON CONFLICT (id) DO NOTHING",
            (board_id, board_id_2),
        )

    def insert_jobs_for_add_external(
        self,
        jid: Any,
        cid: Any,
        value_3: Any,
        canonical: Any,
        canonical_5: Any,
        board_id: Any,
        tenant: Any,
        req: Any,
        company_name: Any,
        value_10: Any,
        canonical_11: Any,
        source: str,
        status: str,
    ) -> int:
        return self.db.run(
            """INSERT INTO jobs(id, company_id, role, url, canonical_url_hash, board_id, tenant, requisition_id,
                                        company_norm, title_norm, pacing_hostname, source, status, waiting_on)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::job_status,'site')""",
            (
                jid,
                cid,
                value_3,
                canonical,
                canonical_5,
                board_id,
                tenant,
                req,
                company_name,
                value_10,
                canonical_11,
                source,
                status,
            ),
        )

    def update_companies_for_add_external(self, company_description: str | None, jid: Any) -> int:
        return self.db.run(
            "UPDATE companies SET description=%s WHERE id=(SELECT company_id FROM jobs WHERE id=%s)", (company_description, jid)
        )

    def insert_applications_for_add_external(self, jid: Any, applied_on: date | None, value_3: Any) -> int:
        return self.db.run(
            """INSERT INTO applications(job_id, applied_on, applied_at, confirmation, confirmation_text)
                       VALUES(%s,%s,now(),'user-confirmed',%s) ON CONFLICT (job_id) DO NOTHING""",
            (jid, applied_on, value_3),
        )

    def update_jobs_for_add_external(self, jid: Any) -> int:
        return self.db.run("UPDATE jobs SET waiting_on='agent', blocked_reason=NULL WHERE id=%s", (jid,))

    def find_applications_for_purge(self, job_id: str) -> Row | None:
        return self.db.one("SELECT 1 FROM applications WHERE job_id=%s AND confirmation <> 'user-confirmed' LIMIT 1", (job_id,))

    def find_browser_session_tab_for_purge(self, job_id: str) -> Row | None:
        return self.db.one("SELECT target_id FROM browser_sessions WHERE job_id=%s", (job_id,))

    def find_tasks_for_merge(self, other_id: Any) -> Row | None:
        return self.db.one("SELECT 1 FROM tasks WHERE job_id=%s AND state='leased' AND visible_at>now()", (other_id,))

    def find_applications_for_merge(self, keep_id: str) -> Row | None:
        return self.db.one("SELECT applied_on FROM applications WHERE job_id=%s", (keep_id,))

    def find_application_confirmation(self, other_id: Any) -> Row | None:
        return self.db.one(
            "SELECT applied_on, applied_at, confirmation, confirmation_text FROM applications WHERE job_id=%s", (other_id,)
        )

    def find_facts_for_fact_create(self, fact_id: Any) -> Row | None:
        return self.db.one("SELECT 1 FROM facts WHERE id=%s", (fact_id,))

    def insert_applications_for_merge(
        self, keep_id: str, applied_on: Any, applied_at: Any, confirmation: Any, confirmation_text: Any
    ) -> int:
        return self.db.run(
            """INSERT INTO applications(job_id, applied_on, applied_at, confirmation, confirmation_text)
                           VALUES(%s,%s,%s,%s::confirmation_kind,%s)
                           ON CONFLICT (job_id) DO UPDATE SET applied_on=EXCLUDED.applied_on""",
            (keep_id, applied_on, applied_at, confirmation, confirmation_text),
        )

    def find_job_record(self, job_id: str) -> Job | None:
        row = self.db.one("SELECT * FROM jobs WHERE id=%s", (job_id,))
        return Job.model_validate(row) if row is not None else None

    def list_rows(
        self, status: str | None, q: str | None, waiting_on: str | None, limit: int, offset: int, archived: bool
    ) -> list[Row]:
        where: list[str] = ["j.archived_at IS NOT NULL" if archived else "j.archived_at IS NULL"]
        args: list[Any] = []
        if status:
            where.append("j.status=%s::job_status")
            args.append(status)
        if waiting_on:
            where.append("j.waiting_on=%s::waiting_on")
            args.append(waiting_on)
        if q:
            where.append("(j.role ILIKE %s OR c.name ILIKE %s OR j.notes ILIKE %s)")
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            args += [like] * 3
        args += [min(limit, 500), offset]
        sql = f"""SELECT j.id, c.name AS company, j.role, a.applied_on::text AS "appliedOn", j.status,
                             j.user_status AS "userStatus", j.waiting_on AS "waitingOn", j.blocked_reason AS "blockedReason",
                             j.date_contacted::text AS "dateContacted", j.interview_on::text AS "interviewOn",
                             j.follow_up_on::text AS "followUpOn", j.resume_label AS "resumeLabel",
                             j.cv_track_id AS "cvTrack", j.cv_mode AS "cvMode", j.submitted_via AS "submittedVia",
                             j.job_level AS "jobLevel", j.pay AS salary, j.location, j.work_mode AS "workMode",
                             j.employment_type AS "employmentType", j.url, j.source, j.notes,
                             (j.posting_text IS NOT NULL AND j.posting_text <> '') AS "hasDescription",
                             j.updated_at AS "updatedAt", j.archived_at AS "archivedAt"
                      FROM jobs j LEFT JOIN companies c ON c.id=j.company_id LEFT JOIN applications a ON a.job_id=j.id
                      {"WHERE " + " AND ".join(where) if where else ""}
                      ORDER BY COALESCE(a.applied_on, j.created_at::date) DESC, j.updated_at DESC LIMIT %s OFFSET %s"""
        return self.db.all(sql, args)

    def delete_job_dependencies(self, job_id: str) -> None:
        for table in (
            "job_events", "approvals", "tasks", "job_forms", "applications",
            "browser_hands_usage", "browser_sessions", "cv_commit_jobs", "projection_dirty", "recipe_suggestions",
        ):  # fmt: skip
            self.db.run(f"DELETE FROM {table} WHERE job_id=%s", (job_id,))

    def update_fields(self, job_id: str, values: dict[str, Any], *, cast_dates: bool = True) -> None:
        allowed = {
            "role",
            "location",
            "work_mode",
            "pay",
            "job_level",
            "employment_type",
            "submitted_via",
            "resume_label",
            "date_contacted",
            "interview_on",
            "follow_up_on",
            "user_status",
            "notes",
            "status",
            "posting_text",
            "posting_sha256",
        }
        if not values.keys() <= allowed:
            raise ValueError("unsupported job column")
        sets = []
        for column in values:
            suffix = (
                "::job_status"
                if column == "status"
                else "::date"
                if cast_dates and column in {"date_contacted", "interview_on", "follow_up_on"}
                else ""
            )
            sets.append(f"{column}=%s{suffix}")
        if sets:
            self.db.run(f"UPDATE jobs SET {', '.join(sets)}, updated_at=now() WHERE id=%s", [*values.values(), job_id])

    def fill_imported_fields(
        self,
        job_id: str,
        values: dict[str, Any],
        role: str | None,
        title_norm: str | None,
        posting: str | None,
        posting_sha256: str | None,
        status: str | None,
    ) -> None:
        allowed = {
            "role",
            "location",
            "work_mode",
            "pay",
            "job_level",
            "employment_type",
            "submitted_via",
            "resume_label",
            "date_contacted",
            "interview_on",
            "follow_up_on",
            "user_status",
            "notes",
        }
        if not values.keys() <= allowed:
            raise ValueError("unsupported job column")
        sets = []
        args: list[Any] = []
        for column, value in values.items():
            if column in {"date_contacted", "interview_on", "follow_up_on"}:
                sets.append(f"{column}=COALESCE({column},%s::date)")
            else:
                sets.append(f"{column}=COALESCE(NULLIF({column},''),%s)")
            args.append(value)
        if role:
            sets += ["role=CASE WHEN role='(role not captured yet)' THEN %s ELSE role END", "title_norm=%s"]
            args += [role, title_norm]
        if posting:
            sets += [
                "posting_sha256=CASE WHEN COALESCE(posting_text,'')='' THEN %s ELSE posting_sha256 END",
                "posting_text=COALESCE(NULLIF(posting_text,''),%s)",
            ]
            args += [posting_sha256, posting]
        if status:
            sets.append("status=%s::job_status")
            args.append(status)
        if sets:
            self.db.run(f"UPDATE jobs SET {', '.join(sets)}, updated_at=now() WHERE id=%s", [*args, job_id])

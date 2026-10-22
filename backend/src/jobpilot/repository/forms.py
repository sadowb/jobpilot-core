"""PostgreSQL operations for forms. Transactions are owned by application services."""

from __future__ import annotations

from typing import Any

from .database import Database, Row


class FormsRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def lock_job(self, job_id: str) -> Row | None:
        return self.db.one("SELECT id FROM jobs WHERE id=%s FOR UPDATE", (job_id,))

    def insert_job_forms_for_set_inventory(self, job_id: str, value: Any, page: int | None, complete: bool) -> int:
        return self.db.run(
            """INSERT INTO job_forms(job_id, inventory, current_page, pages_complete) VALUES(%s,%s,%s,%s)
               ON CONFLICT (job_id) DO UPDATE SET inventory=EXCLUDED.inventory, plan=NULL, missing='[]',
                 current_page=EXCLUDED.current_page, pages_complete=EXCLUDED.pages_complete,
                 fill_done_at=NULL, verified_at=NULL, draft_hash=NULL, updated_at=now()""",
            (job_id, value, page, complete),
        )

    def list_profile_for_load_known(self) -> list[Row]:
        return self.db.all("SELECT key, value FROM profile")

    def list_answers_for_load_known(self) -> list[Row]:
        return self.db.all("SELECT question_key, answer, scope FROM answers")

    def update_job_forms_for_make_plan(self, value: Any, value_2: Any, job_id: str) -> int:
        return self.db.run("UPDATE job_forms SET plan=%s, missing=%s, updated_at=now() WHERE job_id=%s", (value, value_2, job_id))

    def insert_answers_for_answer_set(
        self, question: Any, answer: str, scope: str, source: str, answer_5: str, source_6: str
    ) -> int:
        return self.db.run(
            """INSERT INTO answers(question_key, answer, scope, source, confirmed_at) VALUES(%s,%s,%s,%s,now())
               ON CONFLICT (question_key, scope) DO UPDATE SET answer=%s, source=%s, confirmed_at=now()""",
            (question, answer, scope, source, answer_5, source_6),
        )

    def insert_answer_if_absent(self, question: str, answer: str, scope: str, source: str) -> int:
        """Never replaces an existing answer for the same (question, scope): an owner's edit always wins."""
        return self.db.run(
            """INSERT INTO answers(question_key, answer, scope, source, confirmed_at) VALUES(%s,%s,%s,%s,now())
               ON CONFLICT (question_key, scope) DO NOTHING""",
            (question, answer, scope, source),
        )

    def update_job_forms_for_invalidate_form(self, reset_plan: bool, reset_plan_2: bool, agent_edit: Any, job_id: str) -> int:
        return self.db.run(
            """UPDATE job_forms SET fill_done_at=NULL, verified_at=NULL, draft_hash=NULL,
               plan=CASE WHEN %s THEN NULL ELSE plan END, missing=CASE WHEN %s THEN '[]'::jsonb ELSE missing END,
               revision=revision+%s, updated_at=now() WHERE job_id=%s
                 AND (fill_done_at IS NOT NULL OR verified_at IS NOT NULL OR draft_hash IS NOT NULL
                      OR %s<>0 OR (%s AND (plan IS NOT NULL OR missing IS DISTINCT FROM '[]'::jsonb)))""",
            (reset_plan, reset_plan_2, agent_edit, job_id, agent_edit, reset_plan),
        )

    def revoke_submit_approvals(self, job_id: str) -> int:
        return self.db.run("DELETE FROM approvals WHERE job_id=%s AND kind='submit' AND consumed_at IS NULL", (job_id,))

    def update_job_forms_for_record_fill(self, failures: int, job_id: str) -> int:
        return self.db.run(
            "UPDATE job_forms SET fill_done_at=CASE WHEN %s=0 THEN now() ELSE NULL END WHERE job_id=%s", (failures, job_id)
        )

    def update_job_forms_for_record_verification(self, draft: str | None, draft_2: str | None, job_id: str) -> int:
        return self.db.run(
            "UPDATE job_forms SET verified_at=CASE WHEN %s::text IS NULL THEN NULL ELSE now() END, draft_hash=%s WHERE job_id=%s",
            (draft, draft_2, job_id),
        )

    def list_jobs_for_replan_blocked(self) -> list[Row]:
        return self.db.all("""SELECT j.id, f.missing FROM jobs j JOIN job_forms f ON f.job_id=j.id
               WHERE j.waiting_on='you' AND j.status='preparing' AND j.archived_at IS NULL
                 AND f.inventory IS NOT NULL AND jsonb_array_length(f.missing) > 0""")

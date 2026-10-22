"""PostgreSQL operations for "Refresh details" (ADR 0029). Transactions are owned by services."""

from __future__ import annotations

from typing import Any

from ..domain.details import ATS_NAMES, ats_host_regex
from .database import Database, Row

_COLUMNS = {"role", "location", "work_mode", "pay", "job_level", "employment_type"}


class DetailsRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def lock_job(self, job_id: str) -> Row | None:
        return self.db.one("SELECT id, archived_at FROM jobs WHERE id=%s FOR UPDATE", (job_id,))

    def find_job(self, job_id: str) -> Row | None:
        """What a refresh may need to read: the six detail columns, the company name and the links."""
        return self.db.one(
            """SELECT j.id, j.status, j.url, j.apply_url, j.role, j.location, j.work_mode, j.pay, j.job_level,
                      j.employment_type, c.name AS company, j.archived_at, length(COALESCE(j.posting_text,'')) AS posting_len
               FROM jobs j LEFT JOIN companies c ON c.id=j.company_id WHERE j.id=%s""",
            (job_id,),
        )

    def find_posting_text(self, job_id: str) -> str:
        row = self.db.one("SELECT posting_text FROM jobs WHERE id=%s", (job_id,))
        return str(row["posting_text"] or "") if row else ""

    def owner_edited_fields(self, job_id: str) -> set[str]:
        """Detail fields the owner changed by hand: the `fields` of every `tracking_updated` event (user actor)."""
        rows = self.db.all(
            "SELECT payload->'fields' AS fields FROM job_events WHERE job_id=%s AND kind='tracking_updated' AND actor='user'",
            (job_id,),
        )
        edited: set[str] = set()
        for row in rows:
            fields = row["fields"]
            if isinstance(fields, list):
                edited.update(str(f) for f in fields)
        return edited

    # ---- request row
    def find_request(self, job_id: str) -> Row | None:
        return self.db.one(
            "SELECT job_id, state, requested_at, started_at, finished_at, via, reason FROM job_detail_requests WHERE job_id=%s",
            (job_id,),
        )

    def request(self, job_id: str) -> bool:
        """Queue a refresh. False when one is already queued or running (idempotent); done/failed ones are re-queued."""
        row = self.db.one(
            """INSERT INTO job_detail_requests(job_id, state) VALUES(%s, 'queued')
               ON CONFLICT (job_id) DO UPDATE SET state='queued', requested_at=now(), started_at=NULL, finished_at=NULL,
                    via=NULL, reason=NULL
               WHERE job_detail_requests.state IN ('done','failed') RETURNING job_id""",
            (job_id,),
        )
        return row is not None

    def claim(self, capacity: int) -> list[str]:
        """Move up to `capacity` minus the running ones from queued to running, oldest first."""
        rows = self.db.all(
            """UPDATE job_detail_requests SET state='running', started_at=now(), attempts=attempts+1
               WHERE job_id IN (SELECT job_id FROM job_detail_requests WHERE state='queued' ORDER BY requested_at
                                LIMIT GREATEST(0, %s - (SELECT count(*) FROM job_detail_requests WHERE state='running'))
                                FOR UPDATE SKIP LOCKED)
               RETURNING job_id""",
            (capacity,),
        )
        return [str(r["job_id"]) for r in rows]

    def requeue(self, job_id: str) -> int:
        return self.db.run(
            "UPDATE job_detail_requests SET state='queued', started_at=NULL WHERE job_id=%s AND state='running'", (job_id,)
        )

    def finish(self, job_id: str, state: str, via: str | None, reason: str | None) -> int:
        return self.db.run(
            """UPDATE job_detail_requests SET state=%s, via=%s, reason=%s, finished_at=now()
               WHERE job_id=%s AND state='running'""",
            (state, via, reason, job_id),
        )

    def expire_stale(self, seconds: int) -> list[str]:
        rows = self.db.all(
            """UPDATE job_detail_requests SET state='failed', reason='timeout', finished_at=now()
               WHERE state='running' AND started_at < now() - make_interval(secs => %s) RETURNING job_id""",
            (seconds,),
        )
        return [str(r["job_id"]) for r in rows]

    def apply(self, job_id: str, changes: dict[str, str], company_id: int | None, norms: dict[str, str]) -> int:
        """Write exactly the merged columns. Never touches status, waiting_on, forms, approvals or updated_at."""
        if not set(changes) <= _COLUMNS | {"company"}:
            raise ValueError("unsupported details column")
        sets: list[str] = []
        args: list[Any] = []
        for column, value in changes.items():
            if column == "company":
                sets.append("company_id=%s")
                args.append(company_id)
            else:
                sets.append(f"{column}=%s")
                args.append(value)
        for column, value in norms.items():
            if column not in {"company_norm", "title_norm", "location_norm"}:
                raise ValueError("unsupported details column")
            sets.append(f"{column}=%s")
            args.append(value)
        if not sets:
            return 0
        return self.db.run(f"UPDATE jobs SET {', '.join(sets)} WHERE id=%s", [*args, job_id])

    def set_posting_text(self, job_id: str, text: str) -> int:
        """Fill an empty posting text only (never replaces text already captured); the hash follows it."""
        return self.db.run(
            "UPDATE jobs SET posting_text=%s, posting_sha256=encode(sha256(convert_to(%s,'UTF8')),'hex') "
            "WHERE id=%s AND length(COALESCE(posting_text,''))<100",
            (text, text, job_id),
        )

    def list_missing(self, limit: int, retry_hours: int) -> list[str]:
        """Live jobs whose role or company is a placeholder, with no open request and no recent attempt."""
        rows = self.db.all(
            r"""SELECT j.id FROM jobs j
                LEFT JOIN companies c ON c.id=j.company_id
                LEFT JOIN job_detail_requests r ON r.job_id=j.id
                WHERE j.archived_at IS NULL
                  AND (btrim(COALESCE(j.role,''))='' OR lower(j.role) LIKE '(role not captured%%'
                       OR btrim(COALESCE(c.name,''))='' OR lower(c.name) = ANY(%s)
                       OR (lower(c.name) ~ '^([a-z0-9-]+\.)+[a-z]{2,}$'
                           AND (lower(c.name) ~ %s
                                OR lower(c.name) = lower(substring(j.url from '://([^/:?#]+)'))
                                OR lower(c.name) = lower(COALESCE(substring(j.apply_url from '://([^/:?#]+)'), '')))))
                  AND (r.job_id IS NULL OR (r.state IN ('done','failed')
                       AND r.finished_at < now() - make_interval(hours => %s)))
                ORDER BY j.created_at DESC LIMIT %s""",
            (sorted(ATS_NAMES), ats_host_regex(), retry_hours, limit),
        )
        return [str(r["id"]) for r in rows]

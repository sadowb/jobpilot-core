"""Direct-mode reads and reported form storage; service owns each transaction."""

from __future__ import annotations

from typing import Any

from ..domain.address import split_us_address
from ..domain.answers import SKIPPED
from ..dto.direct import SECRET_KEY
from .database import Database, J, Row


class DirectRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def owner_data(self, job_id: str) -> dict[str, Any]:
        profile = self.db.all("SELECT key, value FROM profile")
        saved = [
            r
            for r in self.db.all(
                "SELECT question_key, answer, scope FROM answers WHERE scope=ANY(%s) ORDER BY question_key, scope",
                (["global", f"job:{job_id}"],),
            )
            if not SECRET_KEY.search(r["question_key"])
        ]
        profile_data = {r["key"]: r["value"] for r in profile}
        # The owner saved the address once as free text; forms split it into line 1, unit and ZIP.
        address = next((r["answer"] for r in saved if r["scope"] == "global" and "address" in r["question_key"]), "")
        for key, value in split_us_address(str(address)).items():
            profile_data.setdefault(key, value)
        return {
            "profile": profile_data,
            "facts": self.db.all("SELECT claim FROM facts WHERE confirmed_at IS NOT NULL ORDER BY id"),
            "answers": [r for r in saved if r["answer"] != SKIPPED],
            # The owner chose to leave these empty on this application: do not guess, do not ask again.
            "skipped_questions": [r["question_key"] for r in saved if r["answer"] == SKIPPED],
        }

    def skipped_question_keys(self, job_id: str) -> set[str]:
        rows = self.db.all("SELECT question_key FROM answers WHERE scope=%s AND answer=%s", (f"job:{job_id}", SKIPPED))
        return {str(r["question_key"]) for r in rows}

    def cv(self, job_id: str) -> Row | None:
        return self.db.one("SELECT v.sha256 FROM jobs j JOIN cv_versions v ON v.id=j.cv_version_id WHERE j.id=%s", (job_id,))

    def selection_candidates(self) -> list[Row]:
        return self.db.all(
            """SELECT t.id, t.title, t.market, v.id AS version_id, v.sha256
               FROM cv_tracks t JOIN cv_versions v ON v.id=t.current_version_id
               WHERE t.archived_at IS NULL ORDER BY t.id LIMIT 25"""
        )

    def count_events(self, job_id: str, kind: str) -> int:
        row = self.db.one("SELECT count(*) AS n FROM job_events WHERE job_id=%s AND kind=%s", (job_id, kind))
        return int(row["n"]) if row else 0

    def last_event_payload(self, job_id: str, kind: str) -> dict[str, Any]:
        row = self.db.one("SELECT payload FROM job_events WHERE job_id=%s AND kind=%s ORDER BY id DESC LIMIT 1", (job_id, kind))
        payload = row["payload"] if row else None
        return payload if isinstance(payload, dict) else {}

    def last_event(self, job_id: str, kind: str) -> tuple[int, dict[str, Any]] | None:
        row = self.db.one(
            "SELECT id, payload FROM job_events WHERE job_id=%s AND kind=%s ORDER BY id DESC LIMIT 1", (job_id, kind)
        )
        if not row:
            return None
        return int(row["id"]), row["payload"] if isinstance(row["payload"], dict) else {}

    def count_events_since(self, job_id: str, kind: str, since_kind: str) -> int:
        """Events of `kind` newer than the latest `since_kind` event (all of them when there is none)."""
        row = self.db.one(
            """SELECT count(*) AS n FROM job_events WHERE job_id=%s AND kind=%s
               AND id > COALESCE((SELECT max(id) FROM job_events WHERE job_id=%s AND kind=%s), 0)""",
            (job_id, kind, job_id, since_kind),
        )
        return int(row["n"]) if row else 0

    def owner_completed_labels(self, job_id: str) -> set[str]:
        """Normalised labels of questions the owner said they filled in the tab themselves (any earlier time)."""
        rows = self.db.all("SELECT payload FROM job_events WHERE job_id=%s AND kind='owner_completed'", (job_id,))
        labels: set[str] = set()
        for row in rows:
            raw = row["payload"].get("labels") if isinstance(row["payload"], dict) else None
            if isinstance(raw, list):
                labels.update(str(item) for item in raw)
        return labels

    def current_track_version(self, track: str) -> int | None:
        row = self.db.one("SELECT current_version_id AS v FROM cv_tracks WHERE id=%s AND archived_at IS NULL", (track,))
        return int(row["v"]) if row and row["v"] is not None else None

    def assign_reasoned_track(self, track: str, version: int, job_id: str) -> Row | None:
        """Pin exactly the compared version; a concurrent owner choice or CV edit wins."""
        return self.db.one(
            """UPDATE jobs j SET cv_track_id=t.id, cv_version_id=v.id, match_score=NULL
               FROM cv_tracks t JOIN cv_versions v ON v.id=t.current_version_id
               WHERE j.id=%s AND t.id=%s AND v.id=%s AND t.archived_at IS NULL
                 AND j.archived_at IS NULL AND j.cv_track_id IS NULL AND j.cv_version_id IS NULL AND j.cv_mode='base'
                 AND j.status IN ('discovered','screened','preparing','ready')
                 AND NOT EXISTS (SELECT 1 FROM job_events e WHERE e.job_id=j.id AND e.kind='submit_clicked')
               RETURNING v.sha256""",
            (job_id, track, version),
        )

    def plan(self, job_id: str, plan: list[dict[str, Any]], missing: list[str]) -> None:
        self.db.run("UPDATE job_forms SET plan=%s, missing=%s, updated_at=now() WHERE job_id=%s", (J(plan), J(missing), job_id))

    def lock_plan_write(self, job_id: str) -> None:
        self.db.one("SELECT id FROM tasks WHERE job_id=%s FOR UPDATE", (job_id,))
        self.db.one("SELECT id FROM jobs WHERE id=%s FOR UPDATE", (job_id,))

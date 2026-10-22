"""Named SQL reads for the dashboard. Values are bound parameters, never SQL interpolation."""

from __future__ import annotations

from ..domain.answers import SKIPPED
from .database import Database, Row


class DashboardRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def job_events(self, job_id: str) -> list[Row]:
        return self.db.all(
            "SELECT ts, kind, actor, payload FROM job_events WHERE job_id=%s ORDER BY id DESC LIMIT 100", (job_id,)
        )

    def answer_bank(self) -> list[Row]:
        return self.db.all("SELECT question_key, answer, scope FROM answers")

    def owner_completed_labels(self, job_id: str) -> set[str]:
        """Normalised labels the owner said they filled in the tab themselves (see `owner_completed` events)."""
        labels: set[str] = set()
        for row in self.db.all("SELECT payload FROM job_events WHERE job_id=%s AND kind='owner_completed'", (job_id,)):
            raw = row["payload"].get("labels") if isinstance(row["payload"], dict) else None
            if isinstance(raw, list):
                labels.update(str(item) for item in raw)
        return labels

    def blocked_jobs(self, limit: int, offset: int) -> list[Row]:
        return self.db.all(
            """SELECT j.id, c.name AS company, j.role, b.display_name AS board, j.blocked_reason AS reason, f.missing,
                  f.inventory, j.status, f.submit_evidence,
                  COALESCE((SELECT sum((e.payload->>'count')::int) FROM job_events e
                             WHERE e.job_id=j.id AND e.kind='answers_saved'
                               AND e.id > COALESCE((SELECT max(r.id) FROM job_events r
                                                     WHERE r.job_id=j.id AND r.kind='resume'), 0)), 0)::int
                    AS "answersPending",
                  c.slug AS "companySlug",
                  (SELECT count(*) FROM answers a WHERE a.scope='job:' || j.id AND a.answer <> %s)::int AS "answeredCount",
                  COALESCE((SELECT jsonb_array_length(e.payload->'settled') FROM job_events e
                             WHERE e.job_id=j.id AND e.kind='answers_reasked' AND jsonb_typeof(e.payload->'settled')='array'
                               AND e.id > COALESCE((SELECT max(d.id) FROM job_events d
                                                     WHERE d.job_id=j.id AND d.kind='direct_result'), 0)
                             ORDER BY e.id DESC LIMIT 1), 0)::int AS "settledCount"
           FROM jobs j LEFT JOIN companies c ON c.id=j.company_id LEFT JOIN job_forms f ON f.job_id=j.id
                LEFT JOIN boards b ON b.id=j.board_id
           WHERE j.waiting_on='you' AND j.archived_at IS NULL ORDER BY j.updated_at DESC, j.id LIMIT %s OFFSET %s""",
            (
                SKIPPED,
                limit,
                offset,
            ),
        )

    def blocked_jobs_ready(self, limit: int, offset: int) -> list[Row]:
        return self.db.all(
            """SELECT j.id, c.name AS company, j.role, f.draft_hash AS "draftHash", j.cv_track_id AS "cvTrack",
                  j.cv_mode AS "cvMode", COALESCE(b.automation::text, 'assisted') AS policy, f.park_signal AS "parkSignal",
                  jsonb_path_exists(COALESCE(f.plan, '[]'::jsonb), '$[*] ? (@.source == "agent")') AS "agentSet"
           FROM jobs j LEFT JOIN companies c ON c.id=j.company_id JOIN job_forms f ON f.job_id=j.id
                LEFT JOIN boards b ON b.id=j.board_id
           WHERE j.status='awaiting_approval' AND j.archived_at IS NULL AND f.verified_at IS NOT NULL ORDER BY j.updated_at, j.id LIMIT %s OFFSET %s""",
            (
                limit,
                offset,
            ),
        )

    def track_exists_for_ingest(self, cv_track: str) -> Row | None:
        return self.db.one("SELECT 1 FROM cv_tracks WHERE id=%s", (cv_track,))

    def tracks(self, marker_hash: str) -> list[Row]:
        return self.db.all(
            """SELECT t.id, t.title, t.keywords, t.default_mode AS "defaultMode", t.market, v.sha256,
                  (SELECT count(*)::int FROM jobs j WHERE j.cv_track_id=t.id AND j.archived_at IS NULL) AS jobs,
                  (v.tex_sha256 IS NOT NULL AND v.tex_sha256 <> %s) AS tailorable
           FROM cv_tracks t LEFT JOIN cv_versions v ON v.id=t.current_version_id
           WHERE t.archived_at IS NULL ORDER BY t.id""",
            (marker_hash,),
        )

    def track_exists(self, track: str) -> Row | None:
        return self.db.one("SELECT 1 FROM cv_tracks WHERE id=%s AND archived_at IS NULL", (track,))

    def track_metadata(self, track_id: str) -> Row | None:
        return self.db.one(
            "SELECT title, keywords, default_mode, market FROM cv_tracks WHERE id=%s AND archived_at IS NULL", (track_id,)
        )

    def track_cv(self, track_id: str) -> Row | None:
        return self.db.one(
            "SELECT v.sha256 FROM cv_tracks t JOIN cv_versions v ON v.id=t.current_version_id WHERE t.id=%s", (track_id,)
        )

    def job_cv(self, job_id: str) -> Row | None:
        return self.db.one(
            """SELECT COALESCE(jv.sha256, tv.sha256) AS sha FROM jobs j
           LEFT JOIN cv_versions jv ON jv.id=j.cv_version_id
           LEFT JOIN cv_tracks t ON t.id=j.cv_track_id LEFT JOIN cv_versions tv ON tv.id=t.current_version_id
           WHERE j.id=%s""",
            (job_id,),
        )

    def profile(self) -> list[Row]:
        return self.db.all('SELECT key, value, source, confirmed_at AS "confirmedAt" FROM profile ORDER BY key')

    def facts(self) -> list[Row]:
        return self.db.all('SELECT id, claim, source, confirmed_at AS "confirmedAt" FROM facts ORDER BY id')

    def answers(self) -> list[Row]:
        return self.db.all(
            'SELECT question_key AS "questionKey", answer, scope, source, confirmed_at AS "confirmedAt" FROM answers ORDER BY question_key'
        )

    def boards(self) -> list[Row]:
        return self.db.all("""SELECT id, display_name AS "displayName", automation FROM boards ORDER BY id""")

    def recipe_suggestions(self, limit: int, offset: int) -> list[Row]:
        return self.db.all(
            "SELECT id,job_id,board_id,note,status FROM recipe_suggestions ORDER BY id DESC LIMIT %s OFFSET %s",
            (
                limit,
                offset,
            ),
        )

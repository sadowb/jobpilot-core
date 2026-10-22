"""Disposable PostgreSQL check for AgentSessions and the legacy-UUID migration path.

Run from the repository root:
    docker compose run --rm --no-deps -e JOBPILOT_HOME=/tmp/jobpilot-agent-session-check \
      -e JOBPILOT_VAULT=/tmp/jobpilot-agent-session-check/vault -e JOBPILOT_NODE=mac control \
      python backend/scripts/check_agent_sessions_db.py

Exercises real DB-backed start/resume/reset/sanitize/cleanup/sweep and applies migration 0024 both to a fresh
schema and to a simulated legacy schema where agent_session_id already exists. It does not invoke live Claude,
Neo, a worker, or an approval/submission path.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
from pathlib import Path

import psycopg
from psycopg import sql

from jobpilot.config import get_settings
from jobpilot.domain.ids import JobIdentity, job_id
from jobpilot.migrate import MIGRATIONS, apply_data_migration, migrate
from jobpilot.repository.database import Database
from jobpilot.service import Services
from jobpilot.service.agent_sessions import RETENTION, AgentSessions


def main() -> int:
    base = get_settings()
    admin = psycopg.connect(
        host=base.pghost,
        port=base.pgport,
        user=base.pguser,
        password=base.pgpassword,
        dbname="postgres",
        autocommit=True,
    )
    names = [f"jp_check_{secrets.token_hex(4)}", f"jp_check_{secrets.token_hex(4)}"]
    previous = os.environ.get("PGDATABASE")
    previous_home = os.environ.get("JOBPILOT_HOME")
    previous_vault = os.environ.get("JOBPILOT_VAULT")
    root = Path(tempfile.mkdtemp(prefix="jobpilot-agent-session-check-"))
    os.environ["JOBPILOT_HOME"] = str(root)
    os.environ["JOBPILOT_VAULT"] = str(root / "vault")
    try:
        # Fresh current schema and real service/repository lifecycle.
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(names[0])))
        os.environ["PGDATABASE"] = names[0]
        get_settings.cache_clear()
        cfg = get_settings()
        migrate()
        db = Database(cfg, single=True)
        svc = Services(cfg, db)
        try:
            job = str(svc.ingest("https://job-boards.greenhouse.io/session-check/jobs/1001")["id"])
            sessions = AgentSessions(svc)
            sid, resume, context = sessions.start(job)
            assert not resume and not context
            assert db.one("SELECT agent_session_id::text AS id FROM jobs WHERE id=%s", (job,))["id"] == sid
            job_dir = sessions.directory(job)
            assert job_dir.stat().st_mode & 0o777 == 0o700
            transcript = job_dir / "projects" / "stable-cwd" / f"{sid}.jsonl"
            transcript.parent.mkdir(parents=True, mode=0o700)
            transcript.write_text(
                json.dumps({"type": "user", "sessionId": sid, "message": {"content": "DATA JSON ({}):\\n{}"}})
                + "\n"
                + json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": sid,
                        "message": {
                            "content": [{"type": "tool_use", "id": "t", "name": "neo", "input": {"code": "fixture-secret"}}]
                        },
                    }
                )
                + "\n"
            )
            assert sessions.start(job) == (sid, True, {})
            cleaned = transcript.read_text()
            assert "fixture-secret" not in cleaned
            assert transcript.stat().st_mode & 0o777 == 0o600

            # Same UUID with missing history triggers a fresh full-data session and safe event.
            transcript.unlink()
            fresh, resume, context = sessions.start(job)
            assert fresh != sid and not resume and not context
            assert db.one("SELECT agent_session_id::text AS id FROM jobs WHERE id=%s", (job,))["id"] == fresh
            assert (
                db.one("SELECT count(*)::int AS n FROM job_events WHERE job_id=%s AND kind='agent_session_reset'", (job,))["n"]
                == 1
            )

            # A corrupt transcript also resets and clears remembered DATA hashes.
            sessions.repo.remember(job, fresh, {"private": "hash"})
            broken = job_dir / "projects" / "stable-cwd" / f"{fresh}.jsonl"
            broken.parent.mkdir(parents=True, exist_ok=True)
            broken.write_text("broken json\n")
            newest, resume, context = sessions.start(job)
            assert newest != fresh and not resume and not context
            assert db.one("SELECT agent_session_context FROM jobs WHERE id=%s", (job,))["agent_session_context"] == {}

            # Active histories survive a sweep; terminal and orphan histories are removed.
            active_path = job_dir / "projects" / "stable-cwd" / f"{newest}.jsonl"
            active_path.parent.mkdir(parents=True, exist_ok=True)
            active_path.write_text(json.dumps({"type": "user", "sessionId": newest, "message": {"content": "DATA"}}))
            sessions.sweep()
            assert job_dir.exists() and active_path.exists()
            orphan_id = job_id(JobIdentity("https://example.invalid/orphan-session-fixture"))
            orphan_dir = sessions.directory(orphan_id)
            (orphan_dir / "ancillary").write_text("fixture")
            sessions.sweep()
            assert not orphan_dir.exists()
            old = root / "data" / "agent-sessions" / job
            old_time = os.path.getmtime(old) - RETENTION - 5
            os.utime(old, (old_time, old_time))
            sessions.sweep()
            assert not old.exists()

            # Real cleanup clears the DB UUID/context and removes only this job's directory.
            sessions.directory(job)
            svc.jobs.transition(job, "closed")
            assert not (sessions.root / job).exists()
            cleared = db.one("SELECT agent_session_id, agent_session_context FROM jobs WHERE id=%s", (job,))
            assert cleared == {"agent_session_id": None, "agent_session_context": {}}
        finally:
            db.close()
            shutil.rmtree(root, ignore_errors=True)
        print("AgentSessions DB lifecycle: PASS (start, resume UUID, reset, sanitize/modes, sweep, cleanup)")

        # Replay migrations through 0023, add the legacy UUID column, then apply current 0024.
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(names[1])))
        os.environ["PGDATABASE"] = names[1]
        get_settings.cache_clear()
        with psycopg.connect(get_settings().dsn, autocommit=False) as connection:
            connection.execute(
                "CREATE TABLE schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            for migration in sorted(MIGRATIONS.glob("*.sql")):
                if migration.name == "0024_agent_sessions.sql":
                    break
                for statement in migration.read_text().split("--> statement-breakpoint"):
                    if statement.strip():
                        connection.execute(statement)
                apply_data_migration(connection, migration.name)
                connection.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (migration.name,))
            connection.execute("ALTER TABLE jobs ADD COLUMN agent_session_id uuid")
            connection.commit()
        assert "0024_agent_sessions.sql" in migrate()
        with psycopg.connect(get_settings().dsn) as connection:
            fields = {
                row[0] for row in connection.execute("SELECT column_name FROM information_schema.columns WHERE table_name='jobs'")
            }
            assert {"agent_session_id", "agent_session_context"} <= fields
            index = connection.execute("SELECT to_regclass('public.jobs_agent_session_id')").fetchone()
            assert index is not None and index[0] is not None
        print("Legacy UUID schema + migration 0024: PASS")
        return 0
    finally:
        if previous is None:
            os.environ.pop("PGDATABASE", None)
        else:
            os.environ["PGDATABASE"] = previous
        if previous_home is None:
            os.environ.pop("JOBPILOT_HOME", None)
        else:
            os.environ["JOBPILOT_HOME"] = previous_home
        if previous_vault is None:
            os.environ.pop("JOBPILOT_VAULT", None)
        else:
            os.environ["JOBPILOT_VAULT"] = previous_vault
        get_settings.cache_clear()
        for name in names:
            admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s", (name,))
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
        admin.close()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

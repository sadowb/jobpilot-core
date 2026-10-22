"""Tiny forward-only SQL migrator. Applies backend/migrations/*.sql in name order, once each."""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

from .boards import resolve_board
from .config import get_settings
from .domain.canonical import pacing_hostname

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def apply_data_migration(connection: psycopg.Connection[tuple[object, ...]], name: str) -> None:
    """Python data conversions run atomically with the corresponding SQL migration."""
    if name == "0007_pacing_hostname.sql":
        rows = connection.execute("SELECT id,url FROM jobs").fetchall()
        for job_id, url in rows:
            connection.execute("UPDATE jobs SET pacing_hostname=%s WHERE id=%s", (pacing_hostname(str(url)), job_id))
        # Merge historical keys conservatively; never reset a host's reserved cooldown.
        rows = connection.execute("SELECT domain,last_hit FROM domain_pacing").fetchall()
        for domain, last_hit in rows:
            # Existing urlsplit keys can be bare IPv6 hostnames.
            authority = (
                f"[{domain}]" if str(domain).count(":") > 1 and "@" not in str(domain) and "[" not in str(domain) else str(domain)
            )
            hostname = pacing_hostname(f"https://{authority}")
            if hostname != domain:
                connection.execute(
                    """INSERT INTO domain_pacing(domain,last_hit) VALUES(%s,%s) ON CONFLICT(domain)
                       DO UPDATE SET last_hit=greatest(domain_pacing.last_hit,EXCLUDED.last_hit)""",
                    (hostname, last_hit),
                )
                connection.execute("DELETE FROM domain_pacing WHERE domain=%s", (domain,))
        connection.execute("ALTER TABLE jobs ALTER COLUMN pacing_hostname DROP DEFAULT")
    elif name == "0019_pacing_per_tenant.sql":
        rows = connection.execute("SELECT id,coalesce(apply_url,url),pacing_hostname FROM jobs").fetchall()
        for job_id, url, old_key in rows:
            _, match = resolve_board(str(url))
            key = pacing_hostname(match.canonical_url, match.tenant)
            host = pacing_hostname(match.canonical_url)
            # Retain the host row for unknown tenants. Include the old job key
            # and the destination's reservation, so reruns never move a slot earlier.
            connection.execute(
                """INSERT INTO domain_pacing(domain,last_hit)
                   SELECT %s,max(last_hit) FROM domain_pacing WHERE domain IN (%s,%s)
                   HAVING count(*) > 0
                   ON CONFLICT(domain) DO UPDATE
                   SET last_hit=greatest(domain_pacing.last_hit,EXCLUDED.last_hit)""",
                (key, host, old_key),
            )
            connection.execute("UPDATE jobs SET pacing_hostname=%s WHERE id=%s", (key, job_id))


def migrate() -> list[str]:
    s = get_settings()
    applied: list[str] = []
    with psycopg.connect(s.dsn, autocommit=False) as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {r[0] for r in c.execute("SELECT name FROM schema_migrations").fetchall()}
        legacy = c.execute("SELECT to_regclass('public.jobs') IS NOT NULL").fetchone()
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.name in done:
                continue
            if legacy and legacy[0] and not done and f.name in {"0000_dashing_adam_destine.sql", "0001_elite_absorbing_man.sql"}:
                # schema was created earlier by the previous toolchain; just record it
                c.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (f.name,))
                applied.append(f"{f.name} (recorded)")
                continue
            for stmt in f.read_text().split("--> statement-breakpoint"):
                if stmt.strip():
                    c.execute(stmt)
            apply_data_migration(c, f.name)
            c.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (f.name,))
            applied.append(f.name)
        c.commit()
    return applied


if __name__ == "__main__":
    print("\n".join(migrate()) or "up to date")
    sys.exit(0)

"""PostgreSQL operations for job-site credentials (ADR 0031). Transactions are owned by the service."""

from __future__ import annotations

from typing import Literal

from ..domain.secretbox import Sealed
from .database import Database, Row

State = Literal["pending", "active", "failed", "retired"]
Source = Literal["generated", "imported", "manual"]
SEALED_COLUMNS = "ciphertext, nonce, key_id"
META = "id, origin, username, state, source, created_at, updated_at, last_used_at, last_verified_at"


class CredentialsRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_meta(self) -> list[Row]:
        return self.db.all(f"SELECT {META} FROM site_credentials ORDER BY origin, username")

    def find_meta(self, credential_id: int) -> Row | None:
        return self.db.one(f"SELECT {META} FROM site_credentials WHERE id=%s", (credential_id,))

    def lock_by_key(self, origin: str, username: str) -> Row | None:
        return self.db.one(f"SELECT {META} FROM site_credentials WHERE origin=%s AND username=%s FOR UPDATE", (origin, username))

    def find_password(self, credential_id: int) -> Row | None:
        return self.db.one(
            f"SELECT id, origin, username, state, {SEALED_COLUMNS} FROM site_credentials WHERE id=%s", (credential_id,)
        )

    def find_usable(self, origin: str, username: str | None) -> Row | None:
        """The newest login the product may sign in with: `active`, or `pending` (created but not yet confirmed)."""
        return self.db.one(
            f"""SELECT id, origin, username, state, {SEALED_COLUMNS} FROM site_credentials
               WHERE origin=%s AND state IN ('active', 'pending') AND (%s::text IS NULL OR username=%s)
               ORDER BY (state='active') DESC, updated_at DESC LIMIT 1""",
            (origin, username, username),
        )

    def insert(self, origin: str, username: str, sealed: Sealed, state: State, source: Source) -> Row:
        row = self.db.one(
            f"""INSERT INTO site_credentials(origin, username, ciphertext, nonce, key_id, state, source)
                VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING {META}""",
            (origin, username, sealed.ciphertext, sealed.nonce, sealed.key_id, state, source),
        )
        assert row
        return row

    def replace_password(self, credential_id: int, sealed: Sealed, state: State) -> None:
        self.db.run(
            "UPDATE site_credentials SET ciphertext=%s, nonce=%s, key_id=%s, state=%s, updated_at=now() WHERE id=%s",
            (sealed.ciphertext, sealed.nonce, sealed.key_id, state, credential_id),
        )

    def set_state(self, credential_id: int, state: State, allowed_from: tuple[State, ...]) -> bool:
        return bool(
            self.db.run(
                "UPDATE site_credentials SET state=%s, updated_at=now() WHERE id=%s AND state = ANY(%s)",
                (state, credential_id, list(allowed_from)),
            )
        )

    def touch(self, credential_id: int, *, verified: bool) -> None:
        self.db.run(
            "UPDATE site_credentials SET last_used_at=now(), last_verified_at=CASE WHEN %s THEN now() ELSE last_verified_at END WHERE id=%s",
            (verified, credential_id),
        )

    def list_export(self) -> list[Row]:
        return self.db.all(
            f"SELECT origin, username, state, {SEALED_COLUMNS} FROM site_credentials WHERE state IN ('active', 'pending') ORDER BY origin, username"
        )

    def list_job_urls(self) -> list[Row]:
        return self.db.all("SELECT url, apply_url FROM jobs WHERE archived_at IS NULL")


def sealed_of(row: Row) -> Sealed:
    return Sealed(int(row["key_id"]), bytes(row["nonce"]), bytes(row["ciphertext"]))

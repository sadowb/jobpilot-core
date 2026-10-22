"""Job-site logins stored in PostgreSQL (ADR 0031).

Passwords are stored as AES-256-GCM ciphertext bound to their row (origin, username); the key is a 0600 file outside the
database and backups. Plaintext leaves this module only through `password_for_signin` (server-side injection into a page, never into a model prompt, event, log or job
result) and `reveal` / `export_csv` (owner dashboard session, audited by an event that never carries the value).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

from ..boards import resolve_board
from ..config.vault_key import vault_box
from ..domain.credentials import (
    DEFAULT_SYMBOLS,
    generate_password,
    normalize_origin,
    parse_password_csv,
    to_password_csv,
)
from ..domain.secretbox import Sealed, SecretBox
from ..repository.credentials import CredentialsRepository, sealed_of

if TYPE_CHECKING:
    from .application import Services

log = logging.getLogger("jobpilot.credentials")

ImportScope = Literal["ats", "all"]
PREVIEW_ITEMS = 200


class CredentialService:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.repository = CredentialsRepository(svc.db)
        self._box: SecretBox | None = None

    @property
    def box(self) -> SecretBox:
        """Loaded on first use so reading metadata never touches the key file."""
        if self._box is None:
            self._box = vault_box(self.svc.cfg.data_dir)
        return self._box

    def _seal(self, origin: str, username: str, password: str) -> Sealed:
        return self.box.seal(password, f"{origin}|{username}")

    def _open(self, row: Any) -> str:
        return self.box.open(sealed_of(row), f"{row['origin']}|{row['username']}")

    # --- metadata only -------------------------------------------------------------------------------------------

    def list_all(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.repository.list_meta()]

    # --- creation: write-ahead ------------------------------------------------------------------------------------

    def reserve(self, origin: str, username: str, *, symbols: str = DEFAULT_SYMBOLS) -> dict[str, Any]:
        """Generate a password and store it as `pending` BEFORE it is submitted to the site, so no account can exist
        without its password on record. A known `active` login is returned unchanged (never overwritten); a
        `pending`/`failed`/`retired` one gets a fresh password (the retry after a site rejects the first)."""
        key = normalize_origin(origin)
        name = username.strip()
        if not name:
            raise ValueError("a username is required")
        with self.svc.db.transaction():
            row = self.repository.lock_by_key(key, name)
            if row is None:
                return dict(
                    self.repository.insert(
                        key, name, self._seal(key, name, generate_password(symbols=symbols)), "pending", "generated"
                    )
                )
            if row["state"] != "active":
                self.repository.replace_password(
                    int(row["id"]), self._seal(key, name, generate_password(symbols=symbols)), "pending"
                )
            found = self.repository.find_meta(int(row["id"]))
            assert found
            return dict(found)

    def add_manual(self, origin: str, username: str, password: str) -> dict[str, Any]:
        """A login the owner already has. An existing row for the same origin and username is replaced and re-activated."""
        key, name = normalize_origin(origin), username.strip()
        if not name or not password or len(password) > 256:
            raise ValueError("username and password are required")
        with self.svc.db.transaction():
            row = self.repository.lock_by_key(key, name)
            if row is None:
                return dict(self.repository.insert(key, name, self._seal(key, name, password), "active", "manual"))
            self.repository.replace_password(int(row["id"]), self._seal(key, name, password), "active")
            found = self.repository.find_meta(int(row["id"]))
            assert found
            return dict(found)

    def activate(self, credential_id: int) -> None:
        """The site accepted the account (verification page, or a sign-in worked)."""
        with self.svc.db.transaction():
            if not self.repository.set_state(credential_id, "active", ("pending", "failed")):
                self._require(credential_id)
            self.repository.touch(credential_id, verified=True)

    def fail(self, credential_id: int) -> None:
        """The site rejected the account or its password. Only a pending row can fail."""
        with self.svc.db.transaction():
            if not self.repository.set_state(credential_id, "failed", ("pending",)):
                self._require(credential_id)

    def retire(self, credential_id: int) -> None:
        """Soft removal: the row and password stay for the owner, but the product stops using it."""
        with self.svc.db.transaction():
            if not self.repository.set_state(credential_id, "retired", ("pending", "active", "failed")):
                self._require(credential_id)

    def _require(self, credential_id: int) -> None:
        if self.repository.find_meta(credential_id) is None:
            raise LookupError("unknown credential")

    def stored_hosts(self) -> list[str]:
        """Hosts that have a usable (pending or active) login. Metadata only: lets the agent recognise a sign-in page that
        the server can handle, without ever receiving a credential."""
        hosts = {
            urlsplit(str(r["origin"])).hostname or "" for r in self.repository.list_meta() if r["state"] in ("pending", "active")
        }
        return sorted(h for h in hosts if h)[:50]

    def has_login(self, origin: str) -> bool:
        """True when a usable (pending or active) login exists for `origin`. Metadata only: nothing is decrypted."""
        return self.repository.find_usable(normalize_origin(origin), None) is not None

    # --- server-side use -------------------------------------------------------------------------------------------

    def password_for_signin(self, origin: str, username: str | None = None) -> tuple[dict[str, Any], str] | None:
        """(metadata, password) for the newest usable login at `origin`, or None. Server code only: the caller types it
        into the page and must not pass it to a model, an event, a log line or a job result."""
        with self.svc.db.transaction():
            row = self.repository.find_usable(normalize_origin(origin), username.strip() if username else None)
            if row is None:
                return None
            self.repository.touch(int(row["id"]), verified=False)
            return {"id": row["id"], "origin": row["origin"], "username": row["username"], "state": row["state"]}, self._open(row)

    # --- owner dashboard -------------------------------------------------------------------------------------------

    def reveal(self, credential_id: int) -> str:
        row = self.repository.find_password(credential_id)
        if row is None:
            raise LookupError("unknown credential")
        self.svc.event(None, "credential_revealed", "user", {"id": credential_id, "origin": row["origin"]})
        return self._open(row)

    def export_csv(self) -> str:
        rows = self.repository.list_export()
        self.svc.event(None, "credential_exported", "user", {"count": len(rows)})
        return to_password_csv([(r["origin"], r["username"], self._open(r), f"JobPilot ({r['state']})") for r in rows])

    def import_csv(self, text: str, *, scope: ImportScope = "ats", commit: bool = False) -> dict[str, Any]:
        """Import a password-manager export. `commit=False` previews: nothing is written and no password is returned.
        The file is parsed from memory and never stored. Existing logins are skipped, never overwritten."""
        parsed, invalid = parse_password_csv(text)
        job_hosts = self._job_hosts() if scope == "ats" else set()
        wanted: dict[tuple[str, str], str] = {}
        out_of_scope = 0
        for item in parsed:
            if scope == "ats" and not self._in_ats_scope(item.origin, job_hosts):
                out_of_scope += 1
                continue
            wanted.setdefault((item.origin, item.username), item.password)
        imported = existing = 0
        with self.svc.db.transaction():
            for (origin, username), password in wanted.items():
                if self.repository.lock_by_key(origin, username) is not None:
                    existing += 1
                elif commit:
                    self.repository.insert(origin, username, self._seal(origin, username, password), "active", "imported")
                    imported += 1
        if commit:
            self.svc.event(None, "credential_imported", "user", {"imported": imported, "skippedExisting": existing})
        return {
            "committed": commit,
            "found": len(parsed),
            "toImport": len(wanted) - existing,
            "imported": imported,
            "skippedExisting": existing,
            "skippedOutOfScope": out_of_scope,
            "skippedInvalid": invalid,
            "items": [{"origin": o, "username": u} for (o, u) in list(wanted)[:PREVIEW_ITEMS]],
        }

    # --- helpers ---------------------------------------------------------------------------------------------------

    def _job_hosts(self) -> set[str]:
        hosts: set[str] = set()
        for row in self.repository.list_job_urls():
            for value in (row["url"], row["apply_url"]):
                host = urlsplit(str(value or "")).hostname
                if host:
                    hosts.add(host.lower())
        return hosts

    @staticmethod
    def _in_ats_scope(origin: str, job_hosts: set[str]) -> bool:
        host = (urlsplit(origin).hostname or "").lower()
        return host in job_hosts or resolve_board(origin)[0].id != "custom"

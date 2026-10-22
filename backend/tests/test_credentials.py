"""Credential store (ADR 0031): pure rules, the service over an in-memory repository, and the HTTP auth boundary."""

from __future__ import annotations

import csv
import io
import json
import os
import stat
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from jobpilot.config.vault_key import KEY_FILE, load_vault_key
from jobpilot.domain.credentials import (
    DEFAULT_SYMBOLS,
    MAX_CSV_ROWS,
    generate_password,
    normalize_origin,
    parse_password_csv,
    to_password_csv,
)
from jobpilot.domain.secretbox import Sealed, SecretBox
from jobpilot.service.credentials import CredentialService


class OriginTests(unittest.TestCase):
    def test_normalises_case_default_ports_userinfo_and_bare_hosts(self) -> None:
        cases = {
            "https://NVIDIA.wd5.MyWorkdayJobs.com/en-US/site/job/x?y=1#z": "https://nvidia.wd5.myworkdayjobs.com",
            "nvidia.wd5.myworkdayjobs.com": "https://nvidia.wd5.myworkdayjobs.com",
            "http://example.com:80/a": "http://example.com",
            "https://example.com:443": "https://example.com",
            "https://example.com:8443/x": "https://example.com:8443",
            "https://user:secret@example.com/path": "https://example.com",
            "https://example.com./": "https://example.com",
            "http://[::1]:4777/x": "http://[::1]:4777",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_origin(raw), expected, raw)

    def test_refuses_non_http_and_empty(self) -> None:
        for raw in (
            "",
            "   ",
            "ftp://example.com",
            "javascript:alert(1)",
            "file:///etc/passwd",
            "https://",
            "https://host:99999",
        ):
            with self.assertRaises(ValueError, msg=raw):
                normalize_origin(raw)


class PasswordTests(unittest.TestCase):
    def test_every_class_is_present_and_length_is_exact(self) -> None:
        for _ in range(200):
            p = generate_password(20)
            self.assertEqual(len(p), 20)
            self.assertTrue(any(c.isupper() for c in p))
            self.assertTrue(any(c.islower() for c in p))
            self.assertTrue(any(c.isdigit() for c in p))
            self.assertTrue(any(c in DEFAULT_SYMBOLS for c in p))

    def test_retry_symbol_set_is_respected_and_values_differ(self) -> None:
        seen = {generate_password(20, "-_.") for _ in range(50)}
        self.assertEqual(len(seen), 50)
        for p in seen:
            self.assertFalse(set(p) & set("!@#$%^&*"))

    def test_no_symbols_allowed_and_short_length_refused(self) -> None:
        p = generate_password(16, "")
        self.assertTrue(p.isalnum())
        with self.assertRaises(ValueError):
            generate_password(11)


class CsvTests(unittest.TestCase):
    GOOGLE = (
        "name,url,username,password,note\n"
        "greenhouse.io,https://boards.greenhouse.io/login,me@example.com,p@ss,\n"
        'weird,https://example.com/login,"a,b@example.com","pw ""quoted""",note\n'
    )

    def test_parses_google_format_with_bom_and_quotes(self) -> None:
        rows, skipped = parse_password_csv("﻿" + self.GOOGLE)
        self.assertEqual(skipped, 0)
        self.assertEqual(
            [(r.origin, r.username, r.password) for r in rows],
            [
                ("https://boards.greenhouse.io", "me@example.com", "p@ss"),
                ("https://example.com", "a,b@example.com", 'pw "quoted"'),
            ],
        )

    def test_other_managers_column_names_work(self) -> None:
        rows, _ = parse_password_csv("login_uri,login_username,login_password\nhttps://x.example,u,pw\n")
        self.assertEqual((rows[0].origin, rows[0].username, rows[0].password), ("https://x.example", "u", "pw"))

    def test_bad_rows_are_skipped_not_repaired(self) -> None:
        text = "url,username,password\nandroid://app,u,p\nhttps://ok.example,,p\nhttps://ok.example,u,\nhttps://ok.example,u,p\nshort\n"
        rows, skipped = parse_password_csv(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(skipped, 4)

    def test_missing_columns_empty_and_oversized_files_are_refused(self) -> None:
        for text in ("", "name,url\nx,y\n"):
            with self.assertRaises(ValueError):
                parse_password_csv(text)
        big = "url,username,password\n" + "https://a.example,u,p\n" * (MAX_CSV_ROWS + 1)
        with self.assertRaises(ValueError):
            parse_password_csv(big)

    def test_export_round_trips_through_the_parser(self) -> None:
        text = to_password_csv([("https://a.example", "u@x.io", 'pw,"1"', "JobPilot (active)")])
        self.assertEqual(next(csv.reader(io.StringIO(text))), ["name", "url", "username", "password", "note"])
        rows, skipped = parse_password_csv(text)
        self.assertEqual(
            (skipped, rows[0].origin, rows[0].username, rows[0].password), (0, "https://a.example", "u@x.io", 'pw,"1"')
        )


class FakeRepository:
    """In-memory stand-in with the same contract as CredentialsRepository."""

    def __init__(self, job_urls: list[dict[str, str | None]] | None = None) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self.next_id = 1
        self.job_urls = job_urls or []

    def _meta(self, row: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in row.items() if k not in ("ciphertext", "nonce", "key_id")}

    def list_meta(self) -> list[dict[str, Any]]:
        return [self._meta(r) for r in sorted(self.rows.values(), key=lambda r: (r["origin"], r["username"]))]

    def find_meta(self, i: int) -> dict[str, Any] | None:
        return self._meta(self.rows[i]) if i in self.rows else None

    def lock_by_key(self, origin: str, username: str) -> dict[str, Any] | None:
        row = next((r for r in self.rows.values() if (r["origin"], r["username"]) == (origin, username)), None)
        return self._meta(row) if row else None

    def find_password(self, i: int) -> dict[str, Any] | None:
        return dict(self.rows[i]) if i in self.rows else None

    def find_usable(self, origin: str, username: str | None) -> dict[str, Any] | None:
        pool = [r for r in self.rows.values() if r["origin"] == origin and r["state"] in ("active", "pending")]
        if username:
            pool = [r for r in pool if r["username"] == username]
        pool.sort(key=lambda r: r["state"] != "active")
        return dict(pool[0]) if pool else None

    def insert(self, origin: str, username: str, sealed: Sealed, state: str, source: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        row = {
            "id": self.next_id, "origin": origin, "username": username, "ciphertext": sealed.ciphertext, "nonce": sealed.nonce,
            "key_id": sealed.key_id, "state": state,
            "source": source, "created_at": now, "updated_at": now, "last_used_at": None, "last_verified_at": None,
        }  # fmt: skip
        self.rows[self.next_id] = row
        self.next_id += 1
        return self._meta(row)

    def replace_password(self, i: int, sealed: Sealed, state: str) -> None:
        self.rows[i].update(ciphertext=sealed.ciphertext, nonce=sealed.nonce, key_id=sealed.key_id, state=state)

    def set_state(self, i: int, state: str, allowed_from: tuple[str, ...]) -> bool:
        if i in self.rows and self.rows[i]["state"] in allowed_from:
            self.rows[i]["state"] = state
            return True
        return False

    def touch(self, i: int, *, verified: bool) -> None:
        self.rows[i]["last_used_at"] = datetime.now(UTC)
        if verified:
            self.rows[i]["last_verified_at"] = datetime.now(UTC)

    def list_export(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows.values() if r["state"] in ("active", "pending")]

    def list_job_urls(self) -> list[dict[str, str | None]]:
        return self.job_urls


def make(job_urls: list[dict[str, str | None]] | None = None) -> tuple[CredentialService, FakeRepository, MagicMock]:
    svc = MagicMock()
    service = CredentialService(svc)
    repo = FakeRepository(job_urls)
    service.repository = repo  # type: ignore[assignment]
    service._box = SecretBox({1: os.urandom(32)}, 1)
    return service, repo, svc


class ServiceTests(unittest.TestCase):
    def test_reserve_is_write_ahead_pending_and_never_returns_the_password(self) -> None:
        service, repo, _ = make()
        meta = service.reserve("https://Acme.wd5.myworkdayjobs.com/en-US/x", " me@example.com ")
        self.assertEqual(
            (meta["origin"], meta["username"], meta["state"], meta["source"]),
            ("https://acme.wd5.myworkdayjobs.com", "me@example.com", "pending", "generated"),
        )
        self.assertNotIn("password", meta)
        self.assertEqual(len(service._open(repo.rows[meta["id"]])), 20)
        self.assertNotIn(b"pending", repo.rows[meta["id"]]["ciphertext"])

    def test_retry_regenerates_a_pending_or_failed_password_but_never_an_active_one(self) -> None:
        service, repo, _ = make()
        first = service.reserve("https://a.example", "u")
        before = repo.rows[first["id"]]["ciphertext"]
        again = service.reserve("https://a.example", "u", symbols="-_.")
        self.assertEqual(again["id"], first["id"])
        self.assertNotEqual(repo.rows[first["id"]]["ciphertext"], before)
        service.activate(first["id"])
        kept = repo.rows[first["id"]]["ciphertext"]
        self.assertEqual(service.reserve("https://a.example", "u")["state"], "active")
        self.assertEqual(repo.rows[first["id"]]["ciphertext"], kept)

    def test_state_machine(self) -> None:
        service, repo, _ = make()
        i = service.reserve("https://a.example", "u")["id"]
        service.fail(i)
        self.assertEqual(repo.rows[i]["state"], "failed")
        service.reserve("https://a.example", "u")  # retry after a rejection
        self.assertEqual(repo.rows[i]["state"], "pending")
        service.activate(i)
        self.assertEqual(repo.rows[i]["last_verified_at"] is not None, True)
        service.fail(i)  # an active login cannot fail
        self.assertEqual(repo.rows[i]["state"], "active")
        service.retire(i)
        self.assertEqual(repo.rows[i]["state"], "retired")
        with self.assertRaises(LookupError):
            service.activate(999)

    def test_reserve_validates_inputs(self) -> None:
        service, _, _ = make()
        with self.assertRaises(ValueError):
            service.reserve("https://a.example", "   ")
        with self.assertRaises(ValueError):
            service.reserve("ftp://a.example", "u")

    def test_signin_prefers_active_ignores_retired_and_touches_last_used(self) -> None:
        service, repo, _ = make()
        service.add_manual("https://a.example", "old@x.io", "old-pw")
        pending = service.reserve("https://a.example", "new@x.io")
        found = service.password_for_signin("https://a.example/login")
        assert found is not None
        self.assertEqual((found[0]["username"], found[1]), ("old@x.io", "old-pw"))
        self.assertIsNotNone(repo.rows[found[0]["id"]]["last_used_at"])
        by_name = service.password_for_signin("https://a.example", "new@x.io")
        assert by_name is not None
        self.assertEqual(by_name[0]["id"], pending["id"])
        service.retire(pending["id"])
        self.assertIsNone(service.password_for_signin("https://a.example", "new@x.io"))
        self.assertIsNone(service.password_for_signin("https://other.example"))

    def test_manual_add_replaces_and_reactivates(self) -> None:
        service, repo, _ = make()
        a = service.add_manual("https://a.example", "u", "one")
        b = service.add_manual("https://a.example", "u", "two")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual((service._open(repo.rows[a["id"]]), repo.rows[a["id"]]["state"]), ("two", "active"))
        with self.assertRaises(ValueError):
            service.add_manual("https://a.example", "u", "")

    def test_reveal_and_export_are_audited_without_the_value(self) -> None:
        service, _, svc = make()
        i = service.add_manual("https://a.example", "u", "s3cret-value")["id"]
        self.assertEqual(service.reveal(i), "s3cret-value")
        text = service.export_csv()
        self.assertIn("s3cret-value", text)
        kinds = [c.args[1] for c in svc.event.call_args_list]
        self.assertEqual(kinds, ["credential_revealed", "credential_exported"])
        for call in svc.event.call_args_list:
            self.assertNotIn("s3cret-value", json.dumps(call.args[3], default=str))
        with self.assertRaises(LookupError):
            service.reveal(404)

    def test_listing_never_contains_passwords(self) -> None:
        service, _, _ = make()
        service.add_manual("https://a.example", "u", "s3cret-value")
        service.reserve("https://b.example", "v")
        self.assertNotIn("password", json.dumps(service.list_all(), default=str))


class SecretBoxTests(unittest.TestCase):
    def box(self) -> SecretBox:
        return SecretBox({1: os.urandom(32)}, 1)

    def test_round_trip_with_fresh_nonce_per_seal(self) -> None:
        box = self.box()
        a, b = box.seal("pw-1", "o|u"), box.seal("pw-1", "o|u")
        self.assertNotEqual((a.nonce, a.ciphertext), (b.nonce, b.ciphertext))
        self.assertEqual((box.open(a, "o|u"), a.key_id, len(a.nonce)), ("pw-1", 1, 12))
        self.assertNotIn(b"pw-1", a.ciphertext)

    def test_ciphertext_copied_to_another_row_does_not_open(self) -> None:
        box = self.box()
        sealed = box.seal("pw", "https://a.example|u")
        for aad in ("https://b.example|u", "https://a.example|v"):
            with self.assertRaises(ValueError):
                box.open(sealed, aad)

    def test_tampering_wrong_key_unknown_key_id_and_bad_nonce_are_refused(self) -> None:
        box = self.box()
        sealed = box.seal("pw", "o|u")
        flipped = Sealed(1, sealed.nonce, bytes([sealed.ciphertext[0] ^ 1]) + sealed.ciphertext[1:])
        for bad in (flipped, Sealed(2, sealed.nonce, sealed.ciphertext), Sealed(1, sealed.nonce[:-1], sealed.ciphertext)):
            with self.assertRaises(ValueError):
                box.open(bad, "o|u")
        with self.assertRaises(ValueError):
            self.box().open(sealed, "o|u")

    def test_rotation_keeps_old_rows_readable(self) -> None:
        old, new = os.urandom(32), os.urandom(32)
        sealed = SecretBox({1: old}, 1).seal("pw", "o|u")
        rotated = SecretBox({1: old, 2: new}, 2)
        self.assertEqual(rotated.open(sealed, "o|u"), "pw")
        self.assertEqual(rotated.seal("x", "o|u").key_id, 2)

    def test_bad_key_material_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            SecretBox({1: b"short"}, 1)
        with self.assertRaises(ValueError):
            SecretBox({1: os.urandom(32)}, 2)


class VaultKeyTests(unittest.TestCase):
    def test_created_private_and_stable(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            key = load_vault_key(Path(d) / "data")
            path = Path(d) / "data" / KEY_FILE
            self.assertEqual((len(key), stat.S_IMODE(path.stat().st_mode)), (32, 0o600))
            self.assertEqual(load_vault_key(Path(d) / "data"), key)
            self.assertEqual([p.name for p in path.parent.iterdir()], [KEY_FILE])

    def test_concurrent_first_use_agrees_on_one_key(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            keys: list[bytes] = []
            threads = [threading.Thread(target=lambda: keys.append(load_vault_key(Path(d)))) for _ in range(12)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual((len(keys), len(set(keys))), (12, 1))
            self.assertEqual([p.name for p in Path(d).iterdir()], [KEY_FILE])

    def test_loose_permissions_are_tightened_and_corrupt_or_short_keys_refused(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            key = load_vault_key(Path(d))
            path = Path(d) / KEY_FILE
            path.chmod(0o644)
            self.assertEqual(load_vault_key(Path(d)), key)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            for content in ("not-hex!", "abcd"):
                path.write_text(content)
                with self.assertRaises(RuntimeError):
                    load_vault_key(Path(d))

    def test_symlinked_key_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "elsewhere"
            target.write_text("00" * 32)
            (Path(d) / KEY_FILE).symlink_to(target)
            with self.assertRaises(OSError):
                load_vault_key(Path(d))


class ImportTests(unittest.TestCase):
    CSV = (
        "name,url,username,password\n"
        "gh,https://boards.greenhouse.io/x,me@x.io,pw1\n"
        "bank,https://bank.example/login,me@x.io,pw2\n"
        "own,https://careers.acme.example/jobs,me@x.io,pw3\n"
        "dup,https://boards.greenhouse.io/y,me@x.io,pw4\n"
        "bad,notaurl,me@x.io,pw5\n"
    )

    def jobs(self) -> list[dict[str, str | None]]:
        return [{"url": "https://careers.acme.example/jobs/1", "apply_url": None}]

    def test_preview_writes_nothing_and_returns_no_passwords(self) -> None:
        service, repo, svc = make(self.jobs())
        result = service.import_csv(self.CSV)
        self.assertEqual(repo.rows, {})
        self.assertFalse(result["committed"])
        self.assertEqual((result["toImport"], result["skippedOutOfScope"], result["skippedInvalid"]), (2, 1, 1))
        self.assertNotIn("pw1", json.dumps(result))
        svc.event.assert_not_called()

    def test_commit_imports_in_scope_rows_active_and_is_idempotent(self) -> None:
        service, repo, svc = make(self.jobs())
        result = service.import_csv(self.CSV, commit=True)
        self.assertEqual(result["imported"], 2)
        self.assertEqual(
            sorted(r["origin"] for r in repo.rows.values()), ["https://boards.greenhouse.io", "https://careers.acme.example"]
        )
        self.assertTrue(all(r["state"] == "active" and r["source"] == "imported" for r in repo.rows.values()))
        svc.event.assert_called_once()
        again = service.import_csv(self.CSV, commit=True)
        self.assertEqual((again["imported"], again["skippedExisting"]), (0, 2))

    def test_existing_login_is_never_overwritten_by_an_import(self) -> None:
        service, repo, _ = make(self.jobs())
        service.add_manual("https://boards.greenhouse.io", "me@x.io", "mine")
        service.import_csv(self.CSV, commit=True)
        self.assertEqual(service._open(next(r for r in repo.rows.values() if r["origin"].endswith("greenhouse.io"))), "mine")

    def test_scope_all_takes_every_valid_row(self) -> None:
        service, _, _ = make()
        self.assertEqual(service.import_csv(self.CSV, scope="all", commit=True)["imported"], 3)


class HttpBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """Bearer tokens may list metadata but never reveal, export, import, add or retire."""

    async def asyncSetUp(self) -> None:
        from test_api import ApiTests

        ApiTests.setUpClass()
        self.api = ApiTests()
        self.addCleanup(ApiTests.tearDownClass)

    async def test_bearer_cannot_reach_plaintext_or_mutations(self) -> None:
        token = {"authorization": "Bearer " + self.api.token}
        for method, path, body in (
            ("POST", "/api/v1/credentials/1/reveal", None),
            ("POST", "/api/v1/credentials/export", None),
            ("POST", "/api/v1/credentials/import", {"csv": "x"}),
            ("POST", "/api/v1/credentials", {"origin": "https://a.example", "username": "u", "password": "p"}),
            ("DELETE", "/api/v1/credentials/1", None),
        ):
            status, _, _ = await self.api.request(path, method=method, headers=token, body=body)
            self.assertEqual(status, 403, path)

    async def test_unauthenticated_is_401_and_listing_is_metadata_only(self) -> None:
        self.assertEqual((await self.api.request("/api/v1/credentials"))[0], 401)
        meta = {
            "id": 1, "origin": "https://a.example", "username": "u", "state": "active", "source": "manual",
            "created_at": datetime.now(UTC), "updated_at": datetime.now(UTC), "last_used_at": None, "last_verified_at": None,
        }  # fmt: skip
        with patch("jobpilot.controller.credentials.application_service.credentials.list_all", return_value=[meta]):
            status, _, raw = await self.api.request("/api/v1/credentials", headers={"authorization": "Bearer " + self.api.token})
        self.assertEqual(status, 200)
        self.assertNotIn(b"password", raw)


if __name__ == "__main__":
    unittest.main()

"""Pure rules for job-site credentials (ADR 0031): origin normalisation, password generation, password-manager CSV."""

from __future__ import annotations

import csv
import io
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit

UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
LOWER = "abcdefghijkmnopqrstuvwxyz"
DIGITS = "23456789"
DEFAULT_SYMBOLS = "!@#$%^&*-_"
RETRY_SYMBOLS = "-_."  # the one fallback when a site rejects the default set
MIN_LENGTH = 12
MAX_CSV_ROWS = 5000
MAX_FIELD = 2048
EXPORT_HEADER = ("name", "url", "username", "password", "note")  # Chrome / Google Password Manager column order

_URL_COLUMNS = ("url", "login_uri", "login uri", "website", "origin", "site")
_USER_COLUMNS = ("username", "login_username", "login username", "user", "login", "email")
_PASS_COLUMNS = ("password", "login_password", "login password")


def normalize_origin(raw: str) -> str:
    """`scheme://host[:port]`, lower-case, default ports and userinfo dropped. A bare host means https."""
    text = raw.strip()
    if not text or len(text) > MAX_FIELD:
        raise ValueError("not an http(s) origin")
    parts = urlsplit(text if "://" in text else f"https://{text}")
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("http", "https") or not host:
        raise ValueError("not an http(s) origin")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("not an http(s) origin") from None
    if "://" not in text and "." not in host and ":" not in host and host != "localhost":
        raise ValueError("not an http(s) origin")  # a bare word is not a host; a scheme makes the intent explicit
    shown = f"[{host}]" if ":" in host else host
    suffix = f":{port}" if port and port != (443 if parts.scheme == "https" else 80) else ""
    return f"{parts.scheme}://{shown}{suffix}"


def generate_password(length: int = 20, symbols: str = DEFAULT_SYMBOLS) -> str:
    """CSPRNG password with at least one upper, lower, digit and (when allowed) symbol; ambiguous glyphs are left out."""
    if length < MIN_LENGTH:
        raise ValueError(f"password length must be at least {MIN_LENGTH}")
    classes = [UPPER, LOWER, DIGITS] + ([symbols] if symbols else [])
    chars = [secrets.choice(c) for c in classes]
    pool = "".join(classes)
    chars += [secrets.choice(pool) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


@dataclass(frozen=True, slots=True)
class CsvCredential:
    origin: str
    username: str
    password: str


def _column(header: list[str], names: tuple[str, ...]) -> int | None:
    lowered = [h.strip().lower() for h in header]
    for name in names:
        if name in lowered:
            return lowered.index(name)
    return None


def parse_password_csv(text: str) -> tuple[list[CsvCredential], int]:
    """Rows of a password-manager export (Google Password Manager, Chrome, Bitwarden, 1Password style) as
    (credentials, skipped_count). Rows without a usable http(s) URL, username or password are skipped, never repaired."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("the file is empty") from None
    url_i, user_i, pass_i = (_column(header, c) for c in (_URL_COLUMNS, _USER_COLUMNS, _PASS_COLUMNS))
    if url_i is None or user_i is None or pass_i is None:
        raise ValueError("expected columns: url, username, password")
    found: list[CsvCredential] = []
    skipped = 0
    for n, row in enumerate(reader, start=1):
        if n > MAX_CSV_ROWS:
            raise ValueError(f"more than {MAX_CSV_ROWS} rows")
        if not row or all(not cell.strip() for cell in row):
            continue
        try:
            url, user, secret = row[url_i], row[user_i].strip(), row[pass_i]
            if not user or not secret or len(user) > 320 or len(secret) > 256:
                raise ValueError
            found.append(CsvCredential(normalize_origin(url), user, secret))
        except (IndexError, ValueError):
            skipped += 1
    return found, skipped


def to_password_csv(rows: list[tuple[str, str, str, str]]) -> str:
    """(origin, username, password, note) rows in the column order Chrome and Google Password Manager import."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(EXPORT_HEADER)
    for origin, username, password, note in rows:
        writer.writerow([urlsplit(origin).hostname or origin, origin, username, password, note])
    return out.getvalue()

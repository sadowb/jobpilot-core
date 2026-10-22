from __future__ import annotations

import hashlib
from dataclasses import dataclass

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"


def _base32(data: bytes) -> str:
    bits = 0
    value = 0
    out = []
    for byte in data:
        value = (value << 8) | byte
        bits += 8
        while bits >= 5:
            out.append(_ALPHABET[(value >> (bits - 5)) & 31])
            bits -= 5
    if bits > 0:
        out.append(_ALPHABET[(value << (5 - bits)) & 31])
    return "".join(out)


@dataclass(frozen=True)
class JobIdentity:
    canonical_url: str
    board: str | None = None
    tenant: str | None = None
    requisition_id: str | None = None


def identity_key(i: JobIdentity) -> str:
    if i.board and i.tenant and i.requisition_id:
        return f"{i.board.lower()}|{i.tenant.lower()}|{i.requisition_id}"
    return i.canonical_url


def job_id(i: JobIdentity) -> str:
    """Deterministic: same posting => same id. Minted only here, never by an agent."""
    digest = hashlib.sha256(identity_key(i).encode()).digest()
    return "j_" + _base32(digest[:5])


def url_hash(canonical_url: str) -> str:
    return hashlib.sha256(canonical_url.encode()).hexdigest()


def agent_session_id() -> str:
    """Claude Code requires a UUID4; the server mints it once per conversation."""
    from uuid import uuid4

    return str(uuid4())

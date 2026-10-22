"""Judge a read-only snapshot of a filled application page. Pure rules, no IO.

The agent's `filled` report is its own word. The server reads the parked tab once (NeoBrowser.form_audit) and refuses to
park a form that visibly still has empty required fields, validation errors or an email that is not the owner's."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

MAX_PROBLEMS = 12
MAX_TEXT = 140


def _clip(value: object) -> str:
    return " ".join(str(value).split())[:MAX_TEXT]


def problems(audit: Mapping[str, Any], owner_email: str | None) -> list[str]:
    """Owner-readable problems, most useful first; empty means nothing visible is wrong."""
    found: list[str] = []
    expected = (owner_email or "").strip().lower()
    emails = [str(e).strip() for e in audit.get("emails") or [] if str(e).strip()]
    if expected:
        for email in dict.fromkeys(emails):
            if email.lower() != expected:
                found.append(f"an email field shows {_clip(email)}, not your email")
    for label in audit.get("empty") or []:
        found.append(f"required field still empty: {_clip(label) or 'unlabelled field'}")
    for text in audit.get("errors") or []:
        found.append(f"the page shows an error: {_clip(text)}")
    return list(dict.fromkeys(found))[:MAX_PROBLEMS]

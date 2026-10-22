"""Recognise an emailed-code challenge on a parked application tab. Pure rules, no IO.

A site shows one after the owner clicked Submit ("Enter the security code we emailed you"). Only a short phrase is ever
returned; the page text is untrusted and is not stored."""

from __future__ import annotations

import re

_PHRASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("security code", re.compile(r"\bsecurity code\b")),
    ("verification code", re.compile(r"\bverification code\b")),
    ("confirmation code", re.compile(r"\bconfirmation code\b")),
    (
        "code we sent",
        re.compile(
            r"\b(?:code|link) (?:we|was|has been) (?:just )?(?:sent|emailed)\b|\bwe (?:just )?(?:sent|emailed) (?:you )?(?:a|an|the) (?:\d+[- ]?digit |six[- ]digit |8[- ]character )?code\b"
        ),
    ),
    (
        "check your email",
        re.compile(r"\bcheck your (?:e-?mail|inbox)\b.{0,80}\bcode\b|\bcode\b.{0,80}\bcheck your (?:e-?mail|inbox)\b"),
    ),
)
MAX_SCAN = 40_000


def code_challenge(text: str) -> str | None:
    """Short label of the code prompt on this page, or None. Needs the word 'code' next to an email cue, so a plain
    contact form or a marketing line does not match."""
    scan = " ".join(text[:MAX_SCAN].split()).lower()
    for label, pattern in _PHRASES:
        if pattern.search(scan):
            return label
    return None

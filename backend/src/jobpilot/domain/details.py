"""Which job fields count as "not captured", and how freshly read details merge into a job (pure, no I/O).

The rule is deliberately one-directional: a refresh only fills what is missing. It replaces a placeholder
(`(role not captured yet)`, a company that is really the ATS host) or an empty value, and never a real value or any field
the owner edited by hand."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

ROLE_PLACEHOLDER = "(role not captured yet)"
FIELDS = ("role", "company", "location", "work_mode", "pay", "job_level", "employment_type")

# Applicant-tracking hosts that show up as a "company" when the real employer was never read (suffix match).
ATS_HOST_SUFFIXES = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "ats.rippling.com", "workable.com", "smartrecruiters.com",
    "myworkdayjobs.com", "workday.com", "icims.com", "jobvite.com", "bamboohr.com", "recruitee.com", "teamtailor.com",
    "breezy.hr", "personio.com", "personio.de", "join.com", "taleo.net", "successfactors.com", "oraclecloud.com",
    "applytojob.com", "careerplug.com",
)  # fmt: skip
# Vendor names a scraper may report instead of the employer (compared case-insensitively, whole name).
ATS_NAMES = frozenset(
    {"greenhouse", "lever", "ashby", "ashbyhq", "workable", "workday", "smartrecruiters", "icims", "jobvite", "bamboohr",
     "recruitee", "teamtailor", "breezy", "personio", "taleo", "successfactors"}
)  # fmt: skip
HOST_LIKE = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}", re.I)


def host_of(url: str | None) -> str:
    try:
        return (urlsplit(url or "").hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def ats_host_regex() -> str:
    """The same suffix test as `is_ats_host`, as a POSIX regex for the bulk query (parity is unit-tested)."""
    return r"(^|\.)(" + "|".join(re.escape(s) for s in ATS_HOST_SUFFIXES) + r")$"


def is_ats_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == s or host.endswith("." + s) for s in ATS_HOST_SUFFIXES)


def is_placeholder_role(role: str | None) -> bool:
    value = (role or "").strip().lower()
    return not value or value.startswith("(role not captured")


def is_placeholder_company(name: str | None, hosts: Iterable[str] = ()) -> bool:
    """Empty, an ATS vendor name, a known ATS host, or exactly the host of the job's own link. A host-like name that is
    none of those ("Booking.com") is a real employer and stays."""
    value = (name or "").strip().lower().rstrip(".")
    if not value:
        return True
    if value in ATS_NAMES:
        return True
    if HOST_LIKE.fullmatch(value):
        return is_ats_host(value) or value in {h.lower().rstrip(".") for h in hosts if h}
    return False


def _empty(value: str | None) -> bool:
    return not (value or "").strip()


def missing_fields(current: Mapping[str, str | None], hosts: Iterable[str] = (), owner_edited: Iterable[str] = ()) -> set[str]:
    """Fields a refresh may fill: placeholder or empty, and not edited by the owner."""
    held = set(owner_edited)
    hosts = tuple(hosts)
    gaps = set()
    for key in FIELDS:
        value = current.get(key)
        bad = (
            is_placeholder_role(value)
            if key == "role"
            else is_placeholder_company(value, hosts)
            if key == "company"
            else _empty(value)
        )
        if bad and key not in held:
            gaps.add(key)
    return gaps


def plan_merge(
    current: Mapping[str, str | None],
    found: Mapping[str, str | None],
    hosts: Iterable[str] = (),
    owner_edited: Iterable[str] = (),
) -> dict[str, str]:
    """The changes to write: found values for fields that are currently missing. A found value that is itself a
    placeholder (the ATS host as a company, the role placeholder) is not an improvement and is dropped."""
    hosts = tuple(hosts)
    changes: dict[str, str] = {}
    for key in missing_fields(current, hosts, owner_edited):
        value = (found.get(key) or "").strip()
        if not value:
            continue
        if key == "role" and is_placeholder_role(value):
            continue
        if key == "company" and is_placeholder_company(value, hosts):
            continue
        changes[key] = value
    return changes

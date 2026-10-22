"""Pure rules for the structured profile sections (education, experience, languages).

Sections are stored as JSON arrays under one profile key each; a few flat keys are derived from them so form
filling (which matches labels to flat keys) keeps working.
"""

from __future__ import annotations

import re
from typing import Any

from .countries import normalize_country

SECTIONS = ("education", "experience", "languages", "citizenships")
DERIVED_SOURCE = "derived"
_DATE = re.compile(r"^(\d{4}(-\d{2})?|present)?$", re.I)
_MAX_ENTRIES = 20
_FIELDS: dict[str, dict[str, int]] = {
    "education": {"school": 200, "degree": 120, "field": 160, "country": 80, "city": 80, "start": 7, "end": 7, "gpa": 20, "notes": 1000},
    "experience": {"company": 200, "title": 160, "location": 120, "country": 80, "start": 7, "end": 7, "description": 3000},
    "languages": {"language": 80, "level": 40},
    "citizenships": {"country": 80},
}  # fmt: skip
_REQUIRED = {"education": "school", "experience": "company", "languages": "language", "citizenships": "country"}
_DATED = {"start", "end"}


def clean_section(section: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and normalise one section; raises ValueError with a message the owner can act on."""
    if section not in SECTIONS:
        raise ValueError(f"unknown section {section}")
    if len(entries) > _MAX_ENTRIES:
        raise ValueError(f"at most {_MAX_ENTRIES} entries")
    spec = _FIELDS[section]
    out: list[dict[str, Any]] = []
    for index, raw in enumerate(entries, start=1):
        row: dict[str, Any] = {}
        for key, limit in spec.items():
            value = str(raw.get(key) or "").strip()
            if len(value) > limit:
                raise ValueError(f"{section} #{index}: {key} is too long (max {limit})")
            if key in _DATED and not _DATE.fullmatch(value):
                raise ValueError(f"{section} #{index}: {key} must look like 2024 or 2024-09")
            if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f‪-‮​-‏]", value):
                raise ValueError(f"{section} #{index}: {key} contains invalid characters")
            if key == "country" and value:
                canon = normalize_country(value)
                if canon is None:
                    raise ValueError(f"{section} #{index}: '{value}' is not a country I recognise; pick one from the list")
                value = canon
            row[key] = value
        if section in ("education", "experience"):
            row["current"] = bool(raw.get("current"))
            if row["current"]:
                row["end"] = ""
        if not row[_REQUIRED[section]]:
            raise ValueError(f"{section} #{index}: {_REQUIRED[section]} is required")
        out.append(row)
    return out


def _sort_key(entry: dict[str, Any]) -> tuple[int, str, str]:
    return (1 if entry.get("current") else 0, entry.get("end") or "", entry.get("start") or "")


def derive(section: str, entries: list[dict[str, Any]]) -> dict[str, str]:
    """Flat profile keys implied by a section. Empty strings mean 'clear this derived key'."""
    if section == "education":
        top = max(entries, key=_sort_key) if entries else {}
        return {
            "university": top.get("school", ""),
            "degree": top.get("degree", ""),
            "major": top.get("field", ""),
            "graduation": "Present" if top.get("current") else top.get("end", ""),
            "education_country": top.get("country", ""),
            "gpa": top.get("gpa", ""),
            "education_start_year": date_part(top.get("start", ""), "year"),
            "education_end_year": date_part(top.get("end", ""), "year"),
            "education_end_month": date_part(top.get("end", ""), "month"),
        }
    if section == "experience":
        top = max(entries, key=_sort_key) if entries else {}
        return {"current_company": top.get("company", ""), "current_title": top.get("title", "")}
    if section == "citizenships":
        # The first entry is the primary one; forms that allow a single nationality get that (or the job's country).
        return {"nationality": entries[0]["country"] if entries else ""}
    return {}


MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def date_part(value: str, part: str) -> str:
    match = re.fullmatch(r"(\d{4})(?:-(0[1-9]|1[0-2]))?", value)
    if not match:
        return ""
    if part == "year":
        return match[1]
    return MONTHS[int(match[2]) - 1] if match[2] else ""

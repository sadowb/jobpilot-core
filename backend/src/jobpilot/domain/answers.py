from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .canonical import norm
from .canonical_answers import canonical_for, choose_option
from .countries import mentions, normalize_country


@dataclass
class PlanItem:
    key: str
    value: str
    source: str


_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p), k)
    for p, k in [
        (r"^(legal |your )?first name\b|^given name\b", "first_name"),
        (r"^(legal |your )?last name\b|^surname\b|^family name\b", "last_name"),
        # the lone "Name" field (Ashby) and its spellings; anchored so "Company name", "Reference name", "Username" never match
        (r"^((your|my) )?(full |legal |full legal |legal full )?name$", "full_name"),
        (r"\be ?mail\b", "email"),
        (r"\b(mobile|phone|telephone|cell)\b(?!.*\b(country code|dial(ing)? code|extension|type)\b)", "phone"),
        (r"linkedin", "linkedin"),
        (r"github", "github"),
        (r"(portfolio|personal) (site|website|url)|^website$", "website"),
        (r"^city$|^location city$|current city|city of residence", "city"),
        (r"^country( of residence)?$", "country"),
        (
            r"(authori[sz]ed|legally|eligible).*(work|employment).*\b(us|usa|united states|u s)\b|^work authori[sz]ation( status)?$",
            "work_authorization_us",
        ),
        (r"(require|need).*(sponsorship|visa)", "requires_sponsorship"),
        (r"(school|university|college)( name)?$", "university"),
        (r"^degree( type)?$", "degree"),
        (r"field of study|area of study|^(your |college |academic |undergraduate )?majors?( and minors?)?$", "major"),
        (r"^discipline$", "major"),
        (r"^start date year$", "education_start_year"),
        (r"^end date month$", "education_end_month"),
        (r"^end date year$", "education_end_year"),
        (r"graduation (date|year)|expected graduation", "graduation"),
        (
            r"(country|location).*(education|school|university|college|study|studied)|(education|school|university|college).*country",
            "education_country",
        ),
        (r"\bgpa\b|grade point", "gpa"),
        (r"^(current|most recent|present) (company|employer)", "current_company"),
        (r"^(current|most recent|present) (job )?title|^(current|present) position", "current_title"),
        (r"preferred (first )?name|nickname", "preferred_name"),
        (r"^(state|province|state province|region)$", "state"),
        (r"(zip|postal) ?code|postcode", "postal_code"),
        (r"nationality|citizenship( country)?$|country of citizenship", "nationality"),
        (r"(expected|desired) (salary|compensation)|salary expectation", "expected_salary"),
        (
            r"^((earliest|desired|preferred|possible|available|expected|anticipated) )*(start|availability|available) date$"
            r"|date available|when (can|could) you start|earliest .*\bstart\b|available (from|to start)|notice period",
            "available_from",
        ),
    ]
]


# Questions about somebody else (a referee, a manager, an emergency contact) share words with the owner's own details.
_ABOUT_SOMEONE_ELSE = re.compile(
    r"\b(references?|referees?|referrer|referred by|emergency|manager|supervisor|recruiter|spouse|partner|parent|guardian|next of kin)\b"
)


def profile_key_for(label: str) -> str | None:
    n = norm(label)
    if _ABOUT_SOMEONE_ELSE.search(n):
        return None
    for rx, key in _ALIASES:
        if rx.search(n):
            return key
    found = canonical_for(label)
    return found.key if found else None


@dataclass
class Known:
    profile: Mapping[str, str] = field(default_factory=dict)
    answers: Mapping[str, Mapping[str, str]] = field(default_factory=dict)  # norm(question) -> scope -> answer
    citizenships: tuple[str, ...] = ()  # canonical country names, primary first
    employers: tuple[str, ...] = ()  # companies from the owner's work history
    education: tuple[dict[str, Any], ...] = ()


@dataclass
class JobContext:
    company: str = ""
    role: str = ""
    url: str = ""
    location: str = ""


_SOURCES = (
    ("linkedin", "LinkedIn"),
    ("indeed", "Indeed"),
    ("glassdoor", "Glassdoor"),
    ("wellfound", "Wellfound"),
    ("handshake", "Handshake"),
)
_WORK_AUTH = re.compile(
    r"authori[sz]ed|right to work|eligible to work|legally (entitled|permitted)|permission to work|work permit"
)


def _derived(key: str | None, label: str, known: Known, ctx: JobContext) -> tuple[str, str] | None:
    """Answers the planner can work out from what the owner already told us, without asking again."""
    n = norm(label)
    cits = set(known.citizenships)
    if _WORK_AUTH.search(n):  # "authorised to work in the UK?" -> yes when the owner holds that citizenship
        named = mentions(label)
        if named and any(country in cits for country in named):
            return "Yes", "derived:citizenship"
    if key in ("us_person_status", "work_authorization_us") and "United States" in cits:
        return "Yes", "derived:citizenship"
    if key == "requires_sponsorship":
        target = mentions(label) or [c for c in [normalize_country(ctx.location.split(",")[-1].strip())] if c]
        if target and any(c in cits for c in target):
            return "No", "derived:citizenship"
        if not target and "United States" in cits:
            return "No", "derived:citizenship"
    if key == "nationality" and cits:
        job_country = next(iter(mentions(ctx.location)), None) or normalize_country(ctx.location.split(",")[-1].strip())
        pick = job_country if job_country in cits else known.citizenships[0]
        return pick, "derived:citizenship"
    if key in ("education_start_year", "education_end_year", "education_end_month"):
        from .profile import derive

        value = derive("education", list(known.education)).get(key, "")
        return (value, "derived:education") if value else None
    if key == "city" and n == "location city":
        parts = [known.profile.get(k, "").strip() for k in ("city", "state", "country")]
        return (", ".join(parts), "derived:profile location") if all(parts) else None
    if key == "full_name":  # the profile stores the parts; an explicit full_name value never reaches here
        first, last = known.profile.get("first_name", "").strip(), known.profile.get("last_name", "").strip()
        return (f"{first} {last}", "derived:profile name") if first and last else None
    if key is None:
        found = canonical_for(label)
        key = found.key if found else None
    if key == "previously_employed" and known.employers and ctx.company:
        company = set(norm(ctx.company).split())
        if not company:
            return None  # a name with no comparable words ("Inc.") proves nothing either way
        # whole words, so "Apple" is not "Pineapple Health" and "Meta" is not "Metabase"
        same = any(e and (e <= company or company <= e) for e in (set(norm(name).split()) for name in known.employers))
        return ("Yes" if same else "No"), "derived:work history"
    if key == "how_did_you_hear" and ctx.url:
        host = ctx.url.split("/")[2].lower() if "//" in ctx.url else ""
        return next((name for needle, name in _SOURCES if needle in host), "Company website"), "derived:job source"
    if key == "seeking_internship" and re.search(r"\bintern(ship)?\b", ctx.role, re.I):
        return "Yes", "derived:job title"
    return None


_MONTHS = (
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


def _date_part(value: str, label: str, options: list[str]) -> str | None:
    """A stored `YYYY-MM[-DD]` date for a form that asks for its day, month or year on separate fields."""
    m = re.fullmatch(r"\s*(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?\s*", value)
    if not m:
        return value
    year, month, day = m.groups()
    n = norm(label)
    if re.search(r"\bday\b", n):
        return day
    if re.search(r"\bmonth\b", n):
        if not month or not 1 <= int(month) <= 12:
            return None
        name = _MONTHS[int(month) - 1]
        digits = [o for o in options if re.fullmatch(r"0?\d{1,2}", o.strip())]
        return str(int(month)) if digits and not any(o.strip().lower() == name.lower() for o in options) else name
    if re.search(r"\byear\b", n):
        return year
    return value


# Stored as a job-scoped answer when the owner chooses not to answer a question. Never planned, never asked again.
SKIPPED = "(skipped by owner)"


def build_plan(
    fields: list[dict[str, Any]], known: Known, job_id: str, company_slug: str, ctx: JobContext | None = None
) -> tuple[list[PlanItem], list[str]]:
    ctx = ctx or JobContext()
    plan: list[PlanItem] = []
    missing: list[str] = []
    scopes = [f"job:{job_id}", f"company:{company_slug}", "global"]
    for f in fields:
        ftype = f.get("type")
        if ftype in ("file", "secret"):
            continue
        qk = norm(f.get("label", ""))
        hit: tuple[str, str] | None = None
        by_scope = known.answers.get(qk)
        if by_scope:
            for s in scopes:
                saved = by_scope.get(s, "").strip()  # a blank saved answer is not an answer
                if saved == SKIPPED:
                    if s == f"job:{job_id}":  # only this application: a skip never leaks to other jobs
                        break
                    continue
                if saved:
                    hit = (by_scope[s], f"answer:{s}")
                    break
        if by_scope and by_scope.get(f"job:{job_id}", "").strip() == SKIPPED:
            continue  # the owner chose to leave this one empty: not planned, not missing
        if hit is None and ftype not in ("checkbox", "custom-checkbox"):
            # a checkbox/radio shares words with profile keys by accident ("Linkedin" option); selects and text are real questions
            pk = profile_key_for(f.get("label", ""))
            canon = canonical_for(f.get("label", ""))
            if canon is not None and canon.contextual and pk == canon.key:
                hit = _derived(pk, f.get("label", ""), known, ctx)  # job-specific: never a stored global value
            elif pk == "city" and norm(f.get("label", "")) == "location city":
                hit = _derived(pk, f.get("label", ""), known, ctx)
                if hit is None and known.profile.get(pk, "").strip():
                    hit = (known.profile[pk], f"profile:{pk}")
            elif pk and known.profile.get(pk, "").strip():
                hit = (known.profile[pk], f"profile:{pk}")
            else:
                hit = _derived(pk, f.get("label", ""), known, ctx)
        options: list[str] = f.get("options") or []
        if hit is not None and hit[1] == "profile:graduation":
            part = _date_part(hit[0], str(f.get("label", "")), options)
            hit = (part, hit[1]) if part else None  # a day the profile does not hold is asked, never invented
        if hit is not None:
            value, source = hit
            if f.get("unmatchedValue") and norm(value) == norm(str(f["unmatchedValue"])):
                if f.get("required"):
                    missing.append(f["key"])
                continue
            if options and value not in options:
                m = choose_option(
                    value,
                    options,
                    exact=ftype == "custom-select"
                    and (bool(f.get("searchable")) or profile_key_for(f.get("label", "")) in ("university", "city")),
                )  # same meaning, different wording: Yes -> "Yes, I am authorized"
                if m is None:
                    if f.get("required"):
                        missing.append(f["key"])
                    continue
                value = m
            plan.append(PlanItem(f["key"], json.dumps([value]) if f.get("multiple") else value, source))
        elif f.get("required"):
            missing.append(f["key"])
    return plan, missing

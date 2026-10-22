"""What an ATS publishes about a posting before anyone opens it: metadata and the application questions.

Pure parsers over already-fetched JSON (no I/O). Every provider is mapped into one shape, ``PostingForm``, whose
``questions`` convert to the inventory items ``build_plan`` already consumes, so a question list read over HTTP is
planned exactly like one scanned from a live page. All input is untrusted: parsers never raise on odd shapes, they
skip what they cannot understand, and callers must treat the result as a lower bound (conditional questions that only
appear after another answer are not visible in a static document).
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

MAX_QUESTIONS = 200
MAX_OPTIONS = 500
MAX_LABEL = 400
YES_NO = ("Yes", "No")


@dataclass(frozen=True)
class PostingQuestion:
    key: str  # provider field id, unique inside one posting
    label: str
    type: str  # inventory vocabulary: text email tel url number date textarea select file checkbox
    required: bool = False
    options: tuple[str, ...] = ()
    multiple: bool = False
    group: str = "application"  # application | standard (name/email/...) | eeoc (voluntary demographic)
    detail: str = ""  # the provider's help text, shown to the model as context

    def inventory_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {"key": self.key, "label": self.label, "type": self.type, "required": self.required}
        if self.options:
            item["options"] = list(self.options)
        if self.multiple:
            item["multiple"] = True
        return item


@dataclass(frozen=True)
class PostingMeta:
    role: str | None = None
    company: str | None = None
    location: str | None = None
    work_mode: str | None = None  # Remote | Hybrid | On-site
    pay: str | None = None
    employment_type: str | None = None
    job_level: str | None = None
    description: str | None = None
    department: str | None = None
    deadline: str | None = None
    published: str | None = None

    def as_set_meta(self) -> dict[str, str]:
        """Keyword arguments for ``JobsService.set_meta``: only what the provider actually stated."""
        mapping = {
            "role": self.role, "company": self.company, "location": self.location, "work_mode": self.work_mode,
            "pay": self.pay, "employment_type": self.employment_type, "job_level": self.job_level,
            "deadline": self.deadline, "posting": self.description,
        }  # fmt: skip
        return {k: v for k, v in mapping.items() if v}


@dataclass(frozen=True)
class PostingForm:
    provider: str
    meta: PostingMeta = field(default_factory=PostingMeta)
    questions: tuple[PostingQuestion, ...] = ()

    def inventory(self, *, groups: Iterable[str] | None = None) -> list[dict[str, Any]]:
        wanted = set(groups) if groups is not None else None
        return [q.inventory_item() for q in self.questions if wanted is None or q.group in wanted]


# ---------------------------------------------------------------- helpers


def _d(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = MAX_LABEL) -> str:
    if not isinstance(value, str):
        return ""
    plain = re.sub(r"<[^>]+>", " ", html.unescape(value))
    return " ".join(plain.split())[:limit]


def _options(values: Any, *keys: str) -> tuple[str, ...]:
    out: list[str] = []
    if not isinstance(values, list):
        return ()
    for item in values[:MAX_OPTIONS]:
        label = _text(item.get(keys[0]) if isinstance(item, dict) else item, 200)
        if not label and isinstance(item, dict):
            label = next((_text(item.get(k), 200) for k in keys[1:] if item.get(k)), "")
        if label and label not in out:
            out.append(label)
    return tuple(out)


def _work_mode(value: Any) -> str | None:
    n = str(value or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    return {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site", "inoffice": "On-site", "telecommute": "Remote"}.get(n)


def _employment(value: Any) -> str | None:
    n = str(value or "").strip()
    if not n:
        return None
    pretty = {"FullTime": "Full-time", "PartTime": "Part-time", "Intern": "Internship", "Contract": "Contract",
              "Temporary": "Temporary"}  # fmt: skip
    return pretty.get(n, n.replace("_", " ").title() if n.isupper() else n)


def _money(cents: int, currency: str) -> str:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency, f"{currency} " if currency else "")
    dollars = cents / 100
    return f"{symbol}{dollars:,.0f}" if cents % 100 == 0 else f"{symbol}{dollars:,.2f}"


# ---------------------------------------------------------------- Greenhouse

_GH_TYPES = {
    "input_text": "text",
    "textarea": "textarea",
    "input_file": "file",
    "multi_value_single_select": "select",
    "multi_value_multi_select": "select",
    "input_hidden": "",
}
_GH_NAME_TYPES = {"email": "email", "phone": "tel"}


def _gh_questions(items: Any, group: str) -> list[PostingQuestion]:
    out: list[PostingQuestion] = []
    for q in items if isinstance(items, list) else []:
        if not isinstance(q, dict):
            continue
        label = _text(q.get("label"))
        fields = [f for f in (q.get("fields") or []) if isinstance(f, dict)]
        for f in fields:
            name = str(f.get("name") or "")
            kind = _GH_TYPES.get(str(f.get("type")))
            if not name or not kind or not label:
                continue
            kind = _GH_NAME_TYPES.get(name, kind)
            multiple = f.get("type") == "multi_value_multi_select"
            # "Cover Letter" is one question rendered as a file input plus a paste box. The page shows the plain
            # label, so only fields that would otherwise collide on it carry the field name.
            same = [x for x in fields if _GH_TYPES.get(str(x.get("type"))) == kind]
            sub = label if len(same) == 1 else f"{label} ({name})"
            standard = name in {"first_name", "last_name", "email", "phone", "resume", "cover_letter"}
            out.append(
                PostingQuestion(
                    key=f"gh:{name}",
                    label=sub,
                    type=kind,
                    required=bool(q.get("required")),
                    options=_options(f.get("values"), "label") if kind == "select" else (),
                    multiple=multiple,
                    group="standard" if standard and group == "application" else group,
                    detail=_text(q.get("description"), 600),
                )
            )
    return out


def _gh_pay(ranges: Any) -> str | None:
    if not isinstance(ranges, list):
        return None
    seen: list[tuple[int, int, str]] = []
    for r in ranges:
        if isinstance(r, dict) and isinstance(r.get("min_cents"), int) and isinstance(r.get("max_cents"), int):
            tup = (r["min_cents"], r["max_cents"], str(r.get("currency_type") or "USD"))
            if tup not in seen:
                seen.append(tup)
    if not seen:
        return None
    low, high, cur = min(s[0] for s in seen), max(s[1] for s in seen), seen[0][2]
    # Greenhouse states no interval. Under 1,000 currency units is not an annual figure, so call it hourly.
    unit = "/hr" if high < 100_000 else "/yr"
    return f"{_money(low, cur)}–{_money(high, cur)}{unit}"


def parse_greenhouse(data: Mapping[str, Any]) -> PostingForm:
    meta_fields = {str(m.get("name")): m.get("value") for m in (data.get("metadata") or []) if isinstance(m, dict)}
    location = data.get("location")
    offices = [o for o in (data.get("offices") or []) if isinstance(o, dict)]
    departments = [d for d in (data.get("departments") or []) if isinstance(d, dict)]
    level = next((str(v) for k, v in meta_fields.items() if "level" in k.lower() and isinstance(v, str)), None)
    meta = PostingMeta(
        role=_text(data.get("title")) or None,
        company=_text(data.get("company_name")) or None,
        location=_text(location.get("name") if isinstance(location, dict) else location) or None,
        pay=_gh_pay(data.get("pay_input_ranges")),
        job_level=level,
        description=_text(data.get("content"), 30_000) or None,
        department=_text(departments[0].get("name")) if departments else None,
        deadline=str(data["application_deadline"]) if data.get("application_deadline") else None,
        published=str(data.get("first_published") or "") or None,
    )
    if not meta.location and offices:
        meta = PostingMeta(**{**meta.__dict__, "location": _text(offices[0].get("location") or offices[0].get("name"))})
    questions = _gh_questions(data.get("questions"), "application")
    questions += _gh_questions(data.get("location_questions"), "application")
    for block in data.get("compliance") or []:
        if isinstance(block, dict):
            questions += _gh_questions(block.get("questions"), "eeoc")
    demographic = data.get("demographic_questions")
    if isinstance(demographic, dict):
        questions += _gh_questions(demographic.get("questions"), "eeoc")
    return PostingForm("greenhouse", meta, tuple(questions[:MAX_QUESTIONS]))


# ---------------------------------------------------------------- Ashby

_ASHBY_TYPES = {
    "String": "text", "Email": "email", "Phone": "tel", "Url": "url", "LongText": "textarea", "Number": "number",
    "Date": "date", "File": "file", "Location": "text", "Boolean": "select", "ValueSelect": "select",
    "MultiValueSelect": "select", "Score": "number",
}  # fmt: skip
_ASHBY_STANDARD = {"_systemfield_name", "_systemfield_email", "_systemfield_resume", "_systemfield_location"}


def parse_ashby_meta(posting: Mapping[str, Any]) -> PostingMeta:
    """The ``jobPosting`` object of the hosted page's ``ApiJobPosting`` GraphQL operation."""
    pay = _text(posting.get("scrapeableCompensationSalarySummary") or posting.get("compensationTierSummary")) or None
    return PostingMeta(
        role=_text(posting.get("title")) or None,
        location=_text(posting.get("locationName")) or None,
        work_mode=_work_mode(posting.get("workplaceType")),
        pay=pay,
        employment_type=_employment(posting.get("employmentType")),
        description=_text(posting.get("descriptionHtml"), 30_000) or None,
        department=_text(posting.get("departmentName")) or None,
        deadline=str(posting["applicationDeadline"]) if posting.get("applicationDeadline") else None,
    )


def parse_ashby_form(data: Mapping[str, Any]) -> tuple[PostingQuestion, ...]:
    """The ``ApiJobPosting`` GraphQL response (what the hosted application page itself requests)."""
    form = _d(_d(_d(data.get("data")).get("jobPosting")).get("applicationForm"))
    out: list[PostingQuestion] = []
    for section in form.get("sections") or []:
        for entry in _d(section).get("fieldEntries") or []:
            fld = entry.get("field") if isinstance(entry, dict) else None
            if not isinstance(fld, dict):
                continue
            kind = _ASHBY_TYPES.get(str(fld.get("type")))
            label = _text(fld.get("title"))
            path = str(fld.get("path") or fld.get("id") or "")
            if not kind or not label or not path:
                continue
            options = YES_NO if fld.get("type") == "Boolean" else _options(fld.get("selectableValues"), "label", "value")
            out.append(
                PostingQuestion(
                    key=f"ashby:{path}",
                    label=label,
                    type=kind,
                    required=bool(entry.get("isRequired")),
                    options=tuple(options) if kind == "select" else (),
                    multiple=fld.get("type") == "MultiValueSelect",
                    group="standard" if path in _ASHBY_STANDARD else "application",
                    detail=_text(entry.get("descriptionHtml"), 600),
                )
            )
    return tuple(out[:MAX_QUESTIONS])


def parse_ashby(data: Mapping[str, Any], company: str | None = None) -> PostingForm:
    meta = parse_ashby_meta(_d(_d(data.get("data")).get("jobPosting")))
    if company:  # the operation does not name the employer; the board slug is the best stated source
        meta = PostingMeta(**{**meta.__dict__, "company": company})
    return PostingForm("ashby", meta, parse_ashby_form(data))


# ---------------------------------------------------------------- BambooHR

_BAMBOO_STANDARD = {
    "firstName": ("text", "standard"), "lastName": ("text", "standard"), "email": ("email", "standard"),
    "phone": ("tel", "standard"), "streetAddress": ("text", "application"), "city": ("text", "application"),
    "state": ("select", "application"), "zip": ("text", "application"), "countryId": ("select", "application"),
    "linkedinUrl": ("url", "application"), "dateAvailable": ("date", "application"),
    "desiredPay": ("text", "application"), "websiteUrl": ("url", "application"), "resumeFileId": ("file", "standard"),
    "coverLetterFileId": ("file", "application"), "referredBy": ("text", "application"),
}  # fmt: skip
_BAMBOO_EEOC = {"genderId", "ethnicityId", "veteranStatusId", "disabilityId"}
_BAMBOO_CHOICE = {"yes_no", "dropdown", "radio", "select", "multiple_choice", "single_choice"}
_BAMBOO_LONG = {"textarea", "long_text", "essay", "paragraph"}


def parse_bamboo(data: Mapping[str, Any]) -> PostingForm:
    result = _d(data.get("result"))
    opening = _d(result.get("jobOpening"))
    loc = _d(opening.get("location"))
    place = ", ".join(_text(loc.get(k)) for k in ("city", "state", "addressCountry") if loc.get(k))
    compensation = _text(opening.get("compensation")) or None
    status = _text(opening.get("employmentStatusLabel")) or None
    meta = PostingMeta(
        role=_text(opening.get("jobOpeningName")) or None,
        location=place or None,
        pay=compensation,
        employment_type=status,
        description=_text(opening.get("description"), 30_000) or None,
        department=_text(opening.get("departmentLabel")) or None,
        published=str(opening.get("datePosted") or "") or None,
        work_mode={"1": "Remote", "2": "Hybrid"}.get(str(opening.get("locationType"))) or ("On-site" if loc else None),
    )
    questions: list[PostingQuestion] = []
    fields = _d(result.get("formFields"))
    for name, spec in fields.items():
        if name == "customQuestions" and isinstance(spec, list):
            for cq in spec:
                if not isinstance(cq, dict) or not cq.get("question") or not cq.get("id"):
                    continue
                kind = str(cq.get("type") or "")
                options = YES_NO if kind == "yes_no" else _options(cq.get("options"), "text", "label", "value")
                is_choice = kind in _BAMBOO_CHOICE and bool(options)
                questions.append(
                    PostingQuestion(
                        key=f"bamboo:q{cq['id']}",
                        label=_text(cq.get("question")),
                        type="select" if is_choice else ("textarea" if kind in _BAMBOO_LONG else "text"),
                        required=bool(cq.get("isRequired")),
                        options=tuple(options) if is_choice else (),
                    )
                )
            continue
        if name in _BAMBOO_EEOC:
            options = _options(spec.get("options") if isinstance(spec, dict) else None, "text")
            if options:
                questions.append(PostingQuestion(f"bamboo:{name}", _text(name), "select", False, options, group="eeoc"))
            continue
        if not isinstance(spec, dict) or name not in _BAMBOO_STANDARD:
            continue
        kind, group = _BAMBOO_STANDARD[name]
        questions.append(
            PostingQuestion(
                key=f"bamboo:{name}",
                label=_text(spec.get("label")) or name,
                type=kind,
                required=bool(spec.get("isRequired")),
                options=_options(spec.get("options"), "text") if kind == "select" else (),
                group=group,
            )
        )
    return PostingForm("bamboohr", meta, tuple(questions[:MAX_QUESTIONS]))


# ---------------------------------------------------------------- Workable

_WORKABLE_TYPES = {
    "text": "text", "email": "email", "phone": "tel", "paragraph": "textarea", "dropdown": "select",
    "boolean": "select", "file": "file", "date": "date", "number": "number", "multiple": "select",
}  # fmt: skip
_WORKABLE_STANDARD = {"firstname", "lastname", "email", "phone", "resume", "cover_letter", "avatar"}


def _workable_location(value: Any) -> str | None:
    loc = _d(value)
    parts = [_text(loc.get(k)) for k in ("city", "region", "country") if loc.get(k)]
    return ", ".join(dict.fromkeys(p for p in parts if p)) or None


def parse_workday(data: Mapping[str, Any]) -> PostingForm:
    """Workday CXS ``/wday/cxs/{tenant}/{site}/job/{path}`` (public JSON behind every career page). It publishes no
    question list, so ``questions`` is empty and the wizard is left to the browser."""
    info = _d(data.get("jobPostingInfo"))
    org = _d(data.get("hiringOrganization"))
    extra = [_text(x, 120) for x in info.get("additionalLocations") or [] if isinstance(x, str)]
    location = " / ".join(x for x in [_text(info.get("location"), 160), *extra[:3]] if x) or None
    meta = PostingMeta(
        role=_text(info.get("title")) or None,
        company=_text(org.get("name")) or None,
        location=location,
        work_mode=_work_mode(info.get("remoteType")),
        employment_type={"full time": "Full-time", "part time": "Part-time"}.get(_text(info.get("timeType"), 40).lower())
        or _text(info.get("timeType"), 40)
        or None,
        description=_text(info.get("jobDescription"), 30_000) or None,
        published=_text(info.get("startDate"), 40) or None,
    )
    return PostingForm("workday", meta, ())


def parse_workable(job: Mapping[str, Any], form: Any) -> PostingForm:
    """``/api/v1/accounts/{account}/jobs/{shortcode}`` plus ``/api/v1/jobs/{shortcode}/form`` (both public)."""
    workplace = _work_mode(job.get("workplace")) or ("Remote" if job.get("remote") is True else None)
    department = job.get("department")
    description = "\n\n".join(_text(job.get(k), 30_000) for k in ("description", "requirements", "benefits") if job.get(k))
    meta = PostingMeta(
        role=_text(job.get("title")) or None,
        location=_workable_location(job.get("location")),
        work_mode=workplace,
        employment_type={
            "full": "Full-time",
            "part": "Part-time",
            "contract": "Contract",
            "temporary": "Temporary",
            "internship": "Internship",
            "intern": "Internship",
        }.get(str(job.get("type") or "").lower())
        or (_text(job.get("type")).replace("_", " ").title() or None),
        description=description[:30_000] or None,
        department=_text(department[0] if isinstance(department, list) and department else department) or None,
        published=str(job.get("published") or "") or None,
    )
    out: list[PostingQuestion] = []
    for section in form if isinstance(form, list) else []:
        for fld in _d(section).get("fields") or []:
            f = _d(fld)
            kind = _WORKABLE_TYPES.get(str(f.get("type")))
            label, fid = _text(f.get("label")), str(f.get("id") or "")
            if not kind or not label or not fid:  # groups (education/experience lists) are left to the browser
                continue
            if f.get("type") == "boolean":
                options: tuple[str, ...] = YES_NO
            elif kind == "select":
                options = _options(f.get("options"), "value", "name")
            else:
                options = ()
            out.append(
                PostingQuestion(
                    key=f"workable:{fid}",
                    label=label,
                    type=kind,
                    required=bool(f.get("required")),
                    options=options,
                    multiple=f.get("type") == "multiple",
                    group="standard" if fid in _WORKABLE_STANDARD else "application",
                    detail=_text(f.get("helper"), 600),
                )
            )
    return PostingForm("workable", meta, tuple(out[:MAX_QUESTIONS]))


# ---------------------------------------------------------------- SmartRecruiters (metadata only)


def parse_smartrecruiters(posting: Mapping[str, Any]) -> PostingForm:
    """The public posting API. Its application questions need an employer token, so there are none here."""
    loc = _d(posting.get("location"))
    place = ", ".join(_text(loc.get(k)) for k in ("city", "region", "country") if loc.get(k))
    sections = _d(_d(posting.get("jobAd")).get("sections"))
    description = "\n\n".join(
        _text(_d(sections.get(k)).get("text"), 30_000)
        for k in ("jobDescription", "qualifications", "additionalInformation", "companyDescription")
        if _d(sections.get(k)).get("text")
    )
    mode = "Remote" if loc.get("remote") is True else ("Hybrid" if loc.get("hybrid") is True else None)
    meta = PostingMeta(
        role=_text(posting.get("name")) or None,
        company=_text(_d(posting.get("company")).get("name")) or None,
        location=place or None,
        work_mode=mode,
        employment_type=_text(_d(posting.get("typeOfEmployment")).get("label")) or None,
        job_level=_text(_d(posting.get("experienceLevel")).get("label")) or None,
        description=description[:30_000] or None,
        department=_text(_d(posting.get("function")).get("label")) or None,
        published=str(posting.get("releasedDate") or "") or None,
    )
    return PostingForm("smartrecruiters", meta, ())

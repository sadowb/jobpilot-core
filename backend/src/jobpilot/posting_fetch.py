"""Read a posting's metadata and application questions over HTTP, with no browser.

Each supported ATS publishes these on the same unauthenticated endpoints its hosted page uses:
  Greenhouse  GET boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}?questions=true
  Ashby       POST jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting   (undocumented: the page's own query)
  BambooHR    GET {tenant}.bamboohr.com/careers/{id}/detail
Everything here is best effort: any failure returns None and the browser path takes over unchanged. Reuses the
SSRF guard, size cap and timeout of ``extract``; the parsed result is untrusted data.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

from .boards import resolve_board
from .domain.html_form import parse_html_form
from .domain.posting_form import (
    PostingForm,
    PostingMeta,
    parse_ashby,
    parse_bamboo,
    parse_greenhouse,
    parse_smartrecruiters,
    parse_workable,
    parse_workday,
)
from .extract import _MAX_BYTES, _TIMEOUT, _UA, _check_url, _get, _jsonld, _read_bounded

log = logging.getLogger("jobpilot.posting_fetch")

_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}")
_UUID = re.compile(r"[0-9a-fA-F-]{32,36}")
_DIGITS = re.compile(r"\d{1,20}")

ASHBY_GRAPHQL = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting"
ASHBY_QUERY = (
    "query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) { "
    "jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) { "
    "id title departmentName locationName workplaceType employmentType descriptionHtml compensationTierSummary "
    "scrapeableCompensationSalarySummary secondaryLocationNames applicationDeadline isListed "
    "applicationForm { sections { title descriptionHtml fieldEntries { ... on FormFieldEntry "
    "{ isRequired field descriptionHtml } } } } } }"
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _post_json(url: str, body: dict[str, Any]) -> Any:
    _check_url(url)
    request = urllib.request.Request(  # noqa: S310 - https URL built from validated constants + slugs
        url,
        data=json.dumps(body).encode(),
        headers={"User-Agent": _UA, "Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
        raw = _read_bounded(response)
    if len(raw) > _MAX_BYTES:
        raise ValueError("response too large")
    return json.loads(raw.decode("utf-8", errors="replace"))


def _title(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).strip().title()


def _greenhouse(tenant: str, job: str) -> PostingForm | None:
    url = f"https://boards-api.greenhouse.io/v1/boards/{quote(tenant)}/jobs/{quote(job)}?questions=true&pay_transparency=true"
    data = json.loads(_get(url, "application/json"))
    return parse_greenhouse(data) if isinstance(data, dict) and data.get("questions") is not None else None


def _ashby(tenant: str, job: str) -> PostingForm | None:
    data = _post_json(
        ASHBY_GRAPHQL,
        {
            "operationName": "ApiJobPosting",
            "variables": {"organizationHostedJobsPageName": tenant, "jobPostingId": job},
            "query": ASHBY_QUERY,
        },
    )
    if not isinstance(data, dict) or data.get("errors") or not _dict(data.get("data")).get("jobPosting"):
        return None
    return parse_ashby(data, company=_title(tenant))


def _bamboo(tenant: str, job: str) -> PostingForm | None:
    data = json.loads(_get(f"https://{quote(tenant)}.bamboohr.com/careers/{quote(job)}/detail", "application/json"))
    if not isinstance(data, dict) or not _dict(data.get("result")).get("jobOpening"):
        return None
    form = parse_bamboo(data)
    meta = PostingMeta(**{**form.meta.__dict__, "company": form.meta.company or _title(tenant)})
    return PostingForm(form.provider, meta, form.questions)


def _workable(url: str) -> PostingForm | None:
    """apply.workable.com/{account}/j/{shortcode}: both documents are the ones the hosted page itself loads."""
    match = re.fullmatch(r"/([A-Za-z0-9][A-Za-z0-9_-]{0,80})/j/([A-Za-z0-9]{6,20})/?", urlsplit(url).path)
    if not match:
        return None
    account, code = match.groups()
    job = json.loads(_get(f"https://apply.workable.com/api/v1/accounts/{quote(account)}/jobs/{quote(code)}", "application/json"))
    form = json.loads(_get(f"https://apply.workable.com/api/v1/jobs/{quote(code)}/form", "application/json"))
    if not isinstance(job, dict) or not job.get("title"):
        return None
    parsed = parse_workable(job, form)
    meta = PostingMeta(**{**parsed.meta.__dict__, "company": parsed.meta.company or _title(account)})
    return PostingForm(parsed.provider, meta, parsed.questions)


_WORKDAY_HOST = re.compile(r"([a-z0-9][a-z0-9-]{0,60})\.wd\d{1,3}\.myworkdayjobs\.com")
_WORKDAY_PATH = re.compile(r"(?:/[a-z]{2}(?:-[A-Za-z]{2})?)?/([A-Za-z0-9_-]{1,80})/job/([^?#]{1,300}?)(?:/apply.*)?/?")


def _workday(url: str) -> PostingForm | None:
    """{tenant}.wdN.myworkdayjobs.com/[lang/]{site}/job/{path}: the page's own CXS document, no browser and no model."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    on_host, on_path = _WORKDAY_HOST.fullmatch(host), _WORKDAY_PATH.fullmatch(parts.path)
    if not on_host or not on_path:
        return None
    site, path = on_path.groups()
    api = f"https://{host}/wday/cxs/{quote(on_host.group(1))}/{quote(site)}/job/{quote(path, safe='/')}"
    data = json.loads(_get(api, "application/json"))
    return parse_workday(data) if isinstance(data, dict) and _dict(data.get("jobPostingInfo")).get("title") else None


def _smartrecruiters(url: str) -> PostingForm | None:
    match = re.fullmatch(r"/([A-Za-z0-9][A-Za-z0-9_.-]{0,80})/(\d{6,20})(-[^/]*)?/?", urlsplit(url).path)
    if not match:
        return None
    company, posting = match.group(1), match.group(2)
    data = json.loads(
        _get(f"https://api.smartrecruiters.com/v1/companies/{quote(company)}/postings/{quote(posting)}", "application/json")
    )
    return parse_smartrecruiters(data) if isinstance(data, dict) and data.get("name") else None


def _meta_from_jsonld(markup: str) -> PostingMeta:
    ld: dict[str, Any] = _jsonld(markup) or {}
    mapping = {
        "role": ld.get("role"), "company": ld.get("company"), "location": ld.get("location"),
        "work_mode": ld.get("work_mode"), "pay": ld.get("pay"), "employment_type": ld.get("employment_type"),
        "description": ld.get("description"),
    }  # fmt: skip
    return PostingMeta(**{k: (str(v)[:30_000] if v else None) for k, v in mapping.items()})


def _html_posting(url: str, provider: str) -> PostingForm | None:
    """CareerPlug / JazzHR: one server-rendered page carries the JSON-LD posting and the whole application form."""
    markup = _get(url)
    questions = parse_html_form(markup, provider)
    if not questions:
        return None
    return PostingForm(provider, _meta_from_jsonld(markup), questions)


def _lever_form(tenant: str, job: str, host: str) -> PostingForm | None:
    api = "api.eu.lever.co" if ".eu." in host else "api.lever.co"
    data = json.loads(_get(f"https://{api}/v0/postings/{quote(tenant)}/{quote(job)}", "application/json"))
    if not isinstance(data, dict) or not data.get("text"):
        return None
    cats = _dict(data.get("categories"))
    salary = _dict(data.get("salaryRange"))
    pay = None
    if salary.get("min") and salary.get("max"):
        interval = str(salary.get("interval") or "").replace("per-", "").replace("-salary", "")
        pay = f"{salary.get('currency', '')} {salary['min']}-{salary['max']}{('/' + interval) if interval else ''}".strip()
    mode = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site", "on-site": "On-site"}.get(
        str(data.get("workplaceType") or "").lower()
    )
    meta = PostingMeta(
        role=str(data["text"])[:400],
        company=_title(tenant),
        location=str(cats.get("location") or "")[:300] or None,
        work_mode=mode,
        pay=pay,
        employment_type=str(cats.get("commitment") or "")[:100] or None,
        description="\n\n".join(str(data.get(k)) for k in ("descriptionPlain", "additionalPlain") if data.get(k))[:30_000]
        or None,
        department=str(cats.get("department") or "")[:200] or None,
    )
    apply_url = str(data.get("applyUrl") or f"https://{host}/{quote(tenant)}/{quote(job)}/apply")
    if (urlsplit(apply_url).hostname or "").lower() != host.lower():
        apply_url = f"https://{host}/{quote(tenant)}/{quote(job)}/apply"
    questions = parse_html_form(_get(apply_url), "lever")
    return PostingForm("lever", meta, questions) if questions else None


_GREENHOUSE_EMBED_HOSTS = ("boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io")


def fetch_embedded_greenhouse(frame_urls: list[str]) -> PostingForm | None:
    """A company-hosted page (`?gh_jid=`) renders the Greenhouse application in an iframe. Given that page's iframe
    URLs, read the posting from Greenhouse's public API using the board and job id in the embed URL."""
    for frame in frame_urls:
        parts = urlsplit(frame)
        if parts.scheme != "https" or (parts.hostname or "").lower() not in _GREENHOUSE_EMBED_HOSTS:
            continue
        query = parse_qs(parts.query)
        tenant = (query.get("for") or [""])[0]
        job = (query.get("token") or [""])[0]
        if not (_SLUG.fullmatch(tenant) and _DIGITS.fullmatch(job)):
            continue
        try:
            return _greenhouse(tenant, job)
        except Exception as error:  # noqa: BLE001 - the page-text path still runs
            log.info("embedded greenhouse fetch failed: %s", type(error).__name__)
    return None


def fetch_posting_form(url: str) -> PostingForm | None:
    """The posting's published metadata and question list, or None when the provider is unsupported or unreachable."""
    try:
        adapter, match = resolve_board(url)
    except ValueError:
        return None
    tenant, job = match.tenant or "", match.requisition_id or ""
    parts = urlsplit(match.canonical_url or url)
    host = (parts.hostname or "").lower()
    try:
        if host == "apply.workable.com":
            return _workable(match.canonical_url or url)
        if host.endswith(".myworkdayjobs.com"):
            return _workday(match.canonical_url or url)
        if host == "jobs.smartrecruiters.com":
            return _smartrecruiters(match.canonical_url or url)
        if adapter.id == "lever" and _SLUG.fullmatch(tenant) and _UUID.fullmatch(job):
            return _lever_form(tenant, job, host)
        if host.endswith(".careerplug.com") and re.fullmatch(r"/jobs/\d+", parts.path.rstrip("/")):
            return _html_posting(urlunsplit(("https", host, parts.path.rstrip("/"), "", "")), "careerplug")
        if host.endswith(".applytojob.com") and re.fullmatch(r"/apply/[A-Za-z0-9]{6,20}(/[^/]*)?", parts.path):
            return _html_posting(urlunsplit(("https", host, parts.path, "", "")), "jazzhr")
        if adapter.id == "greenhouse" and _SLUG.fullmatch(tenant) and _DIGITS.fullmatch(job):
            return _greenhouse(tenant, job)
        if adapter.id == "ashby" and _SLUG.fullmatch(tenant) and _UUID.fullmatch(job):
            return _ashby(tenant, job)
        if adapter.id == "bamboohr" and _SLUG.fullmatch(tenant) and _DIGITS.fullmatch(job):
            return _bamboo(tenant, job)
    except Exception as error:  # noqa: BLE001 - network, JSON or shape: the browser path covers it
        log.info("posting fetch failed board=%s: %s", adapter.id, type(error).__name__)
    return None

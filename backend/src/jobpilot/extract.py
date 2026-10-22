"""Instant, token-free posting extraction for "I applied myself".

Order: public ATS JSON APIs (Greenhouse, Lever) -> schema.org JobPosting JSON-LD -> Open Graph / <title>.
Only fetches the one URL the owner pasted; short timeout, size cap, http(s) only. Everything it returns is
untrusted page data: callers store it as text, never as instructions.
"""

from __future__ import annotations

import contextlib
import html
import ipaddress
import json
import re
import socket
import time
import urllib.request
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

_UA = "Mozilla/5.0 (compatible; JobPilot/1.0; +local)"
_MAX_BYTES = 2_000_000
_TIMEOUT = 12
_PAGE_TEXT_CAP = 30_000


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("only http(s) links can be read")
    try:
        for info in socket.getaddrinfo(parts.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_link_local or ip.is_multicast or ip.is_unspecified:  # cloud-metadata style targets
                raise ValueError("address not allowed")
    except socket.gaierror as error:
        raise ValueError("host not found") from error


def _read_bounded(response: Any) -> bytes:
    """Limit total body-read time as well as socket inactivity and bytes (a trickle must not hold control forever)."""
    deadline = time.monotonic() + _TIMEOUT
    raw = bytearray()
    while len(raw) <= _MAX_BYTES:
        if time.monotonic() >= deadline:
            raise TimeoutError("posting body deadline exceeded")
        chunk = response.read1(min(65536, _MAX_BYTES + 1 - len(raw)))
        if not chunk:
            break
        raw.extend(chunk)
    return bytes(raw)


def _get(url: str, accept: str = "text/html,application/json") -> str:
    _check_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": accept})  # noqa: S310 - scheme checked above
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310 - scheme checked above
        raw = _read_bounded(response)
        charset = response.headers.get_content_charset() or "utf-8"
    if len(raw) > _MAX_BYTES:
        raw = raw[:_MAX_BYTES]
    return str(raw.decode(charset, errors="replace"))


class _Text(HTMLParser):
    _BLOCK = {"p", "br", "li", "div", "h1", "h2", "h3", "h4", "ul", "ol", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")
            if tag == "li":
                self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _Text()
    parser.feed(markup)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _title(value: str) -> str:
    return re.sub(r"[-_]+", " ", value).strip().title()


def _greenhouse(url: str) -> dict[str, Any] | None:
    match = re.search(r"(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([^/?#]+)/jobs/(\d+)", url)
    if not match:
        return None
    board, job = match.groups()
    company = None
    with contextlib.suppress(Exception):  # the company name is a nicety; the board token is the fallback
        company = json.loads(_get(f"https://boards-api.greenhouse.io/v1/boards/{board}", "application/json")).get("name")
    try:
        data = json.loads(_get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}", "application/json"))
    except Exception:  # noqa: BLE001 - posting removed/closed: still know the company
        return {"company": company or _title(board), "closed": True}
    content = html.unescape(data.get("content") or "")
    return {
        "company": company or _title(board),
        "role": data.get("title"),
        "location": (data.get("location") or {}).get("name"),
        "description": html_to_text(content),
    }


def _lever(url: str) -> dict[str, Any] | None:
    match = re.search(r"jobs(?:\.eu)?\.lever\.co/([^/?#]+)/([0-9a-f-]{20,})", url)
    if not match:
        return None
    company, job = match.groups()
    host = "api.eu.lever.co" if ".eu." in url else "api.lever.co"
    data = json.loads(_get(f"https://{host}/v0/postings/{company}/{job}", "application/json"))
    cats = data.get("categories") or {}
    description = "\n\n".join(x for x in (data.get("descriptionPlain"), data.get("additionalPlain")) if x)
    return {
        "company": _title(company),
        "role": data.get("text"),
        "location": cats.get("location"),
        "employment_type": cats.get("commitment"),
        "work_mode": data.get("workplaceType"),
        "description": description or None,
    }


def _jsonld(markup: str) -> dict[str, Any] | None:
    for block in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", markup, flags=re.S | re.I):
        # Most sites emit valid JSON; some HTML-escape the whole block. Unescaping first would corrupt JSON whose
        # strings contain &quot;, so the raw block is tried before the unescaped one.
        data = None
        for candidate in (block.strip(), html.unescape(block.strip())):
            try:
                data = json.loads(candidate)
                break
            except ValueError:
                continue
        if data is None:
            continue
        nodes = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for node in nodes:
            if not isinstance(node, dict) or "JobPosting" not in str(node.get("@type")):
                continue
            org = node.get("hiringOrganization") or {}
            loc = node.get("jobLocation")
            loc = loc[0] if isinstance(loc, list) and loc else loc
            address = (loc or {}).get("address", {}) if isinstance(loc, dict) else {}
            place = ", ".join(
                str(address.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry") if address.get(k)
            )
            salary = node.get("baseSalary")
            pay = None
            if isinstance(salary, dict):
                value = salary.get("value") or {}
                lo, hi = value.get("minValue"), value.get("maxValue")
                single = value.get("value")
                cur = salary.get("currency") or ""
                pay = f"{cur} {lo}-{hi}".strip() if lo and hi else (f"{cur} {single}".strip() if single else None)
                unit = {"YEAR": "/yr", "HOUR": "/hr", "MONTH": "/mo", "WEEK": "/wk", "DAY": "/day"}.get(
                    str(value.get("unitText", "")).upper(), ""
                )
                pay = f"{pay}{unit}" if pay else None
            remote = "Remote" if str(node.get("jobLocationType", "")).upper() == "TELECOMMUTE" else None
            employment = node.get("employmentType")
            return {
                "company": org.get("name") if isinstance(org, dict) else None,
                "role": node.get("title"),
                "location": place or None,
                "work_mode": remote,
                "pay": pay,
                "employment_type": ", ".join(employment) if isinstance(employment, list) else employment,
                "description": html_to_text(html.unescape(node.get("description") or "")) or None,
            }
    return None


def _meta(markup: str, name: str) -> str | None:
    for pattern in (
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']*)["\']',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{re.escape(name)}["\']',
    ):
        found = re.search(pattern, markup, flags=re.I)
        if found:
            return html.unescape(found.group(1)).strip() or None
    return None


_ROLE_WORDS = re.compile(
    r"\b(intern|internship|engineer|developer|manager|analyst|scientist|designer|director|architect|consultant|specialist|"
    r"associate|lead|researcher|coordinator|administrator|officer|technician|programmer|co-?op|fellow|apprentice)\b",
    re.I,
)


def _split_title(title: str) -> tuple[str | None, str | None]:
    """Guess (role, company) from a page title like 'Role - Company', 'Company | Role' or 'Role at Company'."""
    for separator in (" at ", " - ", " | ", " – ", " — ", " @ "):
        if separator in title:
            left, right = (p.strip() for p in title.split(separator, 1))
            left_role, right_role = bool(_ROLE_WORDS.search(left)), bool(_ROLE_WORDS.search(right))
            if separator in (" at ", " @ "):
                return left, right
            if right_role and not left_role:
                return right, left
            return left, right
    return title, None


def _clean_description(text: str | None) -> str | None:
    if not text:
        return None
    value = html.unescape(text)
    return html_to_text(value) if "<" in value else value.strip() or None


def extract_posting(url: str) -> dict[str, Any]:
    """Return whatever could be read: company, role, location, work_mode, pay, employment_type, description."""
    result: dict[str, Any] = {}
    for reader in (_greenhouse, _lever):
        try:
            found = reader(url)
        except Exception:  # noqa: BLE001 - fall through to the generic reader
            found = None
        if found and found.get("role"):
            return {k: v for k, v in found.items() if v}
        if found:
            result.update({k: v for k, v in found.items() if v})  # e.g. company known, posting closed
    try:
        markup = _get(url)
    except Exception:  # noqa: BLE001 - site blocks bots / is offline: keep what the ATS API told us
        return result
    ld = _jsonld(markup)
    if ld:
        for key, value in ld.items():
            if value and not result.get(key):
                result[key] = value
    if not result.get("role"):
        title = _meta(markup, "og:title")
        if not title:
            tag = re.search(r"<title[^>]*>(.*?)</title>", markup, flags=re.S | re.I)
            title = html.unescape(tag.group(1)).strip() if tag else None
        if title:
            role, company = _split_title(title)
            known = str(result.get("company") or "").lower()
            if role and role.lower() != known:  # a board home page titled with the company is not a role
                result["role"] = role
            if company and not result.get("company"):
                result["company"] = company
            if role and not known and not company and not _ROLE_WORDS.search(role):
                result.setdefault("company", role)
                result.pop("role", None)
    if not result.get("company"):
        site = _meta(markup, "og:site_name")
        if site:
            result["company"] = site
    if not result.get("description"):
        description = _meta(markup, "og:description") or _meta(markup, "description")
        if description and result.get("role"):
            result["description"] = description
    result["description"] = _clean_description(result.get("description"))
    result["page_text"] = html_to_text(markup)[:_PAGE_TEXT_CAP]  # raw material for the Haiku cleaner, never stored
    return {k: v for k, v in result.items() if v}

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, quote_plus, urlsplit, urlunsplit

_TRACKING = re.compile(
    r"^(?:utm_[a-z0-9_]+|gh_src|gh_jid_src|source|src|ref|referrer|lever-source|lever-origin|trk|trackingid|fbclid|gclid|mc_cid|mc_eid|_hsenc|_hsmi)$",
    re.I,
)


_SHARED_BOARD_HOSTS = frozenset(
    {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards.eu.greenhouse.io",
        "job-boards.eu.greenhouse.io",
        "jobs.ashbyhq.com",
        "jobs.lever.co",
        "jobs.eu.lever.co",
        "jobs.smartrecruiters.com",
        "careers.smartrecruiters.com",
        "apply.workable.com",
    }
)
_PACING_TENANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", re.ASCII)


def pacing_hostname(url: str, tenant: str | None = None) -> str:
    """Normalize a URL or stored pacing key; only known shared boards get a tenant.

    URL tenants must come from the board adapter, never from an arbitrary path.
    Stored keys have no scheme and can be normalized again without losing their tenant.
    Invalid tenants fall back to the host; never truncate or decode untrusted text.
    """
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", url):
        host = (urlsplit(url).hostname or "").lower()
    else:
        authority, _, key_tenant = url.partition("/")
        if authority.count(":") > 1 and "@" not in authority and "[" not in authority:
            authority = f"[{authority}]"  # stored bare IPv6 hostname
        host = (urlsplit(f"//{authority}").hostname or "").lower()
        if tenant is None:
            tenant = key_tenant or None
    shared = host in _SHARED_BOARD_HOSTS or host == "myworkdaysite.com" or host.endswith(".myworkdaysite.com")
    if shared and tenant and _PACING_TENANT.fullmatch(tenant):
        return f"{host}/{tenant.lower()}"
    return host


def canonicalize_url(raw: str) -> str:
    """Lowercase host, drop fragment + tracking params + default port, sort params, trim trailing slash."""
    s = urlsplit(raw.strip())
    netloc = (s.hostname or "").lower()
    if ":" in netloc:
        netloc = f"[{netloc}]"  # IPv6 literal
    try:
        port = s.port
    except ValueError as error:  # "https://x.com:99999/": reject cleanly instead of a stack trace
        raise ValueError("the link has an invalid port") from error
    if port and not ((s.scheme == "https" and port == 443) or (s.scheme == "http" and port == 80)):
        netloc += f":{port}"
    keep = sorted((k, v) for k, v in parse_qsl(s.query, keep_blank_values=True) if not _TRACKING.match(k))
    query = "&".join(f"{quote_plus(k)}={quote_plus(v)}" for k, v in keep)
    path = s.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return urlunsplit((s.scheme, netloc, path, query, ""))


_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|gmbh|sa|plc)\b\.?")


def norm(s: str | None) -> str:
    t = unicodedata.normalize("NFKD", s or "").lower()
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = _SUFFIX.sub("", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def slug(s: str, max_len: int = 60) -> str:
    v = re.sub(r"\s+", "-", norm(s))[:max_len].rstrip("-")
    if v:
        return v
    raw = (s or "").strip()
    # A name with no Latin letters (华为, Яндекс) must not collapse into one shared "unknown" company.
    return f"x-{hashlib.sha256(raw.encode()).hexdigest()[:12]}" if raw and re.search(r"\w", raw) else "unknown"

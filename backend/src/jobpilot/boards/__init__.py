from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..domain.canonical import canonicalize_url

CONFIRM_COMMON = [
    "thank you for appl",
    "application (has been )?(received|submitted)",
    "we('| ha)ve received your application",
    "successfully submitted",
]


@dataclass(frozen=True)
class BoardMatch:
    canonical_url: str
    tenant: str | None = None
    requisition_id: str | None = None


@dataclass(frozen=True)
class BoardAdapter:
    id: str
    display_name: str
    automation: str  # auto | assisted | manual  (platforms that forbid bots default to manual/assisted)
    matches: Callable[[str], bool]
    canonicalize: Callable[[str], BoardMatch]
    confirmation_patterns: list[str] = field(default_factory=lambda: list(CONFIRM_COMMON))
    max_automation: str = "auto"  # the highest policy the owner may set: a board whose wizard is review-only caps at assisted
    entry: Callable[[str], str] | None = None  # where the agent starts when the stored link is not the right door


def _host_in(*needles: str) -> Callable[[str], bool]:
    def f(url: str) -> bool:
        h = (urlsplit(url).hostname or "").lower()
        return any(h == n or h.endswith("." + n) for n in needles)

    return f


def _segs(url: str) -> list[str]:
    return [s for s in urlsplit(url).path.split("/") if s]


def _q(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def _first(url: str, key: str) -> str | None:
    v = _q(url).get(key)
    return v[0] if v else None


def _sub(url: str) -> str:
    return (urlsplit(url).hostname or "").split(".")[0]


def _origin(url: str) -> str:
    """scheme://host[:port], lower-cased and without userinfo, so a URL never stores credentials and case cannot fork ids."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    return f"{parts.scheme.lower()}://{host}{port}"


def _greenhouse(url: str) -> BoardMatch:
    s = _segs(url)
    path_id = s[s.index("jobs") + 1] if "jobs" in s and s.index("jobs") + 1 < len(s) else None
    jid = path_id or _first(url, "gh_jid") or _first(url, "token")  # the path names the posting; params are secondary
    embed = s[-2:] == ["embed", "job_app"]
    tenant = _first(url, "for") or (None if embed else (s[0] if s else None))
    if embed and jid and jid.isdigit() and tenant:  # the standalone embedded application form
        return BoardMatch(f"{_origin(url)}/embed/job_app?for={tenant}&token={jid}", tenant, jid)
    if jid and jid.isdigit() and tenant and "jobs" in s:
        return BoardMatch(f"{_origin(url)}/{tenant}/jobs/{jid}", tenant, jid)
    return BoardMatch(canonicalize_url(url), tenant, jid)


def _two_seg() -> Callable[[str], BoardMatch]:
    def f(url: str) -> BoardMatch:
        s = _segs(url)
        if len(s) >= 2:
            return BoardMatch(f"{_origin(url)}/{s[0]}/{s[1]}", s[0], s[1])
        return BoardMatch(canonicalize_url(url), s[0] if s else None)

    return f


def _workday(url: str) -> BoardMatch:
    path = urlsplit(url).path
    m = re.search(r"_([A-Za-z0-9-]+)(?:/apply.*)?$", path)
    tenant = _sub(url)
    site = re.search(r"/recruiting/([^/]+)/", path)  # myworkdaysite.com: the tenant is in the path, the host label is a shard
    if site:
        tenant = site.group(1)
    return BoardMatch(re.sub(r"/apply.*$", "", canonicalize_url(url)), tenant, m.group(1) if m else None)


def _eightfold(url: str) -> BoardMatch:
    p = urlsplit(url).path
    m = re.search(r"/job/(\d+)", p) or re.search(r"positions/(\d+)", p)
    return BoardMatch(canonicalize_url(url), _sub(url), m.group(1) if m else _first(url, "pid"))


def _smartrecruiters(url: str) -> BoardMatch:
    s = _segs(url)
    return BoardMatch(canonicalize_url(url), s[0] if s else None, s[1].split("-")[0] if len(s) > 1 else None)


def _icims(url: str) -> BoardMatch:
    m = re.search(r"/jobs/(\d+)", urlsplit(url).path)
    return BoardMatch(canonicalize_url(url), _sub(url), m.group(1) if m else None)


def _taleo(url: str) -> BoardMatch:
    return BoardMatch(canonicalize_url(url), _sub(url), _first(url, "job") or _first(url, "jobId"))


def _sf(url: str) -> BoardMatch:
    return BoardMatch(canonicalize_url(url), _sub(url), _first(url, "career_job_req_id"))


def _workable(url: str) -> BoardMatch:
    s = _segs(url)
    jid = s[s.index("j") + 1] if "j" in s and s.index("j") + 1 < len(s) else None
    return BoardMatch(canonicalize_url(url), None if (s and s[0] == "j") else (s[0] if s else None), jid)


def _bamboo(url: str) -> BoardMatch:
    m = re.search(r"/careers/(\d+)", urlsplit(url).path)
    return BoardMatch(canonicalize_url(url), _sub(url), m.group(1) if m else _first(url, "id"))


def _linkedin(url: str) -> BoardMatch:
    m = re.search(r"/jobs/view/(?:[^/?#]*-)?(\d{6,})", urlsplit(url).path)
    jid = m.group(1) if m else _first(url, "currentJobId")
    return BoardMatch(f"https://www.linkedin.com/jobs/view/{jid}" if jid else canonicalize_url(url), None, jid)


def _indeed(url: str) -> BoardMatch:
    jk = _first(url, "jk") or _first(url, "vjk")
    return BoardMatch(f"https://www.indeed.com/viewjob?jk={jk}" if jk else canonicalize_url(url), None, jk)


def _handshake(url: str) -> BoardMatch:
    m = re.search(r"/jobs/(\d+)", urlsplit(url).path)
    return BoardMatch(canonicalize_url(url), None, m.group(1) if m else None)


def _workday_entry(link: str) -> str:
    """The manual wizard, never "Autofill with Resume" (it re-parses the CV and overwrites fields)."""
    if "/apply" in urlsplit(link).path:
        return link
    return f"{link.split('?')[0].rstrip('/')}/apply/applyManually"


ADAPTERS: list[BoardAdapter] = [
    BoardAdapter("greenhouse", "Greenhouse", "auto", _host_in("greenhouse.io"), _greenhouse),
    BoardAdapter("lever", "Lever", "auto", _host_in("lever.co"), _two_seg()),
    BoardAdapter("ashby", "Ashby", "auto", _host_in("ashbyhq.com"), _two_seg()),
    BoardAdapter(
        "workday", "Workday", "manual", _host_in("myworkdayjobs.com", "myworkdaysite.com"), _workday, entry=_workday_entry
    ),
    BoardAdapter("eightfold", "Eightfold", "auto", _host_in("eightfold.ai"), _eightfold),
    BoardAdapter("smartrecruiters", "SmartRecruiters", "auto", _host_in("smartrecruiters.com"), _smartrecruiters),
    BoardAdapter("icims", "iCIMS", "assisted", _host_in("icims.com"), _icims),
    BoardAdapter("taleo", "Taleo", "assisted", _host_in("taleo.net"), _taleo),
    BoardAdapter(
        "successfactors",
        "SAP SuccessFactors",
        "assisted",
        _host_in("successfactors.com", "successfactors.eu", "jobs.sap.com"),
        _sf,
    ),
    BoardAdapter("workable", "Workable", "auto", _host_in("workable.com"), _workable),
    BoardAdapter("bamboohr", "BambooHR", "auto", _host_in("bamboohr.com"), _bamboo),
    # Platforms that prohibit bots: ingest + prepare only; a human clicks. (Lesson from AIHawk.)
    BoardAdapter("linkedin", "LinkedIn", "manual", _host_in("linkedin.com"), _linkedin),
    BoardAdapter("indeed", "Indeed", "manual", _host_in("indeed.com"), _indeed),
    BoardAdapter("handshake", "Handshake", "assisted", _host_in("joinhandshake.com"), _handshake),
]

CUSTOM = BoardAdapter(
    "custom",
    "Company site",
    "assisted",
    lambda _u: True,
    lambda u: BoardMatch(canonicalize_url(u), re.sub(r"^www\.", "", urlsplit(u).hostname or "")),
)


def adapter_by_id(board_id: str | None) -> BoardAdapter:
    return next((a for a in ADAPTERS if a.id == board_id), CUSTOM)


def resolve_board(url: str) -> tuple[BoardAdapter, BoardMatch]:
    scheme = urlsplit(url).scheme
    if scheme not in ("http", "https"):
        raise ValueError("only http(s) URLs are accepted")
    adapter = next((a for a in ADAPTERS if a.matches(url)), CUSTOM)
    return adapter, adapter.canonicalize(url)


def entry_url(board_id: str | None, link: str) -> str:
    """Where the agent should start: the board's own entry hook, else the stored link."""
    entry = adapter_by_id(board_id).entry
    return entry(link) if entry else link


MAX_RECIPE_CHARS = 2000


def recipe_text(agent_dir: Path, board_id: str | None) -> str:
    """The board's short recipe (agent/boards/<id>.md) after the shared one (_common.md), or "" when absent. Repository
    files, trusted like NEO.md; the id is validated against the known adapters so it can never name another path."""
    known = {a.id for a in ADAPTERS} | {CUSTOM.id}
    parts = []
    for name in ("_common", board_id if board_id in known else None):
        if not name:
            continue
        try:
            parts.append((agent_dir / "boards" / f"{name}.md").read_text(encoding="utf-8").strip())
        except OSError:
            continue
    return "\n".join(parts)[:MAX_RECIPE_CHARS]


POLICY_RANK = {"manual": 0, "assisted": 1, "auto": 2}


def cap_policy(board_id: str, policy: str) -> str:
    """`policy` limited to what the board allows (Workday: never auto, so its submits are always the owner's)."""
    ceiling = adapter_by_id(board_id).max_automation
    return ceiling if POLICY_RANK.get(policy, 0) > POLICY_RANK[ceiling] else policy


def exceeds_ceiling(board_id: str, policy: str) -> bool:
    return POLICY_RANK.get(policy, 0) > POLICY_RANK[adapter_by_id(board_id).max_automation]

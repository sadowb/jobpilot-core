"""BrowserOS neo, the only job browser: tab and page operations carried over MCP.

Neo is a separate application (the owner's dedicated agent browser), so there is no browser to supervise, no debug port
and no per-OS launcher: the server only needs a reachable MCP endpoint (`NEO_MCP_URL`). Everything Neo returns about a
page is untrusted data: it arrives wrapped in `[UNTRUSTED_PAGE_CONTENT nonce=… origin=…]` markers; the unwrapping here
checks the per-response nonce, so page text cannot forge an early end marker.

Neo ids pages by small integers that are only meaningful while the tab is open, so "target ids" here are those integers
as strings. The engine never enumerates-and-closes tabs it did not open (`close_orphans` is a no-op): Neo is shared
with the owner and other agents.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from ..config import Settings

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
MAX_RESPONSE = 16 * 1024 * 1024  # a full-page PNG arrives base64-encoded inside the JSON
# Loopback, or the name Docker gives the host. Never an arbitrary host: the endpoint receives page-bound commands.
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "host.docker.internal"})
CLIENT_NAME = "jobpilot"
SESSION_LABEL = "jobpilot server"

_WRAPPED = re.compile(
    r"\[UNTRUSTED_PAGE_CONTENT nonce=(?P<nonce>[0-9a-f]+) origin=(?P<origin>[^\]\n]*)\][^\n]*\n(?P<body>.*?)\n?"
    r"\[END_UNTRUSTED_PAGE_CONTENT nonce=(?P=nonce)\]",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class Tab:
    target_id: str
    url: str
    title: str = ""  # untrusted page data: display only


@dataclass(frozen=True, slots=True)
class TabGroup:
    id: str
    title: str
    color: str
    pages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PageState:
    url: str
    ready: str
    text: str
    title: str = ""


@dataclass(frozen=True, slots=True)
class PageCapture:
    """What a tab shows right now; every field is untrusted page data. `png` is None when Neo refused a screenshot."""

    url: str
    title: str
    text: str
    png: bytes | None


class BrowserUnavailable(RuntimeError):
    """Neo is not reachable, or its reply is unusable."""


class BrowserError(RuntimeError):
    """Neo reported an error for a tool call; its text is only Neo's message."""


_OPENED = re.compile(r"^opened page (\d+)\b")
_TAB_LINE = re.compile(r"^\[(\d+)\]\s+(\S+)")
_TAB_TITLE = re.compile(r"\s\((.*)\)(?:\s+\[group [^\]]*\])?\s*$")
_GROUP_LINE = re.compile(r'^\[([^\]]+)\]\s+"(.*)"\s+\((\w+)\)\s+pages:\s*(.*)$')


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None  # the endpoint is fixed; a redirect could only be an attempt to leave it


@dataclass(frozen=True, slots=True)
class ToolResult:
    text: list[str]
    images: list[bytes]
    is_error: bool


def validate_endpoint(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in ALLOWED_HOSTS or parts.username or parts.password:
        raise ValueError("NEO_MCP_URL must be http://<loopback or host.docker.internal>[:port]/path")
    if parts.query or parts.fragment:
        raise ValueError("NEO_MCP_URL must not carry a query or fragment")
    return url


def unwrap(text: str) -> tuple[str, str]:
    """(origin, body) of a Neo page-content block; the whole text with an empty origin when it is not wrapped."""
    found = _WRAPPED.search(text)
    if not found:
        return "", text
    return found.group("origin"), found.group("body")


class McpHttp:
    """The small part of MCP streamable HTTP this application needs: initialize, then tools/call, one request at a time."""

    def __init__(self, url: str, *, timeout: float = 30.0) -> None:
        self.url = validate_endpoint(url)
        self.timeout = timeout
        self._session: str | None = None
        self._next_id = 0
        self._lock = threading.RLock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects())

    def close(self) -> None:
        with self._lock:
            session, self._session = self._session, None
            if session:
                try:
                    self._send(None, None, session=session, method="DELETE")
                except (BrowserUnavailable, BrowserError, _SessionLost):
                    pass  # the session ends with the connection either way

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        with self._lock:
            for attempt in (1, 2):
                if self._session is None:
                    self._initialize()
                try:
                    reply = self._rpc("tools/call", {"name": name, "arguments": arguments})
                    break
                except _SessionLost:
                    self._session = None
                    if attempt == 2:
                        raise BrowserUnavailable("Neo session could not be re-established") from None
        result = reply.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("content"), list):
            raise BrowserUnavailable("Invalid Neo tool result")
        texts: list[str] = []
        images: list[bytes] = []
        for part in result["content"]:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif part.get("type") == "image" and isinstance(part.get("data"), str):
                try:
                    images.append(base64.b64decode(part["data"], validate=True))
                except (binascii.Error, ValueError):
                    continue
        return ToolResult(texts, images, result.get("isError") is True)

    def _initialize(self) -> None:
        self._session = None
        reply = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": "1"},
            },
        )
        if not isinstance(reply.get("result"), dict):
            raise BrowserUnavailable("Invalid Neo initialize reply")
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"}, None, session=self._session)

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        raw, session = self._send(body, request_id, session=self._session)
        if method == "initialize":
            self._session = session
        reply = _find_reply(raw, request_id)
        error = reply.get("error")
        if isinstance(error, dict):
            raise BrowserError(str(error.get("message", "Neo error"))[:200])
        return reply

    def _send(
        self, body: dict[str, Any] | None, request_id: int | None, *, session: str | None, method: str = "POST"
    ) -> tuple[str, str | None]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if session:
            headers["Mcp-Session-Id"] = session
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url, data=data, headers=headers, method=method)  # noqa: S310 - validated loopback
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
                new_session = response.headers.get("mcp-session-id")
        except urllib.error.HTTPError as error:
            if error.code in (400, 404) and session:
                raise _SessionLost from error
            raise BrowserUnavailable(f"Neo MCP request failed ({error.code})") from error
        except (OSError, ValueError) as error:
            raise BrowserUnavailable("Neo MCP is not reachable (is BrowserOS neo running?)") from error
        if len(raw) > MAX_RESPONSE:
            raise BrowserUnavailable("Neo MCP response is too large")
        return raw.decode("utf-8", errors="replace"), new_session or session


class _SessionLost(Exception):
    """The server no longer knows our session id (Neo restarted or expired it)."""


def _find_reply(raw: str, request_id: int | None) -> dict[str, Any]:
    if request_id is None:
        return {}
    candidates = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")] or [raw]
    for candidate in candidates:
        if not candidate.startswith("{"):
            continue
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("id") == request_id:
            return value
    raise BrowserUnavailable("Neo MCP gave no reply")


_FORM_AUDIT_JS = r"""() => {
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const clean = (t) => String(t || '').replace(/\s+/g, ' ').trim().slice(0, 140);
  const label = (e) => { let t = '';
    if (e.id) { const l = document.querySelector('label[for="' + CSS.escape(e.id) + '"]'); if (l) t = l.innerText; }
    if (!t) { const l = e.closest('label'); if (l) t = l.innerText; }
    return clean(t || e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.name || e.id); };
  const empty = [], emails = [], radios = {};
  for (const e of document.querySelectorAll('input,textarea,select')) {
    const type = (e.type || '').toLowerCase();
    if (['hidden', 'file', 'submit', 'button', 'image', 'reset'].includes(type) || !vis(e)) continue;
    if (e.getAttribute('role') === 'combobox' || e.getAttribute('aria-haspopup') || e.getAttribute('aria-autocomplete')) continue;
    const req = e.required || e.getAttribute('aria-required') === 'true';
    const value = (e.value || '').trim();
    const ident = (label(e) + ' ' + (e.name || '') + ' ' + (e.id || '') + ' ' + (e.autocomplete || '')).toLowerCase();
    if (type === 'email' || /e-?mail/.test(ident)) { if (value) emails.push(clean(value)); }
    if (type === 'radio') { const g = radios[e.name || e.id] || (radios[e.name || e.id] = {label: label(e), req: false, on: false});
      g.req = g.req || req; g.on = g.on || e.checked; continue; }
    if (type === 'checkbox') { if (req && !e.checked) empty.push(label(e)); continue; }
    if (req && !value) empty.push(label(e));
  }
  for (const g of Object.values(radios)) if (g.req && !g.on) empty.push(g.label);
  const errors = [...document.querySelectorAll('[aria-invalid="true"],[role="alert"]')].filter(vis)
    .map((e) => clean(e.innerText || label(e))).filter(Boolean);
  return JSON.stringify({empty: empty.slice(0, 30), errors: [...new Set(errors)].slice(0, 20), emails: [...new Set(emails)].slice(0, 10)});
}"""


class NeoBrowser:
    """Tab and page operations over Neo's MCP tools."""

    def __init__(self, url: str, *, timeout: float = 30.0, client: McpHttp | None = None) -> None:
        self.client = client or McpHttp(url, timeout=timeout)
        self._named = False

    # ---- plumbing -------------------------------------------------------------------------------------------------
    def _tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if not self._named and name != "name_session":
            self._named = True  # one best-effort label so the owner sees whose tabs these are in the Neo cockpit
            try:
                self.client.call(
                    "name_session",
                    {"name": "job applications", "category": "other", "summary": "JobPilot server tab bookkeeping"},
                )
            except (BrowserError, BrowserUnavailable):
                pass
        result = self.client.call(name, arguments)
        if result.is_error:
            raise BrowserError((result.text[0] if result.text else f"Neo {name} failed")[:200])
        return result

    def _body(self, result: ToolResult) -> str:
        for text in result.text:
            origin, body = unwrap(text)
            if origin or text.startswith("[UNTRUSTED_PAGE_CONTENT"):
                return body
        return result.text[0] if result.text else ""

    def _page(self, target_id: str) -> int:
        if not target_id.isdigit():
            raise BrowserError("invalid Neo page id")
        return int(target_id)

    # ---- operations --------------------------------------------------------------------------
    def alive(self) -> bool:
        try:
            self._tool("tabs", {"action": "active"})
            return True
        except (BrowserError, BrowserUnavailable):
            return False

    def open_tab(self, url: str = "about:blank") -> Tab:
        result = self._tool("tabs", {"action": "new", "url": url})
        for text in result.text:
            opened = _OPENED.match(text.strip())
            if opened:
                log.info("opened Neo page=%s", opened.group(1))
                return Tab(opened.group(1), url)
        raise BrowserUnavailable("Neo did not report the opened page")

    def tabs(self) -> list[Tab]:
        listed = self._tool("tabs", {"action": "list"})
        tabs: list[Tab] = []
        for text in listed.text:
            for line in text.splitlines():
                found = _TAB_LINE.match(line.strip())
                if found:
                    title = _TAB_TITLE.search(line.strip())
                    tabs.append(Tab(found.group(1), found.group(2), title.group(1) if title else ""))
        return tabs

    def tab_groups(self) -> list[TabGroup]:
        groups: list[TabGroup] = []
        for text in self._tool("tab_groups", {"action": "list"}).text:
            for line in text.splitlines():
                found = _GROUP_LINE.match(line.strip())
                if found:
                    pages = tuple(p for p in re.split(r"[,\s]+", found.group(4).strip()) if p.isdigit())
                    groups.append(TabGroup(found.group(1), found.group(2), found.group(3), pages))
        return groups

    def expand_group(self, group_id: str) -> None:
        """Make a (possibly collapsed) tab group's tabs visible in the strip. Never collapses anything."""
        self._tool("tab_groups", {"action": "update", "groupId": group_id, "collapsed": False})

    def activate(self, target_id: str) -> bool:
        """Bring a page to the front through the `run` SDK's raw CDP escape hatch. False when Neo refuses or the call
        has no effect we can see; the caller then tells the owner where the page is."""
        page = self._page(target_id)
        try:
            result = self._tool(
                "run", {"code": f"await browser.page({page}).cdp('Page.bringToFront', '{{}}'); return 'activated';"}
            )
        except BrowserError:
            return False
        return any("activated" in text for text in result.text)

    def page_text(self, target_id: str) -> str:
        return self._body(self._tool("read", {"page": self._page(target_id), "format": "text"}))[:100_000]

    def _evaluate_json(self, target_id: str, function: str) -> dict[str, Any]:
        result = self._tool("evaluate", {"page": self._page(target_id), "func": function})
        try:
            data = json.loads(self._body(result))
        except ValueError as error:
            raise BrowserUnavailable("Invalid Neo page data") from error
        if not isinstance(data, dict):
            raise BrowserUnavailable("Invalid Neo page data")
        return data

    def page_state(self, target_id: str, limit: int = 200_000) -> PageState:
        data = self._evaluate_json(
            target_id,
            "() => JSON.stringify({url: location.href, ready: document.readyState, title: document.title || '', "
            f"text: (document.body?.innerText || '').slice(0, {int(limit)})}})",
        )
        if not all(isinstance(data.get(key), str) for key in ("url", "ready", "text")):
            raise BrowserUnavailable("Invalid Neo page state")
        title = data.get("title")
        return PageState(data["url"], data["ready"], data["text"], title if isinstance(title, str) else "")

    def capture(self, target_id: str, text_limit: int = 2_000) -> PageCapture:
        """URL, title, visible-text prefix and a PNG, read-only. A screenshot Neo cannot make costs only the image."""
        data = self._evaluate_json(
            target_id,
            "() => JSON.stringify({url: location.href, title: document.title || '', "
            f"text: (document.body?.innerText || '').slice(0, {int(text_limit)})}})",
        )
        if not all(isinstance(data.get(key), str) for key in ("url", "title", "text")):
            raise BrowserUnavailable("Invalid Neo page capture")
        png: bytes | None = None
        try:
            shot = self._tool("screenshot", {"page": self._page(target_id), "format": "png"})
            png = shot.images[0] if shot.images else None
        except (BrowserError, BrowserUnavailable):
            png = None
        return PageCapture(data["url"], data["title"], data["text"], png)

    def form_audit(self, target_id: str) -> dict[str, Any]:
        """Read-only: visible required fields still empty, visible validation errors and email field values. Custom
        widgets (comboboxes, hidden inputs, file inputs) are skipped on purpose: they are not judged from the outside."""
        return self._evaluate_json(target_id, _FORM_AUDIT_JS)

    def navigate(self, target_id: str, url: str) -> None:
        """Server-side first load of a job's own http(s) link into its tab (the agent navigates itself afterwards)."""
        if urlsplit(url).scheme not in ("http", "https"):
            raise BrowserError("refusing to navigate to a non-http(s) URL")
        self._tool("navigate", {"action": "url", "page": self._page(target_id), "url": url})

    def evaluate(self, target_id: str, expression: str) -> str:
        """Run one JavaScript expression in the page and return its value as text (JSON for objects)."""
        body = f"return ({expression.strip().rstrip(';')});"
        return self._body(self._tool("evaluate", {"page": self._page(target_id), "code": body}))

    def close_tab(self, target_id: str) -> bool:
        """True when Neo closed the page, False when it no longer exists. Only tabs this server opened are ever closed."""
        try:
            result = self._tool("tabs", {"action": "close", "page": self._page(target_id)})
        except BrowserError as error:
            if re.search(r"unknown page|no such page|not found", str(error), re.IGNORECASE):
                return False
            raise
        return any(text.strip().startswith("closed page") for text in result.text)


_NEO: dict[str, NeoBrowser] = {}
_LOCK = threading.Lock()


def neo_browser(cfg: Settings) -> NeoBrowser:
    """The process-wide Neo client for the configured endpoint: one MCP session per process and endpoint."""
    with _LOCK:
        engine = _NEO.get(cfg.neo_mcp_url)
        if engine is None:
            engine = _NEO[cfg.neo_mcp_url] = NeoBrowser(cfg.neo_mcp_url)
        return engine

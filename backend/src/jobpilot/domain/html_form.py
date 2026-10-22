"""Application questions from a server-rendered HTML form (CareerPlug, JazzHR, Lever and similar).

Pure: stdlib ``html.parser`` over an already-fetched string, no I/O. The page is untrusted: the extractor builds a
bounded tree, never raises on malformed markup, and returns only labelled, visible controls. A label is resolved from
``<label for>``, a wrapping label, ``aria-label``, a fieldset legend, or the nearest caption element before the
control; a control with no readable label is dropped rather than guessed. Radios and checkboxes sharing a name become
one ``select`` question whose options are their own labels.
"""

from __future__ import annotations

import contextlib
import re
from html.parser import HTMLParser
from typing import Any

from .posting_form import MAX_LABEL, MAX_OPTIONS, MAX_QUESTIONS, PostingQuestion

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_SKIP_TEXT = {"script", "style", "noscript", "template", "option", "optgroup"}
_MAX_NODES = 60_000
_MAX_DEPTH = 80
_CAPTION_CLASS = re.compile(
    r"application-label|control-label|form-label|field-label|question-?(text|label|title)|\blabel\b", re.I
)
_HIDDEN_CLASS = re.compile(r"(^|\s)(none|hidden|hide|invisible|sr-only|visually-hidden|honeypot)(\s|$)", re.I)
_IGNORED_TYPES = {"hidden", "submit", "button", "image", "reset", "search", "password"}
_STANDARD_NAMES = re.compile(
    r"^(first|last|full|legal)?[ _-]?name$|e-?mail|phone|resume|\bcv\b|firstname|lastname|cover[ _-]?letter", re.I
)
_PLACEHOLDER_OPTION = re.compile(r"^(select|choose|please select|--|—|no answer|none|\.\.\.)", re.I)
_EEOC = re.compile(r"\b(gender|race|ethnic\w*|veteran|disabilit\w*|sexual orientation|pronouns?|age range|hispanic)\b", re.I)


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text", "index")

    def __init__(self, tag: str, attrs: dict[str, str], parent: _Node | None) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[_Node] = []
        self.parent = parent
        self.text = ""  # character data directly inside this node, only for "#text" nodes
        self.index = 0

    def classes(self) -> str:
        return self.attrs.get("class", "")


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.cur = self.root
        self.count = 0
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.count >= _MAX_NODES:
            return
        node = _Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.count += 1
        node.index = self.count
        self.cur.children.append(node)
        if tag not in _VOID:
            if self.depth >= _MAX_DEPTH:
                return
            self.cur = node
            self.depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and self.cur.tag == tag and self.cur is not self.root:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        node: _Node | None = self.cur
        while node is not None and node is not self.root:
            if node.tag == tag:
                self.cur = node.parent or self.root
                self.depth = max(0, self.depth - 1)
                return
            node = node.parent

    def handle_data(self, data: str) -> None:
        if self.count >= _MAX_NODES or not data.strip():
            return
        leaf = _Node("#text", {}, self.cur)
        leaf.text = data
        self.cur.children.append(leaf)


def _walk(node: _Node) -> Any:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _own_text(node: _Node) -> str:
    """Visible text under ``node``: scripts, option lists and controls excluded."""
    parts: list[str] = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.tag == "#text":
            parts.append(n.text)
            continue
        if n.tag in _SKIP_TEXT or n.tag in ("input", "select", "textarea"):
            continue
        stack.extend(reversed(n.children))
    return " ".join(" ".join(parts).split())


def _option_text(node: _Node) -> str:
    return " ".join(" ".join(n.text for n in _walk(node) if n.tag == "#text").split())


def _label_text(label: _Node) -> str:
    """A label's own words. Lever nests a hint inside the caption element; the caption alone is the question."""
    caption = next(
        (n for n in _walk(label) if n is not label and re.search(r"application-label", n.classes()) and not _has_control(n)),
        None,
    )
    if caption is not None:
        direct = " ".join(" ".join(c.text for c in caption.children if c.tag == "#text").split())
        inner = next((n for n in _walk(caption) if n is not caption and re.search(r"(^|\s)text(\s|$)", n.classes())), None)
        return direct or (_own_text(inner) if inner is not None else "") or _own_text(caption)
    return _own_text(label)


def _has_control(node: _Node) -> bool:
    return any(n.tag in ("input", "select", "textarea") and n.attrs.get("type") not in _IGNORED_TYPES for n in _walk(node))


def _clean_label(text: str) -> tuple[str, bool]:
    star = bool(re.search(r"[*✱]\s*$", text)) or "(required)" in text.lower()
    text = re.sub(r"\s*[*✱]+\s*$", "", text)
    text = re.sub(r"\(required\)", "", text, flags=re.I)
    return " ".join(text.split())[:MAX_LABEL], star


def _hidden(node: _Node) -> bool:
    n: _Node | None = node
    while n is not None:
        a = n.attrs
        if "hidden" in a or a.get("aria-hidden") == "true" or re.search(r"display\s*:\s*none", a.get("style", ""), re.I):
            return True
        if _HIDDEN_CLASS.search(a.get("class", "")) and n.tag not in ("html", "body"):
            return True
        n = n.parent
    return False


def _ancestor(node: _Node, tag: str) -> _Node | None:
    n = node.parent
    while n is not None:
        if n.tag == tag:
            return n
        n = n.parent
    return None


def _caption_before(node: _Node, own: set[int]) -> str:
    """Nearest caption (label/legend/heading/caption-class element) before ``node``, climbing a few levels."""
    cur: _Node | None = node
    for _ in range(5):
        parent = cur.parent if cur else None
        if parent is None:
            break
        siblings = parent.children
        pos = next((i for i, s in enumerate(siblings) if s is cur), 0)
        for sib in reversed(siblings[:pos]):
            if sib.tag == "#text":
                continue
            captionish = sib.tag in ("label", "legend", "h1", "h2", "h3", "h4", "h5", "h6", "p", "span", "div", "dt")
            if not captionish or (sib.tag in ("div", "span", "p") and not _CAPTION_CLASS.search(sib.classes())):
                # a wrapper may hold the caption (Ashby/Lever: div.application-label > div.text)
                inner = next(
                    (n for n in _walk(sib) if n is not sib and _CAPTION_CLASS.search(n.classes()) and not _has_control(n)),
                    None,
                )
                if inner is not None and id(inner) not in own:
                    text = _own_text(inner)
                    if text:
                        return text
                continue
            if _has_control(sib) or id(sib) in own:
                continue
            text = _own_text(sib)
            if text:
                return text
        cur = parent
    return ""


def parse_html_form(markup: str, provider: str, *, key_prefix: str = "html") -> tuple[PostingQuestion, ...]:
    builder = _Builder()
    with contextlib.suppress(Exception):  # malformed markup is data, never a failure
        builder.feed(markup[:3_000_000])
        builder.close()
    root = builder.root
    forms = [n for n in _walk(root) if n.tag == "form"]
    scope = max(forms, key=lambda f: sum(1 for _ in _walk(f))) if forms else root

    labels_for: dict[str, _Node] = {}
    for n in _walk(scope):
        if n.tag == "label" and n.attrs.get("for"):
            labels_for.setdefault(n.attrs["for"], n)

    controls = [
        n
        for n in _walk(scope)
        if n.tag in ("input", "select", "textarea")
        and n.attrs.get("type", "text").lower() not in _IGNORED_TYPES
        and not _hidden(n)
        and (n.attrs.get("name") or n.attrs.get("id"))
    ]
    groups: dict[str, list[_Node]] = {}
    order: list[str] = []
    for c in controls:
        kind = c.attrs.get("type", "text").lower()
        gid = (
            f"grp:{c.attrs.get('name')}"
            if c.tag == "input" and kind in ("radio", "checkbox") and c.attrs.get("name")
            else f"one:{id(c)}"
        )
        if gid not in groups:
            order.append(gid)
        groups.setdefault(gid, []).append(c)

    questions: list[PostingQuestion] = []
    seen: set[str] = set()
    for gid in order:
        members = groups[gid]
        first = members[0]
        kind = first.attrs.get("type", "text").lower() if first.tag == "input" else first.tag
        required = any("required" in m.attrs or m.attrs.get("aria-required") == "true" for m in members)
        name = first.attrs.get("name") or first.attrs.get("id") or ""
        label = ""
        options: list[str] = []
        qtype = "text"
        multiple = False

        if gid.startswith("grp:"):
            for m in members:
                wrap = _ancestor(m, "label")
                lab = labels_for.get(m.attrs.get("id", ""))
                text = _own_text(lab) if lab is not None else (_own_text(wrap) if wrap is not None else m.attrs.get("value", ""))
                text = text or m.attrs.get("value", "")
                if text and text not in options:
                    options.append(text[:200])
            fieldset = _ancestor(first, "fieldset")
            legend = next((n for n in _walk(fieldset) if n.tag == "legend"), None) if fieldset is not None else None
            label = _own_text(legend) if legend is not None else ""
            if not label:
                own = {id(_ancestor(m, "label")) for m in members if _ancestor(m, "label") is not None}
                container = _ancestor(first, "ul") or first
                label = _caption_before(container, own) or _caption_before(first, own)
            qtype = "select" if kind == "radio" or len(options) > 1 else "checkbox"
            multiple = kind == "checkbox" and len(options) > 2
            if len(members) == 1 and kind == "checkbox":
                label, options, qtype = (options[0] if options else label), [], "checkbox"
        else:
            cid = first.attrs.get("id", "")
            lab = labels_for.get(cid) if cid else None
            wrap = _ancestor(first, "label")
            if lab is not None:
                label = _label_text(lab)
            elif wrap is not None:
                label = _label_text(wrap)
            if not label:
                label = first.attrs.get("aria-label", "")
            if not label:
                label = _caption_before(first, set())
            if not label:
                label = first.attrs.get("placeholder", "")
            if first.tag == "select":
                for opt in _walk(first):
                    if opt.tag == "option":
                        text = _option_text(opt) or opt.attrs.get("value", "")
                        text = " ".join(text.split())[:200]
                        singular = " ".join(label.lower().rstrip("s").split())
                        if text and not _PLACEHOLDER_OPTION.match(text) and text.lower() != singular and text not in options:
                            options.append(text)
                qtype = "select"
                multiple = "multiple" in first.attrs
            elif first.tag == "textarea":
                qtype = "textarea"
            else:
                qtype = {"email": "email", "tel": "tel", "url": "url", "number": "number", "date": "date", "file": "file"}.get(
                    kind, "text"
                )

        label, star = _clean_label(label)
        # CareerPlug marks required on the wrapping div, JazzHR with an asterisk inside the label
        up: _Node | None = first
        for _ in range(6):
            up = up.parent if up else None
            if up is not None and re.search(r"(^|\s)required(\s|$)", up.classes()):
                required = True
                break
        required = required or star
        if not label or label.lower() in seen:
            continue
        seen.add(label.lower())
        standard = qtype == "file" or bool(_STANDARD_NAMES.search(name) or _STANDARD_NAMES.search(label))
        voluntary = qtype == "select" and bool(_EEOC.search(label))
        questions.append(
            PostingQuestion(
                key=f"{key_prefix}:{name or label}",
                label=label,
                type=qtype,
                required=required,
                options=tuple(options[:MAX_OPTIONS]) if qtype in ("select", "checkbox") else (),
                multiple=multiple,
                group="eeoc" if voluntary else ("standard" if standard else "application"),
            )
        )
        if len(questions) >= MAX_QUESTIONS:
            break
    return tuple(questions)

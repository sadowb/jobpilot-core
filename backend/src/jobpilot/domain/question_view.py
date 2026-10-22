"""What the owner is shown for a form field an agent could not answer. Pure rules, no IO.

Form labels and options come from a third-party page: they are untrusted data, so the view is bounded here
(text length, option count) and never carries selectors, credential fields or file uploads."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .answers import profile_key_for
from .canonical_answers import canonical_for

MAX_LABEL = 300
MAX_TYPE = 40
MAX_OPTIONS = 60
MAX_OPTION = 120
FALLBACK_LABEL = "Application question"
HIDDEN_TYPES = frozenset({"file", "secret"})  # never asked in the dashboard: files and credentials are not typed answers


def _options(raw: object) -> list[str]:
    """Distinct, non-empty option texts in page order, each clipped; the caller caps how many are returned."""
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return []
    seen: dict[str, None] = {}
    for item in raw:
        text = " ".join(str(item).split())[:MAX_OPTION]
        if text:
            seen.setdefault(text)
    return list(seen)


_STEP = re.compile(r"\bstep\s+(\d{1,2})\s*(?:of|/)\s*(\d{1,2})\b", re.IGNORECASE)


def step_of(note: str | None) -> str | None:
    """ "step 2 of 8" when the agent's note says where it stopped in a multi-page form, else None."""
    found = _STEP.search(note or "")
    if not found or int(found.group(1)) > int(found.group(2)):
        return None
    return f"step {int(found.group(1))} of {int(found.group(2))}"


def is_generic(label: str) -> bool:
    """A question whose answer does not depend on the posting, found by the existing answer-bank matching: a profile alias
    (start date, expected salary, ...) or a non-contextual canonical question (work authorization, sponsorship, ...).
    "How did you hear" is contextual per employer but the owner's usual source is stable, so it counts too."""
    key = profile_key_for(label)
    if key is None:
        return False
    canon = canonical_for(label)
    return canon is None or not canon.contextual or canon.key == "how_did_you_hear"


def missing_field_views(inventory: Sequence[Mapping[str, Any]] | None, missing: Sequence[str] | None) -> list[dict[str, Any]]:
    """One view per missing key that the form inventory still describes, in `missing` order.

    A key the inventory does not know (a form re-captured since the plan was made) is omitted: the caller falls back to
    the bare key rather than inventing a label. `optionsTotal` is the number of distinct options the page offered, so the
    UI can say how many are not shown; `required` / `multiple` are None when the inventory never recorded them."""
    by_key = {str(f.get("key")): f for f in inventory or []}
    views: list[dict[str, Any]] = []
    for key in missing or []:
        field = by_key.get(key)
        if field is None or field.get("type") in HIDDEN_TYPES:
            continue
        label = " ".join(str(field.get("label") or "").split())[:MAX_LABEL]
        options = _options(field.get("options"))
        if field.get("unmatchedValue"):
            options = _options(field.get("suggestions")) + [o for o in options if o not in _options(field.get("suggestions"))]
        if (
            not options and field.get("type") != "custom-select" and not field.get("unmatchedValue")
        ):  # the page showed no choices but the question is a known one: offer its fixed answers
            canon = canonical_for(label)
            options = list(canon.options) if canon else []
        views.append(
            {
                "key": key,
                "label": label or FALLBACK_LABEL,
                "type": str(field.get("type") or "text")[:MAX_TYPE],
                "options": options[:MAX_OPTIONS],
                "optionsTotal": len(options),
                "required": bool(field["required"]) if "required" in field else None,
                "multiple": bool(field["multiple"]) if "multiple" in field else None,
                "searchable": bool(field["searchable"]) if "searchable" in field else None,
                "unmatchedValue": str(field["unmatchedValue"])[:MAX_LABEL] if field.get("unmatchedValue") else None,
                "generic": is_generic(label),
            }
        )
    return views


def required_missing_keys(inventory: Sequence[Mapping[str, Any]] | None, missing: Sequence[str] | None) -> list[str]:
    """Only positively identified required fields belong in the blocking question batch."""
    required = {str(f.get("key")) for f in inventory or [] if f.get("required") is True}
    return [key for key in missing or [] if key in required]

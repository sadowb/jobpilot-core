from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from ..domain.answers import PlanItem


@dataclass
class Check:
    name: str
    ok: bool
    detail: str | None = None


def selector_for(f: dict[str, Any], value: str | None = None) -> str | None:
    if f.get("type") == "radio" and value is not None and f.get("optionSelectors"):
        return f["optionSelectors"].get(value)  # type: ignore[no-any-return]
    return f.get("selector")


_TRUE = re.compile(r"^(true|yes|1|on|checked)$", re.I)


def is_true(v: str) -> bool:
    return bool(_TRUE.match(v))


def check_confirmation(text: str, patterns: list[str]) -> Check:
    hit = any(re.search(p, text, re.I) for p in patterns)
    return Check("confirmation", hit, None if hit else "no confirmation pattern matched")


def draft_hash(
    plan: list[PlanItem],
    cv_sha: str,
    fields: list[dict[str, Any]] | None = None,
    observed: list[PlanItem] | None = None,
    revision: int = 0,
) -> str:
    stable = json.dumps(
        {
            "plan": sorted((p.key, p.value) for p in plan),
            "cv": cv_sha,
            "revision": revision,
            "inventory": sorted(fields or [], key=lambda f: (f.get("pageIndex", 0), f["key"])),
            "observed": sorted((p.key, p.value) for p in observed or []),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(stable.encode()).hexdigest()

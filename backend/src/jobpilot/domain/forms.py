"""Page identity and namespacing: no browser or persistence dependencies."""

from __future__ import annotations

import re
from typing import Any

MAX_PAGES = 20


def page_fields(fields: list[dict[str, Any]], page: int) -> list[dict[str, Any]]:
    if not 0 <= page < MAX_PAGES:
        raise ValueError("form page limit reached")
    return [{**f, "key": f"page:{page}:{f['key']}", "pageIndex": page} for f in fields]


_NOT_CV = re.compile(r"cover|letter|transcript|portfolio|writing|sample|reference", re.I)
_CV = re.compile(r"resume|r[eé]sum[eé]|\bcv\b|curriculum", re.I)

"""Split one free-text US street address into the separate fields application forms ask for."""

from __future__ import annotations

import re

_ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\s*$")


def split_us_address(text: str) -> dict[str, str]:
    """`123 Example Ave, Unit 4B, Springfield IL 62704` -> line1, line2, postal_code. Empty when it does not look like
    "street, [unit,] city ST ZIP" (nothing is guessed from a partial address)."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) < 2:
        return {}
    zip_match = _ZIP.search(parts[-1])
    if not zip_match or not re.match(r"\d", parts[0]):
        return {}
    found = {"address_line1": parts[0], "postal_code": zip_match.group(1)}
    middle = parts[1:-1]
    if re.fullmatch(r"[A-Za-z]{2}\s+\d{5}(?:-\d{4})?", parts[-1]) and middle:
        middle = middle[:-1]  # the last middle part is the city when the final part is only "ST ZIP"
    if middle:
        found["address_line2"] = ", ".join(middle)
    return found

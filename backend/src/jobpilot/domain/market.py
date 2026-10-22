"""Which CV market a job belongs to. Pure: no DB, HTTP or filesystem."""

from __future__ import annotations

import re

from .countries import mentions, normalize_country

MARKETS = ("any", "us", "morocco")
_MOROCCAN_CITIES = re.compile(
    r"\b(casablanca|rabat|marrakesh|marrakech|tangier|tanger|fes|fez|agadir|meknes|oujda|kenitra|tetouan|ifrane|sale|el jadida)\b",
    re.I,
)
# City names that are also towns elsewhere (Sale in England, Fez in the US): they only count without another country named.
_AMBIGUOUS_CITIES = re.compile(r"\b(sale|fes|fez|tanger|tangier)\b", re.I)


def market_for(location: str | None) -> str:
    """'morocco' for a Moroccan location, otherwise 'us' (US, remote, or anywhere else uses the US CV)."""
    text = (location or "").strip()
    if not text:
        return "us"
    named = mentions(text)
    last = normalize_country(text.split(",")[-1].strip())
    if "Morocco" in named or last == "Morocco":
        return "morocco"
    city = _MOROCCAN_CITIES.search(text)
    if not city or named:  # another country is named: "Rabat, Malta", "Sale, United Kingdom"
        return "us"
    if _AMBIGUOUS_CITIES.fullmatch(city.group(0)) and "," in text:
        return "us"  # "Sale, Greater Manchester": a region follows and it is not Morocco
    return "morocco"


def market_matches(track_market: str, job_market: str) -> bool:
    return track_market in ("any", job_market)

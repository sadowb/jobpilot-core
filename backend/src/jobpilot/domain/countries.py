"""Country normalisation: every country the owner or a form mentions maps to one canonical English name."""

from __future__ import annotations

import re

from .canonical import norm

COUNTRIES: tuple[str, ...] = tuple(
    n.strip()
    for n in """Afghanistan|Albania|Algeria|Andorra|Angola|Antigua and Barbuda|Argentina|Armenia|Australia|Austria|Azerbaijan|Bahamas|Bahrain|Bangladesh|Barbados|Belarus|Belgium|Belize|Benin|Bhutan|Bolivia|Bosnia and Herzegovina|Botswana|Brazil|Brunei|Bulgaria|Burkina Faso|Burundi|Cabo Verde|Cambodia|Cameroon|Canada|Central African Republic|Chad|Chile|China|Colombia|Comoros|Congo|Costa Rica|Cote d'Ivoire|Croatia|Cuba|Cyprus|Czechia|Democratic Republic of the Congo|Denmark|Djibouti|Dominica|Dominican Republic|Ecuador|Egypt|El Salvador|Equatorial Guinea|Eritrea|Estonia|Eswatini|Ethiopia|Fiji|Finland|France|Gabon|Gambia|Georgia|Germany|Ghana|Greece|Grenada|Guatemala|Guinea|Guinea-Bissau|Guyana|Haiti|Honduras|Hong Kong|Hungary|Iceland|India|Indonesia|Iran|Iraq|Ireland|Israel|Italy|Jamaica|Japan|Jordan|Kazakhstan|Kenya|Kiribati|Kosovo|Kuwait|Kyrgyzstan|Laos|Latvia|Lebanon|Lesotho|Liberia|Libya|Liechtenstein|Lithuania|Luxembourg|Madagascar|Malawi|Malaysia|Maldives|Mali|Malta|Marshall Islands|Mauritania|Mauritius|Mexico|Micronesia|Moldova|Monaco|Mongolia|Montenegro|Morocco|Mozambique|Myanmar|Namibia|Nauru|Nepal|Netherlands|New Zealand|Nicaragua|Niger|Nigeria|North Korea|North Macedonia|Norway|Oman|Pakistan|Palau|Palestine|Panama|Papua New Guinea|Paraguay|Peru|Philippines|Poland|Portugal|Puerto Rico|Qatar|Romania|Russia|Rwanda|Saint Kitts and Nevis|Saint Lucia|Saint Vincent and the Grenadines|Samoa|San Marino|Sao Tome and Principe|Saudi Arabia|Senegal|Serbia|Seychelles|Sierra Leone|Singapore|Slovakia|Slovenia|Solomon Islands|Somalia|South Africa|South Korea|South Sudan|Spain|Sri Lanka|Sudan|Suriname|Sweden|Switzerland|Syria|Taiwan|Tajikistan|Tanzania|Thailand|Timor-Leste|Togo|Tonga|Trinidad and Tobago|Tunisia|Turkey|Turkmenistan|Tuvalu|Uganda|Ukraine|United Arab Emirates|United Kingdom|United States|Uruguay|Uzbekistan|Vanuatu|Vatican City|Venezuela|Vietnam|Yemen|Zambia|Zimbabwe""".split("|")
)  # fmt: skip

_ALIASES = {
    "usa": "United States", "us": "United States", "u s": "United States", "u s a": "United States", "america": "United States",
    "united states of america": "United States", "the united states": "United States", "american": "United States",
    "uk": "United Kingdom", "u k": "United Kingdom", "great britain": "United Kingdom", "britain": "United Kingdom", "england": "United Kingdom",
    "scotland": "United Kingdom", "wales": "United Kingdom", "northern ireland": "United Kingdom", "british": "United Kingdom",
    "uae": "United Arab Emirates", "emirates": "United Arab Emirates", "korea": "South Korea", "republic of korea": "South Korea",
    "russian federation": "Russia", "czech republic": "Czechia", "ivory coast": "Cote d'Ivoire", "cote divoire": "Cote d'Ivoire",
    "maroc": "Morocco", "moroccan": "Morocco", "kingdom of morocco": "Morocco", "holland": "Netherlands", "the netherlands": "Netherlands",
    "turkiye": "Turkey", "burma": "Myanmar", "vietnam": "Vietnam", "viet nam": "Vietnam", "swaziland": "Eswatini", "cape verde": "Cabo Verde",
    "east timor": "Timor-Leste", "macedonia": "North Macedonia", "drc": "Democratic Republic of the Congo", "dr congo": "Democratic Republic of the Congo",
    "republic of the congo": "Congo", "people s republic of china": "China", "prc": "China", "hong kong sar": "Hong Kong",
    "french": "France", "german": "Germany", "spanish": "Spain", "italian": "Italy", "canadian": "Canada", "indian": "India",
    "chinese": "China", "japanese": "Japan", "brazilian": "Brazil", "mexican": "Mexico", "egyptian": "Egypt", "tunisian": "Tunisia",
    "algerian": "Algeria", "portuguese": "Portugal", "dutch": "Netherlands", "irish": "Ireland", "australian": "Australia",
}  # fmt: skip
_CANON = {norm(c): c for c in COUNTRIES}
_LOOKUP = {**_CANON, **_ALIASES}


def normalize_country(text: str | None) -> str | None:
    """Canonical country name for any common spelling, or None when it is not recognisably a country."""
    key = norm(text or "")
    if not key:
        return None
    if key in _LOOKUP:
        return _LOOKUP[key]
    key = re.sub(r"^(the|republic of|kingdom of) ", "", key)
    return _LOOKUP.get(key)


def mentions(text: str) -> list[str]:
    """Countries named inside a sentence (longest names first, so 'South Africa' is not 'Africa')."""
    haystack = f" {norm(text)} "
    found: list[str] = []
    for name in sorted(_LOOKUP, key=len, reverse=True):
        if len(name) < 3 and name != "uk":
            continue  # "us"/"u s" are only the United States when written as US / U.S. (below), never the word "us"
        if f" {name} " in haystack:
            # consume the match, so "North Korea" does not also count as "Korea" nor "Northern Ireland" as "Ireland"
            haystack = haystack.replace(f" {name} ", "  ")
            if _LOOKUP[name] not in found:
                found.append(_LOOKUP[name])
    if re.search(r"\bU\.S\.?(A\.?)?(?!\w)|\bUSA\b|\bUS\b", text) and "United States" not in found:
        found.append("United States")
    return found


# Phone pickers show only the dial code once a country is chosen. Finite on purpose: a country not listed here
# cannot be verified from "+NN" alone, so verification fails safe and the owner reviews it.
DIAL_CODES: dict[str, str] = {
    "United States": "+1", "Canada": "+1", "United Kingdom": "+44", "Morocco": "+212", "France": "+33", "Germany": "+49",
    "Spain": "+34", "Italy": "+39", "Netherlands": "+31", "Belgium": "+32", "Switzerland": "+41", "Ireland": "+353",
    "Portugal": "+351", "Sweden": "+46", "Norway": "+47", "Denmark": "+45", "Finland": "+358", "Poland": "+48",
    "Turkey": "+90", "Egypt": "+20", "Algeria": "+213", "Tunisia": "+216", "United Arab Emirates": "+971",
    "Saudi Arabia": "+966", "India": "+91", "Pakistan": "+92", "China": "+86", "Japan": "+81", "South Korea": "+82",
    "Australia": "+61", "New Zealand": "+64", "Brazil": "+55", "Mexico": "+52", "South Africa": "+27", "Nigeria": "+234",
    "Singapore": "+65", "Israel": "+972", "Russia": "+7",
}  # fmt: skip

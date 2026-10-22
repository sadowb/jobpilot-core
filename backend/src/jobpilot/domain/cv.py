from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

CV_FILENAME = "Abdelhadi Marjane.pdf"
TEX_FILENAME = "main.tex"


def sha256(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def cv_object_hash(tex: bytes, pdf: bytes) -> str:
    """Content address of a CV object = hash(tex + NUL + pdf). Same content => same object."""
    return hashlib.sha256(tex + b"\0" + pdf).hexdigest()


_REGION = re.compile(r"^[ \t]*% BEGIN TAILOR:([a-z0-9_-]+)[ \t]*\n(.*?)^[ \t]*% END TAILOR:\1[ \t]*$", re.S | re.M)


def tailor_regions(tex: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _REGION.finditer(tex)}


def replace_regions(tex: str, edits: dict[str, str]) -> str:
    known = tailor_regions(tex)
    for k in edits:
        if k not in known:
            raise ValueError(f"unknown tailor region: {k}")
        if re.search(r"\\(?:input|include|openin|read|catcode|csname)\b", edits[k]):
            raise ValueError(f"unsafe TeX command in tailor region: {k}")

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in edits:
            return m.group(0)
        return f"% BEGIN TAILOR:{name}\n{edits[name].rstrip()}\n% END TAILOR:{name}"

    return _REGION.sub(sub, tex)


def outside_regions_unchanged(before: str, after: str) -> bool:
    def strip(t: str) -> str:
        return _REGION.sub(lambda m: f"%%REGION:{m.group(1)}%%", t)

    return strip(before) == strip(after)


_SAFE_PACKAGES = {
    "geometry",
    "fontenc",
    "inputenc",
    "lmodern",
    "helvet",
    "enumitem",
    "titlesec",
    "microtype",
    "hyperref",
    "xcolor",
    "url",
    "parskip",
    "ragged2e",
    "tabularx",
    "array",
    "multicol",
    "fancyhdr",
    "setspace",
    "amsmath",
    "amssymb",
    "graphicx",
    "marvosym",
    "fontawesome5",
    "textcomp",
    "babel",
    "mathpazo",
    "charter",
    "tgheros",
    "tgtermes",
    "roboto",
    "carlito",
    "sourcesanspro",
    "cabin",
    "paracol",
}
_BANNED = [
    re.compile(r"\\(?:usepackage|RequirePackage)(\[[^\]]*\])?\{[^}]*\b(fontspec|polyglossia|unicode-math)\b[^}]*\}"),
    re.compile(r"\\directlua"),
    re.compile(r"\\write18"),
    re.compile(r"\\immediate\\write"),
    re.compile(r"\\(?:input|include)\{\s*(?:/|~|[^}]*\.\.)"),  # absolute, home-relative or parent-directory paths
    re.compile(r"\\openin|\\openout"),
]


@dataclass(frozen=True)
class LintIssue:
    level: str  # error | warn
    msg: str


def lint_overleaf(tex: str) -> list[LintIssue]:
    out: list[LintIssue] = []
    if r"\documentclass" not in tex:
        out.append(LintIssue("error", "missing \\documentclass"))
    if r"\begin{document}" not in tex:
        out.append(LintIssue("error", "missing document body"))
    for rx in _BANNED:
        if rx.search(tex):
            out.append(LintIssue("error", f"not Overleaf/pdfLaTeX-safe: {rx.pattern[:40]}"))
    for m in re.finditer(r"\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}", tex):
        for p in (x.strip() for x in m.group(1).split(",")):
            if p not in _SAFE_PACKAGES:
                out.append(LintIssue("warn", f"package not on allowlist: {p}"))
    return out


def citation_issues(region_text: str, known_facts: set[str]) -> list[str]:
    """Every content line inside a tailor region must end with `% fact:ID[,ID]`."""
    problems: list[str] = []
    for i, raw in enumerate(region_text.split("\n"), start=1):
        line = raw.strip()
        content = re.sub(r"(?<!\\)%.*$", "", line).strip()  # \% is a printed percent sign, not a comment
        if not line or not content:
            continue
        if re.fullmatch(r"\\(?:begin|end)\{[^{}]+\}(?:\[[^\]]*\])?", content) or re.fullmatch(
            r"\\vspace\*?\{[^{}]+\}|\\par|\\\\(?:\[[^\]]*\])?", content
        ):
            continue
        m = re.search(r"(?<!\\)%\s*fact:\s*([A-Za-z0-9_,\s-]+)\s*$", line)
        if not m:
            problems.append(f"line {i}: no fact citation")
            continue
        ids = [x.strip() for x in m.group(1).split(",") if x.strip()]
        if not ids:
            problems.append(f"line {i}: empty fact citation")
        for fid in ids:
            if fid not in known_facts:
                problems.append(f"line {i}: unknown fact {fid}")
    return problems


# Text a CV must not be sent with: an unfilled slot the owner still has to type in.
PLACEHOLDER = re.compile(r"X{3}[ -]X{3}[ -]X{4}|JP-TODO")


def keyword_score(text: str, keywords: list[str]) -> int:
    words = [k.strip().lower() for k in keywords if k.strip()]
    if not words:
        return 0
    hay = text.lower()
    # whole terms: "go" must not score on "Google", "C" not on "Cloud"; symbols ("c++", ".net") still match literally
    hit = sum(1 for k in words if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", hay))
    return round(hit / len(words) * 100)

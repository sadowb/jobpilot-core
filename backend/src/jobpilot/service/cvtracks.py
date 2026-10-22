"""Owner-managed CV tracks: add, replace the file, edit details, remove. The submitted file is always named
`Abdelhadi Marjane.pdf` (see domain.cv.CV_FILENAME); the track id/title are only for the owner.

A PDF-only upload keeps a marker as its LaTeX source: it is used exactly as uploaded and cannot be tailored."""

from __future__ import annotations

import base64
import binascii
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..domain.cv import lint_overleaf, sha256
from ..domain.market import MARKETS
from ..projection.cvstore import compile_tex, pdf_info, pdf_text
from ..repository.cvtracks import CvtracksRepository
from ..repository.database import J
from .application import Services

UPLOADED_MARKER = "% JP-UPLOADED-PDF: no LaTeX source; this CV is used exactly as uploaded.\n"
MAX_PDF_BYTES = 3_000_000
MAX_TEX_BYTES = 500_000
MAX_PAGES = 2
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")


def is_uploaded(tex: str) -> bool:
    return tex.startswith("% JP-UPLOADED-PDF")


def slug_for(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]


def _decode(data: str | None, limit: int, what: str) -> bytes | None:
    if not data:
        return None
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"{what} could not be read") from error
    if len(raw) > limit:
        raise ValueError(f"{what} is too large (max {limit // 1_000_000} MB)")
    return raw


def _check_pdf(path: Path) -> int:
    try:
        pages, _, _ = pdf_info(path)
        text = pdf_text(path)
    except (subprocess.SubprocessError, OSError, ValueError) as error:
        raise ValueError("that file is not a readable PDF") from error
    if pages < 1 or pages > MAX_PAGES:
        raise ValueError(f"the CV has {pages} pages; JobPilot accepts 1 or {MAX_PAGES}")
    if len(text.strip()) < 100:
        raise ValueError(
            "the PDF has no selectable text (a scan?). Employers' systems need real text; export it from your editor"
        )
    return pages


def clean_meta(title: str, keywords: list[str]) -> tuple[str, list[str]]:
    title = " ".join(title.split())
    if not 2 <= len(title) <= 80:
        raise ValueError("title must be 2-80 characters")
    kws: list[str] = []
    for keyword in keywords:
        keyword = " ".join(keyword.split()).lower()
        if keyword and keyword not in kws:
            if len(keyword) > 40:
                raise ValueError("a keyword is too long (max 40)")
            kws.append(keyword)
    if len(kws) > 30:
        raise ValueError("at most 30 keywords")
    return title, kws


def save_version(
    svc: Services,
    track: str,
    title: str,
    keywords: list[str],
    mode: str,
    pdf_b64: str | None,
    tex_b64: str | None,
    market: str = "any",
) -> dict[str, Any]:
    """Create the track or replace its CV. Needs a PDF, a .tex, or both (a lone .tex is compiled)."""
    if not _ID.fullmatch(track):
        raise ValueError("track id must be 2-40 lowercase letters, digits or dashes")
    if market not in MARKETS:
        raise ValueError("market must be any, us or morocco")
    title, keywords = clean_meta(title, keywords)
    pdf = _decode(pdf_b64, MAX_PDF_BYTES, "the PDF")
    tex_bytes = _decode(tex_b64, MAX_TEX_BYTES, "the .tex file")
    if pdf is None and tex_bytes is None:
        raise ValueError("upload a PDF (or a .tex file)")
    if pdf is not None and not pdf.startswith(b"%PDF-"):
        raise ValueError("that file is not a PDF")
    tex: str | None = None
    if tex_bytes is not None:
        try:
            tex = tex_bytes.decode()
        except UnicodeError as error:
            raise ValueError("the .tex file must be UTF-8 text") from error
        errors = [issue.msg for issue in lint_overleaf(tex) if issue.level == "error"]
        if errors:
            raise ValueError("the .tex file has problems: " + "; ".join(errors[:3]))
    with tempfile.TemporaryDirectory(prefix="jp-upload-") as temp:
        tex_path, pdf_path = Path(temp) / "main.tex", Path(temp) / "cv.pdf"
        if pdf is None:
            assert tex is not None
            tex_path.write_text(tex)
            try:
                with compile_tex(tex_path) as built:
                    pages = built.pages
                    pdf_path.write_bytes(Path(built.pdf_path).read_bytes())
            except (subprocess.SubprocessError, OSError) as error:
                raise ValueError("the .tex file did not compile") from error
            if pages > MAX_PAGES:
                raise ValueError(f"the CV compiles to {pages} pages; JobPilot accepts 1 or {MAX_PAGES}")
        else:
            pdf_path.write_bytes(pdf)
            _check_pdf(pdf_path)
        final_tex = tex if tex is not None else UPLOADED_MARKER
        tex_path.write_text(final_tex)
        obj = svc.store.put(tex_path, pdf_path)
    if is_uploaded(final_tex) and mode == "tailor":
        mode = "base"  # nothing to tailor without LaTeX source
    version = svc.register_track(track, title, keywords, mode, obj.sha, sha256(final_tex), market)
    CvtracksRepository(svc.db).update_cv_tracks_for_save_version(track)
    svc.sweep_cv_commits()
    return {"track": track, "sha": obj.sha, "versionId": version, "tailorable": not is_uploaded(final_tex)}


def update_meta(
    svc: Services, track: str, title: str | None, keywords: list[str] | None, mode: str | None, market: str | None = None
) -> None:
    if market is not None and market not in MARKETS:
        raise ValueError("market must be any, us or morocco")
    row = CvtracksRepository(svc.db).find_cv_tracks_for_update_meta(track)
    if not row:
        raise LookupError(f"unknown CV track {track}")
    new_title, new_keywords = clean_meta(
        title if title is not None else row["title"], keywords if keywords is not None else list(row["keywords"] or [])
    )
    new_mode = mode or row["default_mode"]
    if new_mode == "tailor":
        try:
            tex = svc.read_track_tex(track)
        except (LookupError, OSError) as error:
            raise PermissionError("this track has no CV file yet") from error
        if is_uploaded(tex):
            raise PermissionError(
                "this CV was uploaded as a PDF and cannot be tailored; upload the .tex source to enable tailoring"
            )
    CvtracksRepository(svc.db).update_cv_tracks_for_update_meta(
        new_title, J(new_keywords), new_mode, market or row["market"], track
    )
    svc.event(None, "track_edited", "user", {"track": track})


def remove(svc: Services, track: str, reassign_to: str | None) -> dict[str, Any]:
    """Archive a track. Open applications on it must move to another track first; history is untouched."""
    row = CvtracksRepository(svc.db).find_cv_tracks_for_remove(track)
    if not row:
        raise LookupError(f"unknown CV track {track}")
    if reassign_to == track:
        raise ValueError("choose a different track to move applications to")
    open_jobs = CvtracksRepository(svc.db).list_jobs_for_remove(track)
    if open_jobs:
        if not reassign_to:
            raise PermissionError(f"{len(open_jobs)} open application(s) use this CV; choose another track to move them to")
        target = CvtracksRepository(svc.db).find_replacement_track(reassign_to)
        if not target:
            raise LookupError(f"unknown CV track {reassign_to}")
        for job in open_jobs:
            svc.set_job_settings(job["id"], reassign_to, "base")
    CvtracksRepository(svc.db).update_cv_tracks_for_remove(track)
    svc.event(None, "track_removed", "user", {"track": track, "moved": len(open_jobs)})
    return {"ok": True, "moved": len(open_jobs)}


def read_source(svc: Services, track: str) -> dict[str, Any]:
    """The track's LaTeX source, for copying into Overleaf. A PDF-only upload has none."""
    try:
        tex = svc.read_track_tex(track)
    except (LookupError, OSError) as error:
        raise LookupError(f"no CV file for track {track}") from error
    if is_uploaded(tex):
        raise PermissionError("this CV was uploaded as a PDF; there is no LaTeX source to copy")
    return {"tex": tex, "bytes": len(tex.encode())}

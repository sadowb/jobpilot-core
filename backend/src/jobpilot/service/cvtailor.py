from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from ..domain.cv import (
    CV_FILENAME,
    TEX_FILENAME,
    citation_issues,
    lint_overleaf,
    outside_regions_unchanged,
    replace_regions,
    sha256,
    tailor_regions,
)
from ..llm import claude_once
from ..projection.cvstore import compile_tex, pdf_text
from ..repository.cvtailor import CvtailorRepository
from .application import Services

SYSTEM = (
    "You tailor a CV by rewriting ONLY the provided TAILOR regions. Rules: use only facts from FACTS (cite every content line with a "
    'trailing "% fact:ID[,ID]" LaTeX comment); never invent employers, dates, metrics, skills or eligibility; keep LaTeX valid and the '
    "same macros; keep each region about the same length. Output ONLY a JSON object mapping region name -> new LaTeX text. No prose, no code fences."
)


def tailor_cv(svc: Services, job_id: str) -> dict[str, Any]:
    j = svc.job(job_id)
    if svc.submit_clicked(job_id):
        raise PermissionError("CV cannot change after submission begins")
    if not j["cv_track_id"]:
        raise ValueError("no cv track chosen")
    base_tex = svc.read_track_tex(j["cv_track_id"])
    regions = tailor_regions(base_tex)
    if not regions:
        raise ValueError("track has no TAILOR regions")
    facts = CvtailorRepository(svc.db).list_facts_for_tailor_cv()
    fact_ids = {f["id"] for f in facts}
    posting = svc.posting(job_id)[:3500]
    cur = CvtailorRepository(svc.db).find_cv_tracks_for_tailor_cv(j["cv_track_id"])
    cache_key = sha256(f"{cur['sha256'] if cur else ''}|{sha256(posting)}")

    cached = CvtailorRepository(svc.db).find_cv_versions_for_tailor_cv(f"tailor-cache:{cache_key}")
    if cached and svc.store.has(cached["sha256"]):
        obj_sha, version_id = cached["sha256"], int(cached["id"])
        svc.cost(job_id, "cache", 0, 0, "cv_tailor", 0.0, True)  # zero tokens: identical (track version, posting)
    else:
        prompt = (
            f"ROLE: {j['role']} at {j['company_name']}\nPOSTING (truncated):\n{posting}\n\nFACTS:\n"
            + "\n".join(f"{f['id']}: {f['claim']}" for f in facts)
            + "\n\nREGIONS:\n"
            + "\n".join(f"### {k}\n{v}" for k, v in regions.items())
            + "\n\nReturn JSON {"
            + ", ".join(f'"{k}"' for k in regions)
            + ': "..."}.'
        )
        res = claude_once(svc.cfg, svc.cfg.agent_model_cv, prompt, system=SYSTEM, max_budget_usd=svc.cfg.cv_budget_usd)
        svc.cost(job_id, svc.cfg.agent_model_cv, res.tokens_in, res.tokens_out, "cv_tailor", res.usd)
        m = re.search(r"\{.*\}", res.text, re.S)
        if not m:
            raise ValueError("tailor: no JSON in model output")
        edits: dict[str, str] = json.loads(m.group(0))
        for name, text in edits.items():
            problems = citation_issues(text, fact_ids)
            if problems:
                raise ValueError(f"tailor rejected ({name}): {'; '.join(problems[:3])}")
        tex = replace_regions(base_tex, edits)
        if not outside_regions_unchanged(base_tex, tex):
            raise ValueError("tailor changed text outside regions")
        errs = [i for i in lint_overleaf(tex) if i.level == "error"]
        if errs:
            raise ValueError(f"lint: {errs[0].msg}")
        with tempfile.TemporaryDirectory(prefix="jp-tailor-") as temp:
            d = Path(temp)
            tex_path = d / TEX_FILENAME
            tex_path.write_text(tex)
            with compile_tex(tex_path) as built:
                if built.pages != 1:
                    raise ValueError(f"tailored CV is {built.pages} pages")
                if "ABDELHADI MARJANE" not in pdf_text(built.pdf_path):
                    raise ValueError("tailored CV lost its header")
                obj = svc.store.put(tex_path, built.pdf_path)
                obj_sha = obj.sha
        version_id = svc.register_tailored(job_id, obj_sha, sha256(tex), cache_key)

    svc.use_tailored(job_id, obj_sha, version_id)
    return {"sha": obj_sha, "versionId": version_id, "file": CV_FILENAME}

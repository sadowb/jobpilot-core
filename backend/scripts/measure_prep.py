"""Measure ingest preparation without a database, broker, projection or browser.

Run later with network and an authenticated Claude CLI (this makes one paid Haiku call):
  PYTHONPATH=backend/src backend/.venv/bin/python backend/scripts/measure_prep.py URL --context /private/path/prep.json

Context is private JSON, never commit it. PDFs resolve relative to that JSON file:
  {"profile": {"first_name": "Ada"}, "facts": ["Built Python APIs"],
   "answers": {"Preferred language?": "Python"},
   "cvs": [{"track_id": "swe", "title": "Software engineer", "market": "us", "pdf": "cv.pdf"}]}

Supply 1-24 real CVs; there are no fabricated owner defaults. The production preflight orchestrator,
prefetch, PDF extraction, candidate filtering, answer planner, CV validation and tool-less CLI run unchanged.
Only persistence, queueing and vault projection are replaced. No Services/Database is instantiated.
All writes go to a temporary directory removed on exit. Output contains question labels, not answers/CV text.
The timer starts immediately before preflight (excludes reading context and staging PDFs).
Public form inventories are lower bounds; conditional/browser-only questions are not measured.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import time
from contextlib import ExitStack, nullcontext
from pathlib import Path
from typing import Any, Literal
from unittest.mock import patch

from pydantic import BaseModel, ConfigDict, Field

from jobpilot.boards import resolve_board
from jobpilot.config import Settings
from jobpilot.domain.answers import SKIPPED, Known
from jobpilot.domain.canonical import norm
from jobpilot.domain.cv import CV_FILENAME
from jobpilot.domain.ids import JobIdentity, job_id
from jobpilot.dto.direct import safe_data
from jobpilot.service.preflight import prepare_ingested
from jobpilot.service.question_planner import QuestionPlanner
from jobpilot.worker.text import compare_text


class CvInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    track_id: str = Field(min_length=1, max_length=80)
    title: str
    market: Literal["us", "morocco", "any"]
    pdf: Path


class Context(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: dict[str, str] = Field(default_factory=dict)
    facts: list[str] = Field(default_factory=list)
    answers: dict[str, str] = Field(default_factory=dict)
    cvs: list[CvInput] = Field(min_length=1, max_length=24)


class MemoryStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def object_dir(self, digest: str) -> Path:
        return self.root / digest

    def link(self, *_: Any) -> None:
        pass  # no generated vault view needed for a measurement


class MemoryDatabase:
    """Only transactions exist. Any accidental SQL access fails immediately."""

    def transaction(self) -> Any:
        return nullcontext()


class MemoryServices:
    def __init__(self, url: str, context: Context, base: Path, root: Path) -> None:
        adapter, match = resolve_board(url)
        if adapter.id not in {"greenhouse", "lever", "ashby"} or not match.tenant or not match.requisition_id:
            raise ValueError("expected a public Greenhouse, Lever or Ashby job URL")
        self.id = job_id(JobIdentity(match.canonical_url, adapter.id, match.tenant, match.requisition_id))
        self.cfg = Settings(_env_file=None, jobpilot_home=root, jobpilot_vault=root / "vault", agent_model_cv="claude-haiku-5-5")
        self.db = MemoryDatabase()
        self.store = MemoryStore(root / "cv-store")
        self.row: dict[str, Any] = {"id": self.id, "url": url, "cv_mode": "base", "waiting_on": "agent"}
        self.answers = {norm(k): {"global": v} for k, v in context.answers.items()}
        self.known = Known(profile=context.profile.copy(), answers=self.answers)
        self.facts = context.facts
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.candidates: list[dict[str, Any]] = []
        self.form: dict[str, Any] = {}
        self.finished = False
        self.calls = self.tokens_in = self.tokens_out = 0
        if len({cv.track_id for cv in context.cvs}) != len(context.cvs):
            raise ValueError("CV track IDs must be unique")
        for version, cv in enumerate(context.cvs, 1):
            source = (base / cv.pdf).resolve()
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            directory = self.store.object_dir(digest)
            directory.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, directory / CV_FILENAME)
            self.candidates.append(
                {"id": cv.track_id, "title": cv.title, "market": cv.market, "version_id": version, "sha256": digest}
            )

    def job(self, _: str) -> dict[str, Any]:
        return self.row

    def posting(self, _: str) -> str:
        return str(self.row.get("posting") or "")

    def set_meta(self, _: str, **meta: Any) -> dict[str, Any]:
        self.row.update(meta, meta_captured=True)
        self.row["company_name"] = meta.get("company")
        self.row["company_slug"] = norm(meta.get("company") or "")
        return {"ok": True}

    def load_known(self) -> Known:
        return self.known

    def answer_suggest(self, label: str, answer: str, job: str, source: str) -> bool:
        scoped = self.answers.setdefault(norm(label), {})
        scope = f"job:{job}"
        if scope in scoped:
            return False
        scoped[scope] = answer
        return True

    def event(self, job: str, kind: str, actor: str, payload: dict[str, Any]) -> None:
        self.events.append((kind, payload))

    def agent_progress(self, *_: Any) -> None:
        pass

    def cost(self, job: str, model: str, tokens_in: int, tokens_out: int, step: str, usd: float) -> None:
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out

    def set_inventory(self, job: str, inventory: list[dict[str, Any]], **_: Any) -> None:
        self.form["inventory"] = inventory

    def wait(self, job: str, who: str, reason: str) -> None:
        self.row.update(waiting_on=who, reason=reason)

    def request_dispatch(self) -> None:
        pass

    def queue_cv_job(self, _: str) -> None:
        pass

    def project(self, _: str) -> None:
        pass


class MemoryRepository:
    def __init__(self, svc: MemoryServices) -> None:
        self.svc = svc

    def start(self, _: str) -> bool:
        return True

    def active(self, _: str) -> bool:
        return bool(self.svc.row["waiting_on"] == "agent")

    def finish(self, job: str, success: bool) -> None:
        self.svc.finished = success

    def lock_plan_write(self, _: str) -> None:
        pass

    def selection_candidates(self) -> list[dict[str, Any]]:
        return self.svc.candidates

    def owner_data(self, _: str) -> dict[str, Any]:
        return {
            "profile": self.svc.known.profile,
            "facts": [{"claim": claim} for claim in self.svc.facts],
            "answers": [
                {"question_key": key, "answer": answer, "scope": scope}
                for key, scoped in self.svc.known.answers.items()
                for scope, answer in scoped.items()
            ],
        }

    def last_event_payload(self, job: str, kind: str) -> dict[str, Any]:
        return next((payload for name, payload in reversed(self.svc.events) if name == kind), {})

    def assign_reasoned_track(self, track: str, version: int, job: str) -> dict[str, Any]:
        selected = next(c for c in self.svc.candidates if c["id"] == track and c["version_id"] == version)
        self.svc.row.update(cv_track_id=track, cv_version_id=version)
        return selected

    def plan(self, job: str, plan: list[dict[str, Any]], missing: list[str]) -> None:
        self.svc.form.update(plan=plan, missing=missing)


def measure(svc: MemoryServices) -> dict[str, Any]:
    repo = MemoryRepository(svc)
    questions: dict[str, list[str]] = {"required": [], "optional": []}
    original_plan = QuestionPlanner.plan

    def planned(planner: QuestionPlanner, *args: Any, **kwargs: Any) -> Any:
        outcome = original_plan(planner, *args, **kwargs)
        form = args[1]
        answered = {p.key for p in outcome.plan}
        for q in form.questions:
            skipped = svc.known.answers.get(norm(q.label), {}).get(f"job:{svc.id}") == SKIPPED
            if q.type != "file" and q.key not in answered and not skipped:
                questions["required" if q.required else "optional"].append(q.label)
        return outcome

    def completion(*args: Any, **kwargs: Any) -> Any:
        svc.calls += 1
        if svc.calls > 1:
            raise RuntimeError("measurement must make at most one model call")
        return compare_text(*args, **kwargs)

    with ExitStack() as stack:
        for module in ("question_planner", "cv_selection", "direct"):
            stack.enter_context(patch(f"jobpilot.service.{module}.DirectRepository", return_value=repo))
        # PostingPrefetch imports the repository locally.
        stack.enter_context(patch("jobpilot.repository.direct.DirectRepository", return_value=repo))
        stack.enter_context(patch("jobpilot.service.preflight.PreflightRepository", return_value=repo))
        stack.enter_context(patch.object(QuestionPlanner, "plan", planned))
        stack.enter_context(patch("jobpilot.service.question_planner.compare_text", completion))
        started = time.monotonic()
        prepare_ingested(svc, svc.id)  # type: ignore[arg-type]  # deliberately closed, in-memory service double
        elapsed = time.monotonic() - started
    failure = next((p["reason"] for k, p in svc.events if k == "ingest_preflight_failed"), None)
    return {
        "success": svc.finished,
        "seconds_to_questions_ready": round(elapsed, 3) if svc.finished else None,
        "elapsed_seconds": round(elapsed, 3),
        "model_calls": svc.calls,
        "input_tokens": svc.tokens_in,
        "output_tokens": svc.tokens_out,
        "cv_chosen": svc.row.get("cv_track_id"),
        "required_open": questions["required"] if svc.finished else None,
        "optional_open": questions["optional"] if svc.finished else None,
        "failure": failure or (None if svc.finished else "public form unavailable; browser fallback was not run"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url")
    parser.add_argument("--context", required=True, type=Path, help="private owner/CV JSON; see example above")
    args = parser.parse_args()
    try:
        context = Context.model_validate(safe_data(json.loads(args.context.read_text())))
        with tempfile.TemporaryDirectory(prefix="jobpilot-measure-prep-") as directory:
            svc = MemoryServices(args.url, context, args.context.resolve().parent, Path(directory))
            result = measure(svc)
        print(json.dumps(result, indent=2))
        return 0 if result["success"] else 1
    except Exception as exc:  # noqa: BLE001 - never print owner data or model output from validation errors
        print(json.dumps({"success": False, "failure": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

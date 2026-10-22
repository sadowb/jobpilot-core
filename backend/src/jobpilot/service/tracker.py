"""Owner-facing tracker: the application spreadsheet (applied date, status, interview, CV used, JD ...).

Rows come from three places: JobPilot applying (source=platform), the owner logging a job they applied to
themselves by URL (source=manual, details captured by a cheap Haiku/deterministic read), and a spreadsheet
import (source=imported). All writes go through Services' transaction + event + projection path.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ..boards import resolve_board
from ..clean import clean_posting, structure_rows
from ..domain.canonical import norm, pacing_hostname
from ..domain.ids import JobIdentity, url_hash
from ..domain.ids import job_id as mint_job_id
from ..extract import extract_posting
from ..repository.tracker import TrackerRepository
from .application import Services
from .tabs import TabService

# Spreadsheet header (normalised) -> internal key. Order-insensitive; unknown columns are ignored.
log = logging.getLogger("jobpilot.tracker")

_HEADERS: dict[str, str] = {
    "date resume submitted": "applied_on",
    "date applied": "applied_on",
    "applied": "applied_on",
    "current status": "status_text",
    "status": "status_text",
    "date contacted screened": "date_contacted",
    "date contacted": "date_contacted",
    "date of interview": "interview_on",
    "interview": "interview_on",
    "follow up": "follow_up_on",
    "resume used": "resume_label",
    "cv used": "resume_label",
    "submitted on": "submitted_via",
    "company": "company",
    "job title": "role",
    "role": "role",
    "job level stated": "job_level",
    "job level": "job_level",
    "annual salary": "pay",
    "salary": "pay",
    "location": "location",
    "remote on site": "work_mode",
    "remote": "work_mode",
    "type": "employment_type",
    "job link": "url",
    "link": "url",
    "job description": "posting",
    "company description": "company_description",
    "notes": "notes",
}
_CELL_CAPS = {"role": 200, "company": 120, "notes": 4000, "posting": 20_000, "company_description": 2000, "url": 2000}
_DATE_FORMATS = (
    "%d/%m/%Y",
    "%d/%m/%y",  # day-first (owner convention)
    "%Y-%m-%d",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
)
_STATUS_ORDER = [  # first match wins; negations must come before the word they negate
    (r"\bnot (yet )?(applied|submitted|sent)|haven.?t (applied|submitted)|\bto apply\b|\bnot applied\b", "discovered"),
    (r"no offer|reject|denied|no response|ghost|turned down|not selected|unsuccessful|regret", "rejected"),
    (r"offer.*declin|declin.*offer|withdr|pulled out|no longer interested", "withdrawn"),
    (r"offer", "offer"),
    (r"declin", "rejected"),
    (r"interview", "interview"),
    (r"screen|contacted|recruiter|phone", "screening"),
    (r"applied|submitted|sent", "submitted"),
    (r"closed|expired", "closed"),
    (r"added|not checked|todo|to do|saved|wishlist|interested", "discovered"),
]
EDITABLE = {
    "role": "role",
    "location": "location",
    "work_mode": "work_mode",
    "pay": "pay",
    "job_level": "job_level",
    "employment_type": "employment_type",
    "submitted_via": "submitted_via",
    "resume_label": "resume_label",
    "date_contacted": "date_contacted",
    "interview_on": "interview_on",
    "follow_up_on": "follow_up_on",
    "user_status": "user_status",
    "notes": "notes",
}
_DATE_FIELDS = {"date_contacted", "interview_on", "follow_up_on"}
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_STATUS_TEXT_MAX = 40  # longer status cells are free text; they go to Haiku, never straight into a status

# What the owner can set by hand; the rest are driven by the pipeline / approvals.
_OWNER_STATUSES = {"discovered", "submitted", "screening", "interview", "offer", "rejected", "withdrawn", "closed"}
_WAITING_ON = {"agent", "you", "site"}
_STATUSES = {
    "discovered", "screened", "preparing", "ready", "awaiting_approval", "submitted",
    "screening", "interview", "offer", "rejected", "withdrawn", "closed",
}  # fmt: skip
JOB_STATUSES = frozenset(_STATUSES)


def scrub(text: str) -> str:
    """Drop bidi overrides, zero-width and control characters from pasted text."""
    return _INVISIBLE.sub("", text)


def sane_date(day: date | None, *, future_days: int = 0) -> date | None:
    """None unless the date is plausible (2000 .. today + future_days)."""
    if day is None or day < date(2000, 1, 1) or day > date.today() + timedelta(days=future_days):
        return None
    return day


def parse_date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def check_applied_date(day: date) -> None:
    if day < date(2000, 1, 1) or day > date.today() + timedelta(days=1):
        raise ValueError("applied date must be between 2000 and today")


def map_status(text: str | None) -> str:
    value = (text or "").lower()
    for pattern, status in _STATUS_ORDER:
        if re.search(pattern, value):
            return status
    return "submitted"


def _key(header: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", header.lower())).strip()


def parse_table(text: str) -> list[dict[str, str]]:
    """Parse pasted/exported spreadsheet text (TSV from a copy, or CSV) into normalised row dicts."""
    body = text.lstrip("﻿").strip("\r\n")
    if not body.strip():
        return []
    first_line = body.split("\n", 1)[0]
    delimiter = "\t" if first_line.count("\t") >= first_line.count(",") else ","
    reader = csv.reader(io.StringIO(body.replace("\x00", "")), delimiter=delimiter, quotechar='"')
    try:
        raw = list(reader)
    except csv.Error as error:  # unterminated quote, oversized cell
        raise ValueError(f"could not read the sheet: {error}. Check for a stray quote or a very large cell.") from error
    if len(raw) > 5001:
        raise ValueError("too many rows; import at most 5000 at a time")
    if len(raw) < 2:
        return []
    columns = [_HEADERS.get(_key(h)) for h in raw[0]]
    if "company" not in columns and "role" not in columns:
        raise ValueError("could not recognise the header row (need at least Company and Job Title columns)")
    rows: list[dict[str, str]] = []
    for cells in raw[1:]:
        if not any(c.strip() for c in cells):
            continue
        row: dict[str, str] = {}
        for index, name in enumerate(columns):
            if name and index < len(cells) and scrub(cells[index]).strip():
                row.setdefault(name, scrub(cells[index]).strip()[: _CELL_CAPS.get(name, 400)])
        if row.get("company") or row.get("role"):
            rows.append(row)
    return rows


class Tracker:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.db = svc.db
        self.repository = TrackerRepository(svc.db)

    # ---------- read ----------
    def rows(
        self,
        status: str | None = None,
        q: str | None = None,
        waiting_on: str | None = None,
        limit: int = 100,
        offset: int = 0,
        archived: bool = False,
    ) -> list[dict[str, Any]]:
        if status and status not in _STATUSES:
            raise ValueError(f"unknown status: {status}")
        if waiting_on and waiting_on not in _WAITING_ON:
            raise ValueError(f"unknown waitingOn: {waiting_on}")
        if q and "\x00" in q:
            raise ValueError("q: contains an unsupported character")
        result = self.repository.list_rows(status, q, waiting_on, limit, offset, archived)
        stale_before = datetime.now(UTC) - timedelta(minutes=15)
        for row in result:
            if row["source"] != "platform" and row["waitingOn"] == "agent" and row["updatedAt"] < stale_before:
                row["waitingOn"] = "you"  # an agent that hasn't produced anything in 15 min must not look busy forever
                row["blockedReason"] = row["blockedReason"] or "JobPilot couldn't read this page. Add the details yourself."
            if row["source"] != "platform":
                row["missing"] = self.svc.external_missing(
                    {
                        "company_name": row["company"],
                        "role": row["role"],
                        "location": row["location"],
                        "meta_captured": row["hasDescription"],
                    }
                )
            else:
                row["missing"] = []
        TabService(self.svc).annotate(result)
        return result

    def detail_extra(self, job_id: str) -> dict[str, Any]:
        row = self.repository.find_jobs_for_detail_extra(job_id)
        return row or {}

    # ---------- write ----------
    def update(self, job_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Owner edits of tracking fields (UI session only). Status for external rows may be set directly."""
        with self.db.transaction():
            self.repository.lock_job(job_id)
            job = self.repository.find_job_tracking_state(job_id)
            if not job:
                raise LookupError(f"unknown job {job_id}")
            if job["archived_at"] is not None:
                raise PermissionError("this application is deleted; restore it before editing")
            for key, value in fields.items():
                if isinstance(value, str) and "\x00" in value:
                    raise ValueError(f"{key}: contains an unsupported character")
            if "role" in fields and not str(fields["role"] or "").strip():
                raise ValueError("role: a job title is required")
            if "status" in fields and not fields["status"]:
                raise ValueError("status: choose a status")
            values: dict[str, Any] = {}
            changed: list[str] = []
            for key, value in fields.items():
                if key == "status":
                    continue
                if key == "applied_on":
                    self._set_applied_on(job_id, value)
                    changed.append("applied_on")
                    continue
                column = EDITABLE.get(key)
                if not column:
                    raise ValueError(f"field not editable: {key}")
                text = None if value is None or str(value).strip() == "" else str(value).strip()
                if key in _DATE_FIELDS:
                    parsed = parse_date(text)
                    if text and not parsed:
                        raise ValueError(f"{key}: use a date like 2026-10-09")
                    if parsed and not date(2000, 1, 1) <= parsed <= date.today() + timedelta(days=3650):
                        raise ValueError(f"{key}: that date looks wrong")
                    values[column] = parsed
                else:
                    values[column] = text
                changed.append(key)
            if values:
                self.repository.update_fields(job_id, values)
            status = fields.get("status")
            if status:
                if status not in _OWNER_STATUSES:
                    raise ValueError("that status is set by JobPilot, not by hand" if status in _STATUSES else "unknown status")
                if job["source"] == "platform":
                    raise PermissionError("platform jobs change status through approvals/outcome")
                self.repository.update_jobs_for_update(status, job_id)
                changed.append("status")
            if changed:
                detail: dict[str, Any] = {"fields": changed}
                if "status" in changed:
                    detail["status"] = status
                self.svc.event(job_id, "tracking_updated", "user", detail)
        self.svc.project(job_id)
        return {"ok": True, "changed": changed}

    def _set_applied_on(self, job_id: str, value: Any) -> None:
        parsed = parse_date(str(value)) if value else None
        if not parsed:
            raise ValueError("applied_on: use a date like 2026-10-09")
        check_applied_date(parsed)
        self.repository.insert_applications_for_set_applied_on(job_id, parsed)

    def add_external(
        self,
        *,
        url: str | None,
        company: str | None,
        role: str | None,
        status: str,
        applied_on: date | None,
        source: str,
        extra: dict[str, Any] | None = None,
        posting: str | None = None,
        company_description: str | None = None,
        queue_capture: bool = False,
    ) -> dict[str, Any]:
        """Create (or enrich) a row for an application the owner made outside the platform."""
        extra = extra or {}
        with self.db.transaction():
            if url:
                adapter, match = resolve_board(url)
                canonical, board_id, tenant, req = match.canonical_url, adapter.id, match.tenant, match.requisition_id
            else:
                key = f"{norm(company or '')}/{norm(role or '')}/{applied_on or ''}"
                canonical = "https://tracker.invalid/" + hashlib.sha256(key.encode()).hexdigest()[:24]
                board_id, tenant, req = "custom", None, None
            jid = mint_job_id(JobIdentity(canonical, board_id, tenant, req))
            self.repository.find_transaction_lock_for_add_external(jid)
            if not url:  # no-link rows dedupe on company+title, so serialise on that key too
                self.repository.find_transaction_lock_for_add_external(f"ct:{norm(company or '')}/{norm(role or '')}")
            existing = self.repository.find_jobs_for_add_external(
                jid, url_hash(canonical), board_id, tenant, req, req, url, norm(company or ""), norm(role or "")
            )
            created = existing is None
            if existing:
                jid = existing["id"]
            else:
                company_name = company or (
                    tenant.replace("-", " ").replace("_", " ").title() if tenant else "Unknown company"
                )  # a hostname is a guess, not a company: stay honest and flag it as missing
                self.repository.insert_boards_for_add_external(board_id, board_id)
                cid = self.svc.upsert_company(company_name)
                self.repository.insert_jobs_for_add_external(jid, cid, role or '(role not captured yet)', canonical, url_hash(canonical), board_id, tenant, req, norm(company_name), norm(role or ''), pacing_hostname(canonical, tenant), source, status)  # fmt: skip
            updates: dict[str, Any] = {k: v for k, v in extra.items() if v not in (None, "")}
            values: dict[str, Any] = {}
            for key, value in updates.items():
                column = EDITABLE.get(key)
                if column:
                    values[column] = parse_date(str(value)) if key in _DATE_FIELDS else str(value)
            if company_description:
                self.repository.update_companies_for_add_external(company_description, jid)
            imported_status = (
                status if existing is not None and existing["source"] == "platform" and status != "discovered" else None
            )
            self.repository.fill_imported_fields(
                jid,
                values,
                role,
                norm(role) if role else None,
                posting,
                hashlib.sha256(posting.encode()).hexdigest() if posting else None,
                imported_status,
            )
            if applied_on:
                self.repository.insert_applications_for_add_external(jid, applied_on, f"Logged by you ({source})")
            gaps = self.svc.external_missing(self.svc.job(jid)) if queue_capture and url else []
            if gaps:
                # Required fields still empty after the instant read: Haiku fills them, and the software re-asks if it doesn't.
                self.repository.update_jobs_for_add_external(jid)
                self.svc.enqueue(jid)
            self.svc.event(jid, "applied_logged" if source == "manual" else "tracker_import", "user", {"created": created})
        self.svc.project(jid)
        return {"id": jid, "created": created, "captureQueued": bool(gaps)}

    def log_applied(self, urls: list[str], applied_on: date | None, extra: dict[str, Any]) -> list[dict[str, Any]]:
        """Owner applied by hand: read each page right now (no browser; one short tool-less Haiku call tidies the fields) and file the row."""
        day = applied_on or date.today()

        def read(url: str) -> dict[str, Any]:
            try:
                raw = extract_posting(url)
            except Exception:  # noqa: BLE001 - unreadable page: the row is still created, details can be added by hand
                return {}
            return clean_posting(self.svc.cfg, url, raw)  # Haiku-validated fields, or {"cleaned": False}

        with ThreadPoolExecutor(max_workers=6) as pool:
            details = list(pool.map(read, urls))
        out: list[dict[str, Any]] = []
        for url, found in zip(urls, details, strict=True):
            spent = found.pop("_cost", None)
            needs_review = not found.get("cleaned")
            if spent:
                self.svc.cost(None, self.svc.cfg.agent_model_cv, spent[0], spent[1], "extract_clean", spent[2])
            try:
                note = extra.get("notes") or (
                    "Posting is no longer online; add the job title and details yourself."
                    if found.get("closed") and not found.get("role")
                    else "The page was readable but no clear job title was found, so nothing was guessed. Add the details or let JobPilot open it."
                    if needs_review and found.get("reason") == "no_role"
                    else "Couldn't read this page cleanly, so nothing was guessed. Add the details or let JobPilot open it."
                    if needs_review
                    else None
                )
                fields = {
                    **extra,
                    "notes": note,
                    "location": found.get("location"),
                    "work_mode": found.get("work_mode"),
                    "pay": found.get("pay"),
                    "employment_type": found.get("employment_type"),
                    "job_level": found.get("job_level"),
                }
                result = self.add_external(
                    url=url,
                    company=found.get("company"),
                    role=found.get("role"),
                    status="submitted",
                    applied_on=day,
                    source="manual",
                    extra=fields,
                    posting=found.get("description"),
                    company_description=found.get("company_description"),
                    queue_capture=True,
                )
                out.append({**result, "url": url, "filled": not needs_review, "needsReview": needs_review})
            except Exception as error:  # noqa: BLE001 - one bad URL must not lose the others
                out.append({"url": url, "error": str(error)[:200]})
        return out

    def import_rows(self, text: str) -> dict[str, Any]:
        rows = parse_table(text)
        results: list[dict[str, Any]] = []
        structured, review = self._structure_messy(rows)
        for index, row in enumerate(rows):
            row = {**row, **structured.get(str(index), {})}  # Haiku-validated cells replace the messy raw text
            applied_raw = parse_date(row.get("applied_on"))
            applied = sane_date(applied_raw, future_days=1)
            out_of_range = applied_raw is not None and applied is None
            status = map_status(row.get("status_text"))
            if status == "submitted" and not applied and "applied" not in (row.get("status_text") or "").lower():
                status = "discovered"
            contacted_text = row.get("date_contacted", "")
            contacted = parse_date(contacted_text)
            notes = row.get("notes") or ""
            if out_of_range:
                notes = (
                    notes + "\n" if notes else ""
                ) + f"Date applied: {row.get('applied_on')} (outside the valid range, not used)"
                review += 1
            for label, key in (
                ("Date applied", "applied_on"),
                ("Contacted", "date_contacted"),
                ("Interview", "interview_on"),
                ("Follow up", "follow_up_on"),
            ):
                text_value = row.get(key, "")
                if text_value and not parse_date(text_value):  # free text in a date column: keep it, never drop it
                    notes = (notes + "\n" if notes else "") + f"{label}: {text_value}"
            if len(notes) > 4000:
                review += 1
            notes = notes[:4000]
            try:
                results.append(
                    self.add_external(
                        url=row.get("url"),
                        company=row.get("company"),
                        role=row.get("role"),
                        status=status,
                        applied_on=applied,
                        source="imported",
                        posting=row.get("posting"),
                        company_description=row.get("company_description"),
                        extra={
                            "location": row.get("location"),
                            "work_mode": row.get("work_mode"),
                            "pay": row.get("pay"),
                            "job_level": row.get("job_level"),
                            "employment_type": row.get("employment_type"),
                            "submitted_via": row.get("submitted_via"),
                            "resume_label": row.get("resume_label"),
                            "date_contacted": contacted_text if sane_date(contacted, future_days=730) else None,
                            "interview_on": row.get("interview_on")
                            if sane_date(parse_date(row.get("interview_on")), future_days=730)
                            else None,
                            "follow_up_on": row.get("follow_up_on")
                            if sane_date(parse_date(row.get("follow_up_on")), future_days=730)
                            else None,
                            "user_status": (row.get("status_text") or "")[:_STATUS_TEXT_MAX] or None,
                            "notes": notes or None,
                        },
                    )
                )
            except Exception as error:  # noqa: BLE001
                results.append({"error": str(error)[:200], "company": row.get("company"), "role": row.get("role")})
        return {
            "rows": len(rows),
            "created": sum(1 for r in results if r.get("created")),
            "updated": sum(1 for r in results if r.get("created") is False),
            "errors": [r for r in results if "error" in r],
            "needsReview": review,
        }

    def _structure_messy(self, rows: list[dict[str, str]]) -> tuple[dict[str, dict[str, str]], int]:
        """Rows whose cells the deterministic parser can't fully understand go to Haiku in batches.

        Returns (validated replacement cells per row index, number of rows left with raw text in notes)."""
        messy: dict[str, dict[str, str]] = {}
        for index, row in enumerate(rows):
            raw = {
                k: row[k]
                for k in ("applied_on", "date_contacted", "interview_on", "follow_up_on")
                if row.get(k) and not parse_date(row[k])
            }
            text_status = row.get("status_text") or ""
            if text_status and (
                len(text_status) > _STATUS_TEXT_MAX or not any(re.search(p, text_status.lower()) for p, _ in _STATUS_ORDER)
            ):
                raw["status_text"] = text_status
            if row.get("work_mode") and row["work_mode"].lower() not in ("remote", "hybrid", "on-site", "onsite", "on site"):
                raw["work_mode"] = row["work_mode"]
            if raw:
                if row.get("notes"):
                    raw["notes"] = row["notes"]
                messy[str(index)] = raw
        if not messy:
            return {}, 0
        out: dict[str, dict[str, str]] = {}
        keys = list(messy)
        failed = 0
        for start in range(0, len(keys), 30):
            batch = {k: messy[k] for k in keys[start : start + 30]}
            try:
                parsed, cost = structure_rows(self.svc.cfg, batch, date.today().isoformat())
            except Exception:  # noqa: BLE001 - cells stay as notes below; the owner is told how many rows need a look
                failed += len(batch)
                continue
            if cost:
                self.svc.cost(None, self.svc.cfg.agent_model_cv, cost[0], cost[1], "import_structure", cost[2])
            out.update(parsed)
        # Haiku returns clean values; map them back onto the row's own keys (status goes through status_text).
        merged: dict[str, dict[str, str]] = {}
        for key_index, fields in out.items():
            row = {k: v for k, v in fields.items() if k not in ("status", "notes")}  # the owner's own notes are never paraphrased
            if "status" in fields:
                row["status_text"] = fields["status"]
            for key in ("applied_on", "date_contacted", "interview_on", "follow_up_on"):
                row.setdefault(key, "")  # raw unparseable text must not survive next to a structured answer
            merged[key_index] = row
        return merged, failed

    # ---------- delete / restore / merge ----------
    def archive(self, job_id: str) -> dict[str, Any]:
        with self.db.transaction():
            row = self.repository.find_tasks_for_archive(job_id)
            if row:
                raise PermissionError("a worker is using this application right now; try again in a moment")
            current = self.repository.find_jobs_for_archive(job_id)
            if not current:
                raise LookupError(f"unknown job {job_id}")
            if current["archived_at"] is not None:
                return {"ok": True}  # already deleted: nothing to do, no duplicate event
            self.repository.update_jobs_for_archive(job_id)
            self.repository.update_tasks_for_archive(job_id)
            # a deleted application needs no browser: the maintenance sweep closes it
            self.repository.update_browser_sessions_for_archive(job_id)
            self.svc.event(job_id, "archived", "user", {})
            self.svc.cleanup_agent_session(job_id)
        return {"ok": True}

    def restore(self, job_id: str) -> dict[str, Any]:
        with self.db.transaction():
            current = self.repository.find_jobs_for_archive(job_id)
            if not current:
                raise LookupError(f"unknown job {job_id}")
            if current["archived_at"] is None:
                return {"ok": True}
            self.repository.update_jobs_for_restore(job_id)
            self.svc.event(job_id, "restored", "user", {})
        return {"ok": True}

    def purge(self, job_id: str) -> dict[str, Any]:
        """Permanent delete; only for already-archived rows that never submitted anything through JobPilot."""
        with self.db.transaction():
            job = self.repository.find_jobs_for_purge(job_id)
            if not job:
                raise LookupError(f"unknown job {job_id}")
            if job["archived_at"] is None:
                raise PermissionError("delete it first, then it can be removed permanently")
            if job["source"] == "platform" and self.repository.find_applications_for_purge(job_id):
                raise PermissionError("JobPilot submitted this application; it stays in your history")
            tab = self.repository.find_browser_session_tab_for_purge(job_id)
            self.repository.delete_job_dependencies(job_id)
            self.repository.update_cost_ledger_for_purge(job_id)  # spend history outlives the row
            self.repository.delete_jobs_for_purge(job_id)
            self.svc.cleanup_agent_session(job_id)
        if tab and tab["target_id"]:  # its row is gone, so nothing else would ever close this tab
            if not self.svc.tab_binder().release(str(tab["target_id"])):
                log.warning("tab close failed for purged job=%s", job_id)
        return {"ok": True}

    def duplicates(self) -> list[dict[str, Any]]:
        """Groups of live applications that are very likely the same job (same company + title)."""
        rows = self.repository.list_jobs_for_duplicates()
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault((row["cn"], row["tn"]), []).append({k: v for k, v in row.items() if k not in ("cn", "tn")})
        return [{"key": f"{k[0]}|{k[1]}", "rows": members} for k, members in groups.items() if len(members) > 1]

    def merge(self, keep_id: str, merge_ids: list[str]) -> dict[str, Any]:
        """Fold duplicates into one row: fill the kept row's blanks from the others, then hide them."""
        fields = [
            "location", "work_mode", "pay", "job_level", "employment_type", "submitted_via", "resume_label",
            "date_contacted", "interview_on", "follow_up_on", "user_status",
        ]  # fmt: skip
        with self.db.transaction():
            keep = self.repository.find_jobs_for_merge(keep_id)
            if not keep:
                raise LookupError(f"unknown job {keep_id}")
            if keep["archived_at"] is not None:
                raise PermissionError("the application you are keeping is deleted; restore it first")
            for other_id in merge_ids:
                if other_id == keep_id:
                    raise ValueError("keepId must not also be in mergeIds")
                other = self.repository.find_jobs_for_merge(other_id)
                if not other:
                    raise LookupError(f"unknown job {other_id}")
                if self.repository.find_tasks_for_merge(other_id):
                    raise PermissionError("a worker is using one of these applications right now")
                values = {}
                for column in fields:
                    if keep[column] in (None, "") and other[column] not in (None, ""):
                        values[column] = other[column]
                rank = {"discovered": 0, "submitted": 1, "screening": 2, "interview": 3, "offer": 4}
                if (
                    keep["source"] != "platform"
                    and other["status"] in rank
                    and keep["status"] in rank
                    and rank[other["status"]] > rank[keep["status"]]
                ):  # merging must not lose progress: the furthest open stage wins
                    values["status"] = other["status"]
                notes = [n for n in (keep["notes"], other["notes"]) if n]
                if len(notes) == 2 and notes[0] != notes[1]:
                    values["notes"] = notes[0] + "\n" + notes[1]
                elif other["notes"] and not keep["notes"]:
                    values["notes"] = other["notes"]
                if not keep["posting_text"] and other["posting_text"]:
                    values.update(posting_text=other["posting_text"], posting_sha256=other["posting_sha256"])
                if values:
                    self.repository.update_fields(keep_id, values, cast_dates=False)
                    entity = self.repository.find_job_record(keep_id)
                    keep = entity.model_dump() if entity is not None else keep
                # earliest applied date wins; the kept row inherits an application record if it has none
                mine = self.repository.find_applications_for_merge(keep_id)
                theirs = self.repository.find_application_confirmation(other_id)
                if theirs and (not mine or theirs["applied_on"] < mine["applied_on"]):
                    self.repository.insert_applications_for_merge(
                        keep_id, theirs["applied_on"], theirs["applied_at"], theirs["confirmation"], theirs["confirmation_text"]
                    )
                self.repository.update_jobs_for_archive(other_id)
                self.repository.update_tasks_for_archive(other_id)
                self.svc.event(other_id, "merged_into", "user", {"keep": keep_id})
            self.svc.event(keep_id, "merged", "user", {"from": [i for i in merge_ids if i != keep_id]})
        self.svc.project(keep_id)
        return {"ok": True, "kept": keep_id}

    # ---------- facts & answers (the library the owner curates) ----------
    def fact_create(self, claim: str, source: str = "you") -> dict[str, Any]:
        text = claim.strip()
        if not text:
            raise ValueError("write the fact first")
        fact_id = "f_" + hashlib.sha256(text.lower().encode()).hexdigest()[:10]
        with self.db.transaction():
            created = not self.repository.find_facts_for_fact_create(fact_id)
            self.repository.insert_facts_for_fact_create(fact_id, text, source)
            self.svc.event(None, "fact_created", "user", {"id": fact_id})
        return {"id": fact_id, "created": created}

    def fact_delete(self, fact_id: str) -> dict[str, Any]:
        with self.db.transaction():
            self.repository.delete_facts_for_fact_delete(fact_id)
            self.svc.event(None, "fact_deleted", "user", {"id": fact_id})
        return {"ok": True}

    def answer_delete(self, question_key: str, scope: str) -> dict[str, Any]:
        with self.db.transaction():
            self.repository.delete_answers_for_answer_delete(question_key, scope)
            self.svc.event(None, "answer_deleted", "user", {"questionKey": question_key, "scope": scope})
        return {"ok": True}

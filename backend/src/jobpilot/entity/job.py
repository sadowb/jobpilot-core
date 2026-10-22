"""A jobs table record; joined columns are preserved as extra fields."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class Job(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    id: str
    company_id: int | None
    role: str
    url: str
    canonical_url_hash: str
    board_id: str | None
    tenant: str | None
    requisition_id: str | None
    company_norm: str
    title_norm: str
    location_norm: str | None
    location: str | None
    work_mode: str | None
    pay: str | None
    deadline: date | None
    status: str
    waiting_on: str
    blocked_reason: str | None
    cv_track_id: str | None
    cv_mode: str
    cv_version_id: int | None
    match_score: int | None
    posting_sha256: str | None
    posting_text: str | None
    created_at: datetime
    updated_at: datetime
    pacing_hostname: str
    apply_url: str | None
    source: str
    job_level: str | None
    employment_type: str | None
    submitted_via: str | None
    resume_label: str | None
    date_contacted: date | None
    interview_on: date | None
    follow_up_on: date | None
    user_status: str | None
    notes: str | None
    archived_at: datetime | None

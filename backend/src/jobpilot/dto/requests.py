"""Pydantic request bodies; database tables are defined by SQL migrations."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


class IngestRequest(BaseModel):
    urls: list[HttpUrl] = Field(min_length=1, max_length=500)
    cvTrack: str | None = None
    cvMode: Literal["base", "tailor"] = "base"


class CaptureRequest(BaseModel):
    """One link sent from the browser extension. Only the address is used; the title is a display hint."""

    url: HttpUrl
    title: str | None = Field(default=None, max_length=300)


class JobSettings(BaseModel):
    cvTrack: str | None = None
    cvMode: Literal["base", "tailor"] | None = None


class JobSubmitMode(BaseModel):
    mode: Literal["review", "auto"] | None = None  # None: use the default


class AnswerItem(BaseModel):
    question: str = Field(min_length=1)
    answer: str = ""
    scope: str = "global"
    skip: bool = False  # leave this question empty on this application; `answer` is ignored

    @model_validator(mode="after")
    def answered_or_skipped(self) -> AnswerItem:
        if not self.skip and not self.answer.strip():
            raise ValueError("answer is required unless skip is true")
        return self


class AnswersRequest(BaseModel):
    answers: list[AnswerItem] = Field(min_length=1)
    # False stores the answers and leaves the job paused, so several can be saved and sent to the agent in one go.
    resume: bool = True


class ProfileSet(BaseModel):
    key: str = Field(min_length=1, max_length=60)
    value: str = Field(max_length=2000)


class FactConfirm(BaseModel):
    confirmed: bool
    claim: str | None = None


class BoardUpdate(BaseModel):
    automation: Literal["auto", "assisted", "manual"] | None = None


class JobStatusRequest(BaseModel):
    status: Literal["screening", "interview", "offer", "rejected", "withdrawn", "closed"]


class AgentGuidanceRequest(BaseModel):
    model_config = {"extra": "forbid"}
    message: str = Field(min_length=1, max_length=2000)


class TrackCreate(BaseModel):
    title: str = Field(min_length=2, max_length=80)
    id: str | None = Field(default=None, max_length=40)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    defaultMode: Literal["base", "tailor"] = "base"
    market: Literal["any", "us", "morocco"] = "any"
    pdfBase64: str | None = Field(default=None, max_length=4_200_000)
    texBase64: str | None = Field(default=None, max_length=700_000)


class TrackFile(BaseModel):
    pdfBase64: str | None = Field(default=None, max_length=4_200_000)
    texBase64: str | None = Field(default=None, max_length=700_000)


class TrackEdit(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=80)
    keywords: list[str] | None = Field(default=None, max_length=30)
    defaultMode: Literal["base", "tailor"] | None = None
    market: Literal["any", "us", "morocco"] | None = None


class TrackRemove(BaseModel):
    reassignTo: str | None = Field(default=None, max_length=40)


class ProfileSection(BaseModel):
    entries: list[dict[str, Any]] = Field(max_length=20)


class RecipeSuggestion(BaseModel):
    id: int
    job_id: str
    board_id: str
    note: str
    status: Literal["pending", "accepted", "rejected"]


class RecipeReview(BaseModel):
    accept: bool
    text: str | None = Field(default=None, min_length=1, max_length=12000)


class AppliedRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=50)
    appliedOn: date | None = Field(default=None, ge=date(2000, 1, 1))
    resumeLabel: str | None = Field(default=None, max_length=120)
    submittedVia: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)


class ImportRequest(BaseModel):
    text: str = Field(min_length=1, max_length=3_000_000)


class TrackingUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    status: str | None = None
    applied_on: str | None = None
    date_contacted: str | None = None
    interview_on: str | None = None
    follow_up_on: str | None = None
    resume_label: str | None = Field(default=None, max_length=120)
    submitted_via: str | None = Field(default=None, max_length=120)
    job_level: str | None = Field(default=None, max_length=80)
    employment_type: str | None = Field(default=None, max_length=80)
    location: str | None = Field(default=None, max_length=160)
    work_mode: str | None = Field(default=None, max_length=80)
    pay: str | None = Field(default=None, max_length=120)
    role: str | None = Field(default=None, max_length=200)
    user_status: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=4000)


class MergeRequest(BaseModel):
    keepId: str
    mergeIds: list[str] = Field(min_length=1, max_length=50)


class FactCreate(BaseModel):
    claim: str = Field(min_length=1, max_length=1000)


class AnswerUpsert(BaseModel):
    questionKey: str = Field(min_length=1, max_length=300)
    answer: str = Field(min_length=1, max_length=2000)
    scope: str = Field(default="global", max_length=120)


class AnswerRef(BaseModel):
    questionKey: str = Field(min_length=1, max_length=300)
    scope: str = Field(default="global", max_length=120)


class CredentialCreate(BaseModel):
    origin: str = Field(min_length=1, max_length=2048)
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class CredentialImport(BaseModel):
    csv: str = Field(min_length=1, max_length=3_000_000)
    scope: Literal["ats", "all"] = "ats"
    commit: bool = False

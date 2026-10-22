"""Response contracts exported by the private OpenAPI exporter."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Ok(BaseModel):
    ok: bool


class SessionResponse(BaseModel):
    csrf: str


class StatusCount(BaseModel):
    status: str
    waiting_on: str
    n: int


class Cost24h(BaseModel):
    usd: float
    tokens: str


class StatsResponse(BaseModel):
    byStatus: list[StatusCount]
    cost24h: Cost24h | None
    appliedToday: int
    submittedWithoutApproval: list[str]


class JobListItem(BaseModel):
    id: str
    company: str | None
    role: str
    url: str
    board: str | None
    status: str
    waitingOn: str
    cvTrack: str | None
    cvMode: str
    matchScore: int | None
    appliedOn: str | None
    updatedAt: datetime
    blockedReason: str | None
    tabOpen: bool = False  # this job has a live tab in Neo that the owner can find by its page id
    parkSignal: Literal["looks_submitted", "tab_closed"] | None = None  # what the parked-tab watcher saw (ADR 0026)


class CvContext(BaseModel):
    track: str | None
    mode: str
    attached: bool


class FormContext(BaseModel):
    fields: int
    planned: int
    missing: list[str]
    filled: bool
    verified: bool


class JobContext(BaseModel):
    id: str
    company: str | None
    role: str
    url: str
    board: str | None
    status: str
    waiting_on: str
    blocked_reason: str | None
    cv: CvContext
    form: FormContext
    next: str


class JobEvent(BaseModel):
    ts: datetime
    kind: str
    actor: str
    payload: dict[str, Any]


class SubmitEvidence(BaseModel):
    """What the job tab showed when the owner said they submitted, captured read-only by the server. Untrusted page data."""

    capturedAt: str
    url: str
    title: str
    excerpt: str
    confirmation: Literal["seen", "not_seen"] | None = None
    hasScreenshot: bool


class UsageTotals(BaseModel):
    usd: float = Field(ge=0, allow_inf_nan=False)
    tokens: int = Field(ge=0)


class SubmitModeInfo(BaseModel):
    effective: Literal["review", "auto"]
    requested: Literal["review", "auto"]
    override: Literal["review", "auto"] | None = None
    reason: str | None = None


class JobDetail(JobContext):
    usage: UsageTotals
    submitEvidence: SubmitEvidence | None = None
    tracking: dict[str, Any] = {}
    detailsRefresh: dict[str, Any] | None = None  # {state, via, reason} of the last "Refresh details", if any
    posting: str
    location: str | None
    pay: str | None
    events: list[JobEvent]
    plan: list[dict[str, Any]]
    inventory: list[dict[str, Any]]
    agentEdits: list[dict[str, Any]]
    draftRevision: int
    tabOpen: bool = False
    submitMode: SubmitModeInfo
    parkSignal: Literal["looks_submitted", "tab_closed"] | None = None


class Track(BaseModel):
    id: str
    title: str
    keywords: list[str]
    tailorable: bool = False
    defaultMode: str
    market: Literal["any", "us", "morocco"] = "any"
    sha256: str | None
    jobs: int


class TrackSource(BaseModel):
    tex: str
    bytes: int


class History(BaseModel):
    sha: str
    date: str
    subject: str


class MissingField(BaseModel):
    """One form field an agent could not answer; label and options are untrusted page text, bounded server-side."""

    key: str
    label: str
    type: str
    options: list[str] = []
    optionsTotal: int | None = None  # distinct options the page offered; more than len(options) when the list was capped
    required: bool | None = None  # None: the form inventory never recorded it
    multiple: bool | None = None
    searchable: bool | None = None
    unmatchedValue: str | None = None
    generic: bool = False  # a stable, job-independent question (start date, salary, work authorization): "save for all jobs"


class BlockedJob(BaseModel):
    id: str
    company: str | None
    role: str
    board: str | None = None  # the job board / ATS display name (Ashby, Greenhouse…), None when the board is unknown
    reason: str | None
    missing: list[str] | None
    missingFields: list[MissingField] = []
    status: str
    submitEvidence: SubmitEvidence | None = None
    answersPending: int = 0  # answers saved from the dashboard that have not been sent to the agent yet
    tabOpen: bool = False  # this job has a live tab in Neo that the owner can find by its page id
    answeredCount: int = 0  # answers the owner already saved for this application (skips excluded)
    settledCount: int = 0  # questions the last run re-asked that already had an answer: sent back, not asked again
    step: str | None = None  # "step 2 of 8" when the agent's note says so


class ApprovalJob(BaseModel):
    id: str
    company: str | None
    role: str
    draftHash: str | None
    cvTrack: str | None
    clean: bool = False
    reasons: list[str] = []
    tabOpen: bool = False  # this job has a live tab in Neo that the owner can find by its page id


class NeedsYou(BaseModel):
    blocked: list[BlockedJob]
    readyToApprove: list[ApprovalJob]


class Profile(BaseModel):
    key: str
    value: Any
    source: str | None
    confirmedAt: datetime | None


class Fact(BaseModel):
    id: str
    claim: str
    source: str
    confirmedAt: datetime | None


class Answer(BaseModel):
    questionKey: str
    answer: str
    scope: str
    source: str
    confirmedAt: datetime


class Board(BaseModel):
    id: str
    displayName: str
    automation: Literal["auto", "assisted", "manual"]


class ActiveWorker(BaseModel):
    worker: str
    job: str
    since: int
    node: str | None
    started: bool
    elapsedSeconds: int = Field(ge=0)
    role: str | None = None
    company: str | None = None
    url: str | None = None
    step: str | None = None
    tabOpen: bool = False  # this job has a live tab in Neo that the owner can find by its page id


class UsageWindow(UsageTotals):
    runs: int = Field(ge=0)
    jobs: int = Field(ge=0)
    averageCostPerJob: float = Field(ge=0, allow_inf_nan=False)


class FailedRun(BaseModel):
    task: str
    job: str
    role: str | None
    company: str | None
    at: datetime
    node: str | None
    lastError: str


class Monitoring(BaseModel):
    today: UsageWindow
    last7Days: UsageWindow
    allTime: UsageWindow
    queueDepth: int = Field(ge=0)
    recentFailures: list[FailedRun]


class UpdateItem(BaseModel):
    id: int
    ts: datetime
    kind: str
    actor: str
    payload: dict[str, Any] | None
    jobId: str | None
    role: str | None
    company: str | None


class Updates(BaseModel):
    items: list[UpdateItem]
    next: int | None


class Automation(BaseModel):
    running: bool
    concurrency: int
    active: list[ActiveWorker]
    started: int
    finished: int
    failed: int
    workerOnline: bool
    idleReason: str | None
    monitoring: Monitoring
    workersOnline: int = Field(ge=0)
    activeRuns: int = Field(ge=0)
    browsersOpen: int  # browser sessions currently open on any node (running agents + parked approval forms)
    browserLimit: int  # maxOpenBrowsers: preparation stops being dispatched at this count
    capacity: int  # job slots across online nodes (sum of their Celery pool sizes, WORKER_CONCURRENCY per worker), 0 when no worker is online
    queueOnline: bool  # a fresh control heartbeat exists (only a process consuming from Redis writes it)
    browserOnline: bool  # a fresh heartbeat says Neo answers on this node


class Ingested(BaseModel):
    id: str
    created: bool
    board: str
    automation: str


class IngestError(BaseModel):
    url: str
    error: str


class EmailCodeRequested(BaseModel):
    ok: bool
    started: bool  # False: a verification run for this challenge is already open


class OwnerCompleted(BaseModel):
    ok: bool
    already: bool


class OwnerSubmitted(BaseModel):
    ok: bool
    already: bool
    confirmationSeen: bool | None = None


class TrackerRow(BaseModel):
    id: str
    company: str | None
    role: str
    appliedOn: str | None
    status: str
    userStatus: str | None
    waitingOn: str
    blockedReason: str | None
    dateContacted: str | None
    interviewOn: str | None
    followUpOn: str | None
    resumeLabel: str | None
    cvTrack: str | None
    cvMode: str
    submittedVia: str | None
    jobLevel: str | None
    salary: str | None
    location: str | None
    workMode: str | None
    employmentType: str | None
    url: str
    source: str
    notes: str | None
    hasDescription: bool
    updatedAt: datetime
    archivedAt: datetime | None = None
    missing: list[str] = []
    tabOpen: bool = False  # this job has a live tab in Neo that the owner can find by its page id


class LoggedApplication(BaseModel):
    id: str | None = None
    filled: bool | None = None
    needsReview: bool | None = None
    url: str | None = None
    created: bool | None = None
    captureQueued: bool | None = None
    error: str | None = None


class ImportResult(BaseModel):
    rows: int
    created: int
    updated: int
    errors: list[dict[str, Any]]
    needsReview: int = 0


class TrackingChange(BaseModel):
    ok: bool
    changed: list[str]


class DuplicateRow(BaseModel):
    id: str
    company: str | None
    role: str
    url: str
    status: str
    appliedOn: str | None
    updatedAt: datetime


class DuplicateGroup(BaseModel):
    key: str
    rows: list[DuplicateRow]


class FactCreated(BaseModel):
    id: str
    created: bool


class TrackSaved(BaseModel):
    track: str
    sha: str
    versionId: int
    tailorable: bool


class TrackRemoved(BaseModel):
    ok: bool
    moved: int


class ProfileSectionSaved(BaseModel):
    entries: list[dict[str, Any]]


class AnswerBankItem(BaseModel):
    key: str
    title: str
    group: str
    options: list[str]


class TabFocus(BaseModel):
    focused: bool
    reason: Literal["no_tab", "unsupported", "focused"]
    page: str | None = None  # the Neo page id of the job's tab, for the owner to pick in the Neo cockpit
    title: str | None = None  # the page title (untrusted display text) so the owner can spot the tab
    group: str | None = None  # the Neo tab group that holds the tab, e.g. "jobpilot/job-applications"
    color: str | None = None  # that group's colour in the tab strip


class DetailsRequested(BaseModel):
    ok: bool
    already: bool = False
    state: str = "queued"  # queued | running | done | failed


class MissingDetailsRequested(BaseModel):
    ok: bool
    queued: int
    limit: int


class CredentialMeta(BaseModel):
    """A stored login without its password."""

    id: int
    origin: str
    username: str
    state: Literal["pending", "active", "failed", "retired"]
    source: Literal["generated", "imported", "manual"]
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
    last_verified_at: datetime | None


class CredentialReveal(BaseModel):
    password: str


class CredentialImportItem(BaseModel):
    origin: str
    username: str


class CredentialImportResult(BaseModel):
    committed: bool
    found: int
    toImport: int
    imported: int
    skippedExisting: int
    skippedOutOfScope: int
    skippedInvalid: int
    items: list[CredentialImportItem]

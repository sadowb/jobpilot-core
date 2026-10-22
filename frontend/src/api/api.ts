import { request, requestOk } from './http';
import type {
  Answer,
  AnswerBankItem,
  AnswerDeleteRequest,
  AnswerUpsertRequest,
  AppliedRequest,
  Automation,
  Board,
  BoardUpdateRequest,
  DuplicateGroup,
  Fact,
  FactConfirmRequest,
  FactCreated,
  FactCreateRequest,
  ImportRequest,
  DetailsRequested,
  EmailCodeRequested,
  MissingDetailsRequested,
  OwnerCompleted,
  OwnerSubmitted,
  ImportResult,
  IngestRequest,
  IngestResult,
  JobAnswersRequest,
  JobDetail,
  JobListItem,
  JobSettingsRequest,
  JobStatusRequest,
  LoggedApplication,
  MergeRequest,
  NeedsYou,
  OkResponse,
  Profile,
  ProfileSectionRequest,
  ProfileSectionSaved,
  ProfileSetRequest,
  RuntimeSettings,
  SubmitMode,
  StatsResponse,
  Track,
  TrackCreateRequest,
  TrackEditRequest,
  TrackFileRequest,
  TrackHistory,
  TrackingChange,
  TrackingUpdate,
  TrackRemoved,
  TrackRemoveRequest,
  TrackSaved,
  TrackSource,
  TrackerRow,
  UpdatesResponse,
  TabFocus,
} from './models';

// Existing imports from api.ts can still access the shared session/error helpers.
export { ApiError, initSession, SessionRequiredError } from './http';
export type Market = 'any' | 'us' | 'morocco';

// Encode an ID as one URL segment (for example, a space becomes %20).
function encodeId(id: string): string {
  return encodeURIComponent(id);
}

// Each method describes one backend operation. request() adds /api/v1.
export const api = {
  // Jobs and human approval.
  stats() {
    return request<StatsResponse>('/stats');
  },
  jobs(params: URLSearchParams) {
    return request<JobListItem[]>(`/jobs?${params}`);
  },
  job(id: string) {
    return request<JobDetail>(`/jobs/${encodeId(id)}`);
  },
  needsYou() {
    return request<NeedsYou>('/needs-you');
  },
  ingest(body: IngestRequest) {
    return request<IngestResult[]>('/ingest', 'POST', body);
  },
  ownerSubmitted(id: string) {
    return request<OwnerSubmitted>(`/jobs/${encodeId(id)}/owner-submitted`, 'POST', {});
  },
  requestEmailCode(id: string) {
    return request<EmailCodeRequested>(`/jobs/${encodeId(id)}/email-code`, 'POST', {});
  },
  refreshDetails(id: string) {
    return request<DetailsRequested>(`/jobs/${encodeId(id)}/refresh-details`, 'POST', {});
  },
  refreshMissingDetails() {
    return request<MissingDetailsRequested>('/jobs/refresh-missing-details', 'POST', {});
  },
  ownerCompleted(id: string) {
    return request<OwnerCompleted>(`/jobs/${encodeId(id)}/owner-completed`, 'POST', {});
  },
  saveJobSettings(id: string, body: JobSettingsRequest) {
    return requestOk(`/jobs/${encodeId(id)}/settings`, body);
  },
  dismissParkSignal(id: string) {
    return requestOk(`/jobs/${encodeId(id)}/park-signal/dismiss`);
  },
  reopenJob(id: string) {
    return requestOk(`/jobs/${encodeId(id)}/reopen`);
  },
  saveSubmitMode(id: string, mode: SubmitMode | null) {
    return requestOk(`/jobs/${encodeId(id)}/submit-mode`, { mode });
  },
  saveJobAnswers(id: string, body: JobAnswersRequest) {
    return requestOk(`/jobs/${encodeId(id)}/answers`, body);
  },
  resumeJob(id: string, tryAnyway = false) {
    return requestOk(`/jobs/${encodeId(id)}/resume${tryAnyway ? '?try_anyway=true' : ''}`);
  },
  acknowledgeUnapprovedSubmit(id: string) {
    return requestOk(`/jobs/${encodeId(id)}/unapproved-submit/acknowledge`);
  },
  guideAgent(id: string, message: string) {
    return requestOk(`/jobs/${encodeId(id)}/guidance`, { message });
  },
  focusJobTab(id: string) {
    return request<TabFocus>(`/jobs/${encodeId(id)}/tab/focus`, 'POST', {});
  },
  confirmJobSubmitted(id: string) {
    return requestOk(`/jobs/${encodeId(id)}/user-confirmed`);
  },
  jobStatus(id: string, body: JobStatusRequest) {
    return requestOk(`/jobs/${encodeId(id)}/status`, body);
  },

  // Profile and reusable answers.
  countries() {
    return request<string[]>('/countries');
  },
  answerBank() {
    return request<AnswerBankItem[]>('/answer-bank');
  },
  profile() {
    return request<Profile[]>('/profile');
  },
  facts() {
    return request<Fact[]>('/facts');
  },
  answers() {
    return request<Answer[]>('/answers');
  },
  saveProfile(key: string, value: string) {
    const body: ProfileSetRequest = { key, value };
    return requestOk('/profile', body);
  },
  confirmFact(id: string, confirmed: boolean, claim: string) {
    const body: FactConfirmRequest = { confirmed, claim };
    return requestOk(`/facts/${encodeId(id)}/confirm`, body);
  },
  saveSection(
    section: 'education' | 'experience' | 'languages' | 'citizenships',
    entries: Record<string, unknown>[],
  ) {
    const body: ProfileSectionRequest = { entries };
    return request<ProfileSectionSaved>(`/profile/sections/${section}`, 'PUT', body);
  },
  clearProfile(key: string) {
    return request<OkResponse>(`/profile/${encodeId(key)}`, 'DELETE');
  },
  createFact(claim: string) {
    const body: FactCreateRequest = { claim };
    return request<FactCreated>('/facts', 'POST', body);
  },
  deleteFact(id: string) {
    return request<OkResponse>(`/facts/${encodeId(id)}`, 'DELETE');
  },
  saveAnswer(questionKey: string, answer: string, scope = 'global') {
    const body: AnswerUpsertRequest = { questionKey, answer, scope };
    return request<OkResponse>('/answers', 'PUT', body);
  },
  deleteAnswer(questionKey: string, scope = 'global') {
    const body: AnswerDeleteRequest = { questionKey, scope };
    return request<OkResponse>('/answers', 'DELETE', body);
  },

  // Application tracking.
  tracker(params: URLSearchParams) {
    return request<TrackerRow[]>(`/tracker?${params}`);
  },
  logApplied(body: AppliedRequest) {
    return request<LoggedApplication[]>('/tracker/applied', 'POST', body);
  },
  importTracker(text: string) {
    const body: ImportRequest = { text };
    return request<ImportResult>('/tracker/import', 'POST', body);
  },
  updateTracking(id: string, body: TrackingUpdate) {
    return request<TrackingChange>(`/jobs/${encodeId(id)}/tracking`, 'PATCH', body);
  },
  archiveJob(id: string) {
    return request<OkResponse>(`/jobs/${encodeId(id)}`, 'DELETE');
  },
  restoreJob(id: string) {
    return request<OkResponse>(`/jobs/${encodeId(id)}/restore`, 'POST');
  },
  purgeJob(id: string) {
    return request<OkResponse>(`/jobs/${encodeId(id)}/permanent`, 'DELETE');
  },
  duplicates() {
    return request<DuplicateGroup[]>('/tracker/duplicates');
  },
  mergeJobs(keepId: string, mergeIds: string[]) {
    const body: MergeRequest = { keepId, mergeIds };
    return request<OkResponse>('/tracker/merge', 'POST', body);
  },

  // CV tracks and files.
  tracks() {
    return request<Track[]>('/tracks');
  },
  history(id: string) {
    return request<TrackHistory[]>(`/tracks/${encodeId(id)}/history`);
  },
  createTrack(body: TrackCreateRequest) {
    return request<TrackSaved>('/tracks', 'POST', body);
  },
  replaceTrackCv(id: string, body: TrackFileRequest) {
    return request<TrackSaved>(`/tracks/${encodeId(id)}/cv`, 'PUT', body);
  },
  trackSource(id: string) {
    return request<TrackSource>(`/tracks/${encodeId(id)}/tex`);
  },
  editTrack(id: string, body: TrackEditRequest) {
    return request<OkResponse>(`/tracks/${encodeId(id)}`, 'PATCH', body);
  },
  removeTrack(id: string, reassignTo?: string) {
    const body: TrackRemoveRequest = { reassignTo: reassignTo ?? null };
    return request<TrackRemoved>(`/tracks/${encodeId(id)}`, 'DELETE', body);
  },

  // Automation settings and activity.
  boards() {
    return request<Board[]>('/boards');
  },
  saveBoard(id: string, automation: Board['automation']) {
    const body: BoardUpdateRequest = { automation };
    return requestOk(`/boards/${encodeId(id)}`, body);
  },
  updates(before?: number) {
    const query = before ? `?before=${before}` : '';
    return request<UpdatesResponse>(`/updates${query}`);
  },
  automation() {
    return request<Automation>('/automation');
  },
  settings() {
    return request<RuntimeSettings>('/settings');
  },
  // Partial on purpose: the server merges omitted fields with the stored values.
  saveSettings(body: Partial<RuntimeSettings>) {
    return request<RuntimeSettings>('/settings', 'PUT', body);
  },
  toggleAutomation(running: boolean) {
    // The argument is the CURRENT state: stop if running, start otherwise.
    const action = running ? 'stop' : 'start';
    return request<Automation>(`/automation/${action}`, 'POST');
  },
};

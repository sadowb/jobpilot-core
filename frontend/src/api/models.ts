import type { components, paths } from './types';

// Readable names for generated contracts; keep all wire shapes in types.ts.
type Schemas = components['schemas'];
export type Answer = Schemas['Answer'];
export type Automation = Schemas['Automation'];
export type Board = Schemas['Board'];
export type Monitoring = Schemas['Monitoring'];
export type UsageWindow = Schemas['UsageWindow'];
export type Fact = Schemas['Fact'];
export type JobDetail = Schemas['JobDetail'];
export type NeedsYou = Schemas['NeedsYou'];
export type Profile = Schemas['Profile'];
export type ReadyJob = Schemas['ApprovalJob'];
export type RuntimeSettings = Schemas['RuntimeSettings'];
export type TabFocus = Schemas['TabFocus'];
export type Track = Schemas['Track'];
export type TrackerRow = Schemas['TrackerRow'];
export type DuplicateGroup = Schemas['DuplicateGroup'];
export type TrackingUpdate = Schemas['TrackingUpdate'];
export type SubmitMode = Schemas['JobSubmitMode']['mode'] & string;
export type SubmitModeInfo = Schemas['SubmitModeInfo'];
export type ParkSignal = NonNullable<Schemas['JobDetail']['parkSignal']>;
export type CvMode = NonNullable<Schemas['JobSettings']['cvMode']>;
export type JobStatus = Schemas['JobStatusRequest']['status'];

export type ResponseFor<
  P extends keyof paths,
  M extends keyof paths[P] = 'get' & keyof paths[P],
> = paths[P][M] extends { responses: { 200: { content: { 'application/json': infer T } } } }
  ? T
  : never;
export type BodyFor<
  P extends keyof paths,
  M extends keyof paths[P] = 'post' & keyof paths[P],
> = paths[P][M] extends { requestBody: { content: { 'application/json': infer T } } } ? T : never;
export type UpdateItem = Schemas['UpdateItem'];
export type ActiveWorker = Schemas['ActiveWorker'];

// HTTP inputs and outputs use the backend's generated shapes, with ordinary names.
export type OkResponse = Schemas['Ok'];
export type SessionResponse = Schemas['SessionResponse'];
export type StatsResponse = Schemas['StatsResponse'];
export type JobListItem = Schemas['JobListItem'];
export type AnswerBankItem = Schemas['AnswerBankItem'];
export type TrackHistory = Schemas['History'];
export type UpdatesResponse = Schemas['Updates'];
export type IngestRequest = Schemas['IngestRequest'];
export type IngestResult = Schemas['Ingested'] | Schemas['IngestError'];
export type EmailCodeRequested = Schemas['EmailCodeRequested'];
export type DetailsRequested = Schemas['DetailsRequested'];
export type MissingDetailsRequested = Schemas['MissingDetailsRequested'];
export type OwnerCompleted = Schemas['OwnerCompleted'];
export type OwnerSubmitted = Schemas['OwnerSubmitted'];
export type JobSettingsRequest = Schemas['JobSettings'];
export type JobAnswersRequest = Schemas['AnswersRequest'];
export type JobStatusRequest = Schemas['JobStatusRequest'];
export type ProfileSetRequest = Schemas['ProfileSet'];
export type FactConfirmRequest = Schemas['FactConfirm'];
export type BoardUpdateRequest = Schemas['BoardUpdate'];
export type AppliedRequest = Schemas['AppliedRequest'];
export type LoggedApplication = Schemas['LoggedApplication'];
export type ImportRequest = Schemas['ImportRequest'];
export type ImportResult = Schemas['ImportResult'];
export type TrackingChange = Schemas['TrackingChange'];
export type MergeRequest = Schemas['MergeRequest'];
export type TrackCreateRequest = Schemas['TrackCreate'];
export type TrackFileRequest = Schemas['TrackFile'];
export type TrackEditRequest = Schemas['TrackEdit'];
export type TrackRemoveRequest = Schemas['TrackRemove'];
export type TrackSaved = Schemas['TrackSaved'];
export type TrackSource = Schemas['TrackSource'];
export type TrackRemoved = Schemas['TrackRemoved'];
export type ProfileSectionRequest = Schemas['ProfileSection'];
export type ProfileSectionSaved = Schemas['ProfileSectionSaved'];
export type FactCreateRequest = Schemas['FactCreate'];
export type FactCreated = Schemas['FactCreated'];
export type AnswerUpsertRequest = Schemas['AnswerUpsert'];
export type AnswerDeleteRequest = Schemas['AnswerRef'];

# Reading the frontend API client

Start with `frontend/src/api/api.ts`. It is a catalogue of functions the dashboard can call. It does not define backend routes or run agents. The backend implements the routes; this file sends HTTP requests to them.

The files have three responsibilities:

| File | Responsibility |
| --- | --- |
| `frontend/src/api/api.ts` | Named operations such as `job`, `ingest`, and `resumeJob` |
| `frontend/src/api/http.ts` | Session setup, cookies/CSRF, fetch, JSON, and HTTP errors |
| `frontend/src/api/models.ts` | Readable names for request and response types |
| `frontend/src/api/types.ts` | Generated backend contracts; do not edit by hand |

This is ordinary TypeScript. React components call it, but these files do not use React hooks or JSX.

## One request from start to finish

```ts
const job = await api.job('example-job-id');
```

1. `api.job` builds `/jobs/example-job-id`.
2. `request` waits for session setup.
3. `sendRequest` adds `/api/v1`, producing `/api/v1/jobs/example-job-id`.
4. The browser sends a GET request with its same-origin session cookie.
5. FastAPI processes the request and sends JSON.
6. `decode` checks the HTTP status and reads the JSON.
7. The promise resolves to a `JobDetail`, or rejects with an error.

The component or its query/mutation hook then decides how to display the result. This client does not display toasts or update React state. `hooks/useAction.ts` handles mutation messages and cache refreshes; `app/providers.tsx` configures TanStack Query retry behavior.

## Imports and the API object

`import type { JobDetail } from './models'` imports a name for the TypeScript checker. Types are removed from the JavaScript sent to the browser.

`export const api = { ... }` creates and exports a normal JavaScript object. Its properties are functions. A component imports it and calls `api.job(id)`.

```ts
job(id: string) {
  return request<JobDetail>(`/jobs/${encodeId(id)}`);
}
```

- `job` is the method name.
- `id: string` says the argument must be text.
- `return` gives the caller the promise produced by `request`.
- `<JobDetail>` tells TypeScript the expected JSON result shape.
- Backticks create a template string; `${...}` inserts a value.
- `encodeId` makes an ID safe to use as one URL segment. For example, a space becomes `%20` and a slash becomes `%2F`.

A method returning `request(...)` does not need to be marked `async`: `request` already returns a promise. `await` belongs where the caller needs its eventual value.

## Your selected code before the simplification

```ts
ingest: (body: BodyFor<'/api/v1/ingest'>) =>
  request<ResponseFor<'/api/v1/ingest', 'post'>>('/ingest', 'POST', body)
```

`ingest:` is an object property whose value is an arrow function. `=>` separates its arguments from its implementation. Without braces, the expression after the arrow is returned automatically.

`BodyFor<'/api/v1/ingest'>` looks up the generated POST request-body type. `ResponseFor<'/api/v1/ingest', 'post'>` looks up its generated successful response type. The lowercase `post` is a TypeScript lookup key; uppercase `POST` is the actual HTTP method passed to fetch. Neither type lookup sends a network request.

The simpler current spelling is:

```ts
ingest(body: IngestRequest) {
  return request<IngestResult[]>('/ingest', 'POST', body);
}
```

`IngestRequest` and `IngestResult` still come from the generated backend types. `IngestResult[]` means an array of results. Each result can contain a saved job ID or an individual link error. An HTTP 200 does not guarantee every link succeeded.

Example:

```ts
const results = await api.ingest({
  urls: ['https://example.com/jobs/123'],
  cvMode: 'base',
});
```

The original approval code used `BodyFor<...>['jobIds']`. This extracts the type of the `jobIds` property: here, `string[]`. `{ jobIds, scope }` is shorthand for `{ jobIds: jobIds, scope: scope }`. `satisfies SomeType` checks an object's type during development; it does not validate it in the browser or modify the JSON.

The current approval method uses an explicit variable:

```ts
approve(jobIds: string[], scope: string) {
  const body: ApproveSubmitRequest = { jobIds, scope };
  return request<ApprovalResult[]>('/approvals/submit', 'POST', body);
}
```

This sends the IDs and approval scope to the server. The server decides whether each draft can be approved. The frontend cannot bypass its checks.

The old `jobAction` used `...[id, action, body]` plus a union of tuples. A tuple describes an ordered argument list. The rest syntax collected function arguments and destructuring named them. The union (`|`) allowed different combinations:

- Settings: ID, the literal `'settings'`, and a settings body.
- Answers: ID, the literal `'answers'`, and an answers body.
- Resume or user confirmation: ID and action, without a body.

That protected against sending an answers body to the settings endpoint, but it was difficult to read. It has been replaced with four methods:

```ts
api.saveJobSettings(id, { cvMode: 'base' });
api.saveJobAnswers(id, {
  answers: [{ question: 'field-key', answer: 'Yes', scope: 'global' }],
});
api.resumeJob(id);
api.confirmJobSubmitted(id);
```

Each method now accepts the appropriate argument types directly.

## Every method in api.ts

All paths below receive the `/api/v1` prefix. `{id}` means a URL segment filled in from the function argument. Query parameters come from `URLSearchParams`.

| Method | HTTP request | Purpose |
| --- | --- | --- |
| `stats` | GET `/stats` | Dashboard counts |
| `jobs` | GET `/jobs?…` | Filtered/paginated jobs |
| `job` | GET `/jobs/{id}` | One job's detail |
| `needsYou` | GET `/needs-you` | Blocked jobs and drafts awaiting approval |
| `ingest` | POST `/ingest` | Add links and queue work |
| `approve` | POST `/approvals/submit` | Ask the backend to approve the listed drafts |
| `saveJobSettings` | POST `/jobs/{id}/settings` | Save a job's CV choices |
| `saveJobAnswers` | POST `/jobs/{id}/answers` | Save answers for an application |
| `resumeJob` | POST `/jobs/{id}/resume` | Make a job available for automation again |
| `confirmJobSubmitted` | POST `/jobs/{id}/user-confirmed` | Report that you submitted manually |
| `jobStatus` | POST `/jobs/{id}/status` | Request a job status change |
| `countries` | GET `/countries` | Supported country names |
| `answerBank` | GET `/answer-bank` | Definitions of reusable questions |
| `profile` | GET `/profile` | Saved profile entries |
| `facts` | GET `/facts` | Saved personal facts |
| `answers` | GET `/answers` | Saved reusable answers |
| `accounts` | GET `/accounts` | Account metadata |
| `saveProfile` | POST `/profile` | Save one profile key/value |
| `confirmFact` | POST `/facts/{id}/confirm` | Update a fact's confirmation and claim |
| `approveAccount` | POST `/account-approvals` | Approve account creation for an origin |
| `deleteAccount` | DELETE `/accounts/{id}` | Delete an account through the backend |
| `saveSection` | PUT `/profile/sections/{section}` | Replace education, experience, languages, or citizenships |
| `clearProfile` | DELETE `/profile/{key}` | Clear a profile entry |
| `createFact` | POST `/facts` | Add a fact |
| `deleteFact` | DELETE `/facts/{id}` | Remove a fact |
| `saveAnswer` | PUT `/answers` | Save a reusable answer; scope defaults to `global` |
| `deleteAnswer` | DELETE `/answers` | Remove an answer for a scope |
| `tracker` | GET `/tracker?…` | Application tracking rows |
| `logApplied` | POST `/tracker/applied` | Log jobs already applied to |
| `importTracker` | POST `/tracker/import` | Import tracking text |
| `updateTracking` | PATCH `/jobs/{id}/tracking` | Update supplied tracking fields |
| `archiveJob` | DELETE `/jobs/{id}` | Archive a job |
| `restoreJob` | POST `/jobs/{id}/restore` | Restore an archived job |
| `purgeJob` | DELETE `/jobs/{id}/permanent` | Permanently remove it through the backend |
| `duplicates` | GET `/tracker/duplicates` | Find possible duplicate rows |
| `mergeJobs` | POST `/tracker/merge` | Request merging rows into a kept row |
| `tracks` | GET `/tracks` | CV tracks |
| `history` | GET `/tracks/{id}/history` | CV version history |
| `createTrack` | POST `/tracks` | Create a CV track |
| `replaceTrackCv` | PUT `/tracks/{id}/cv` | Replace the track's PDF/TeX input |
| `trackSource` | GET `/tracks/{id}/tex` | Read TeX source |
| `editTrack` | PATCH `/tracks/{id}` | Update supplied track fields |
| `removeTrack` | DELETE `/tracks/{id}` | Delete a track, with optional reassignment |
| `boards` | GET `/boards` | Job website policies and limits |
| `saveBoard` | POST `/boards/{id}` | Save a site's policy and daily limit |
| `updates` | GET `/updates?before=…` | Activity feed with optional pagination |
| `automation` | GET `/automation` | Worker state and budget information |
| `settings` | GET `/settings` | Runtime settings |
| `saveSettings` | PUT `/settings` | Save runtime settings |
| `toggleAutomation` | POST `/automation/start` or `/stop` | Toggle from the supplied current running state |

`toggleAutomation(true)` requests **stop**, because the argument says automation is currently running. It is not a desired-state argument.

For a filtered list:

```ts
const filters = new URLSearchParams({ waitingOn: 'you', limit: '50' });
const jobs = await api.jobs(filters);
```

`URLSearchParams` handles URL encoding for query values. `encodeId` handles IDs inside the path. They solve different parts of URL construction.

Some requests have no body. Others send an object such as `{ key, value }`. `removeTrack` turns an omitted reassignment into `null` using `??`; DELETE requests can have a JSON body where this backend expects one.

## The shared HTTP helper, fully explained

The previous top half of api.ts now lives in `http.ts`. The endpoint catalogue can be read without first understanding session coordination.

- `apiPrefix`: every application request starts with `/api/v1`.
- `csrf`: the current CSRF token, stored in memory. Writes attach it as `X-CSRF-Token`. The authentication cookie is sent separately by the browser.
- `sessionPromise`: one shared session setup operation. If several screens ask for data simultaneously, they wait for the same operation. A completed promise is reused until a refresh is requested.
- `refreshPromise`: one shared refresh operation when several requests discover an expired session.
- `sessionReloadKey`: a browser-storage flag preventing repeated automatic reloads.
- `HttpMethod`: the allowed strings GET, POST, PUT, PATCH, and DELETE.
- `SessionRequiredError`: a specific error when session setup returns 401. `app/App.tsx` can show the corresponding session message.
- `ApiError`: an error containing both a readable message and the HTTP status. TanStack Query uses that status when deciding whether to retry reads.
- `reloadForSession`: navigates to `/` once to allow the backend to establish the navigation session. Without working session storage, it skips automatic navigation to avoid a loop.
- `errorMessage`: uses the backend's `{ error: 'message' }` response when available, otherwise supplies `Request failed (404)`, for example.
- `decode<T>`: reads JSON; turns an unsuccessful HTTP response into `ApiError`; returns successful data with the expected TypeScript type.
- `loadSession`: fetches `/api/v1/session`, checks that its token is nonempty, stores it, and clears the reload guard. It handles session 401 separately.
- `initSession(force)`: starts session loading if needed. `force = true` requests a fresh load. Failure clears the cached promise, allowing another attempt.
- `refreshSession(previousToken)`: if another request already changed the token, it returns immediately. Otherwise it starts or joins the shared refresh and clears the refresh promise when it finishes.
- `sendRequest`: builds fetch options. It sends cookies to the same origin, adds JSON/CSRF headers for writes, serialises a supplied body, and adds the API prefix.
- `request<T>`: waits for the session, sends the request, refreshes and retries once on 401, then decodes the result. It does not automatically retry 403, 404, or 500 here.
- `requestOk`: calls `request<OkResponse>` for endpoints returning `{ ok: true }`. It also rejects `{ ok: false }`, even if the HTTP status is 200. Other methods return their response directly, including batch results that callers need to inspect individually.

`fetch` resolves for HTTP errors like 404; it rejects for network failures. This explains why `decode` must explicitly check `response.ok`.

```ts
try {
  const job = await api.job('example-job-id');
  // Use job in your component or hook.
} catch (error) {
  if (error instanceof ApiError && error.status === 404) {
    // Show "This job does not exist."
  }
}
```

In that example, import `ApiError` from `api/api.ts` along with `api`. A `catch` block also needs to handle other errors appropriately in real UI code; `useAction` already displays mutation errors.

## TypeScript punctuation you will encounter

| Syntax | Meaning |
| --- | --- |
| `string[]` | An array of strings |
| `TypeA \| TypeB` | Either type is allowed |
| `'GET' \| 'POST'` | Only these exact strings are allowed |
| `body?: unknown` | The body is optional; inspect it before accessing properties |
| `Promise<JobDetail>` | A future result containing job details |
| `Promise<void>` | A future completion with no result value |
| `async` / `await` | Wait for promises without blocking the browser's event loop |
| `<T>` | A placeholder for the expected result type |
| `as T` | Tell TypeScript to treat a value as T; no runtime validation |
| `Account['id']` | The type of the account's ID property |
| `Record<string, unknown>` | An object with string keys whose value types are unknown |
| `result?.csrf` | Read csrf only if result is not null or undefined |
| `value ?? null` | Use null if value is null or undefined |
| `condition ? a : b` | Choose a when true, otherwise b |
| `.catch(...)` | Handle a rejected promise |
| `.finally(...)` | Run cleanup whether the promise succeeds or fails |
| `readonly status` | TypeScript prevents later assignment to this property |

The advanced `BodyFor` and `ResponseFor` utilities remain in models.ts for looking up generated route types. They use `keyof` to restrict route names, `extends` to match a type shape, and `infer T` to extract its payload type. Reading endpoint functions no longer requires reading those utilities first. Named aliases such as `IngestRequest` reference the same generated schema.

TypeScript checks code before it runs. It cannot prove that an actual server response matches its declared shape. The backend validates HTTP inputs and outputs with Pydantic; the browser currently trusts successful response JSON.

## References

- [MDN: Using fetch](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch)
- [TypeScript: Functions and generics](https://www.typescriptlang.org/docs/handbook/2/functions.html)
- [TypeScript: Indexed access types](https://www.typescriptlang.org/docs/handbook/2/indexed-access-types.html)

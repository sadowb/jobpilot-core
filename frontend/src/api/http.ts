import type { OkResponse, SessionResponse } from './models';

const apiPrefix = '/api/v1';
const sessionReloadKey = 'jobpilot-session-reload';

// The browser sends the session cookie. We keep its CSRF token in memory.
let csrf = '';
// Several components can share one session request or refresh at the same time.
let sessionPromise: Promise<void> | undefined;
let refreshPromise: Promise<void> | undefined;

type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export class SessionRequiredError extends Error {
  constructor() {
    super('Open JobPilot at http://127.0.0.1:4100 in a normal browser window.');
  }
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function reloadForSession(): void {
  try {
    // Only reload once: a missing session must not create an endless reload loop.
    if (sessionStorage.getItem(sessionReloadKey)) return;
    sessionStorage.setItem(sessionReloadKey, '1');
    window.location.replace('/');
  } catch {
    // If storage is unavailable, we cannot safely remember that we reloaded.
  }
}

function errorMessage(body: unknown, status: number): string {
  if (body !== null && typeof body === 'object' && 'error' in body) {
    if (typeof body.error === 'string') return body.error;
  }
  return `Request failed (${status})`;
}

async function decode<T>(response: Response): Promise<T> {
  const body: unknown = await response.json().catch(() => null);

  // fetch() does not throw for HTTP errors such as 404; we check them ourselves.
  if (!response.ok) {
    throw new ApiError(errorMessage(body, response.status), response.status);
  }

  // T describes the expected result. This assertion does not validate JSON.
  return body as T;
}

async function loadSession(): Promise<void> {
  const response = await fetch(`${apiPrefix}/session`, { credentials: 'same-origin' });
  if (response.status === 401) {
    reloadForSession();
    throw new SessionRequiredError();
  }

  const result = await decode<SessionResponse>(response);
  if (typeof result?.csrf !== 'string' || !result.csrf) {
    throw new Error('The session returned no CSRF token.');
  }
  csrf = result.csrf;

  try {
    sessionStorage.removeItem(sessionReloadKey);
  } catch {
    // A valid session can still work when browser storage is unavailable.
  }
}

export function initSession(force = false): Promise<void> {
  if (force) sessionPromise = undefined;

  if (!sessionPromise) {
    sessionPromise = loadSession().catch((error) => {
      // Allow another attempt after a failed session request.
      sessionPromise = undefined;
      throw error;
    });
  }
  return sessionPromise;
}

function refreshSession(previousToken: string): Promise<void> {
  // Another request may already have refreshed the token while this one waited.
  if (csrf !== previousToken) return Promise.resolve();

  if (!refreshPromise) {
    refreshPromise = initSession(true).finally(() => {
      refreshPromise = undefined;
    });
  }
  return refreshPromise;
}

function sendRequest(path: string, method: HttpMethod, body?: unknown): Promise<Response> {
  const options: RequestInit = { method, credentials: 'same-origin' };

  if (method !== 'GET') {
    options.headers = {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrf,
    };
  }
  if (body !== undefined) {
    options.body = JSON.stringify(body);
  }

  return fetch(`${apiPrefix}${path}`, options);
}

export async function request<T>(
  path: string,
  method: HttpMethod = 'GET',
  body?: unknown,
): Promise<T> {
  await initSession();
  const originalToken = csrf;
  let response = await sendRequest(path, method, body);

  // Retry once after authentication fails; other HTTP errors go to decode().
  if (response.status === 401) {
    await refreshSession(originalToken);
    response = await sendRequest(path, method, body);
  }

  return decode<T>(response);
}

// These endpoints return { ok: true }. Reject a false result as well.
export async function requestOk(
  path: string,
  body?: unknown,
  method: HttpMethod = 'POST',
): Promise<OkResponse> {
  const result = await request<OkResponse>(path, method, body);
  if (!result.ok) throw new Error('The change was not accepted.');
  return result;
}

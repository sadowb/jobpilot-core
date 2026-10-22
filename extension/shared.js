// Shared by the popup and the background worker. The dashboard address is fixed on purpose: the extension is only
// granted access to this one local origin (see host_permissions in manifest.json).
export const BASE = 'http://127.0.0.1:4100';

export async function getToken() {
  const { token } = await chrome.storage.local.get('token');
  return typeof token === 'string' && token.length >= 32 ? token : null;
}

export async function clearToken() {
  await chrome.storage.local.remove('token');
}

/** True for pages that can be a job link. Browser-internal and local-file pages are refused. */
export function isSendable(url) {
  try {
    const { protocol } = new URL(url);
    return protocol === 'http:' || protocol === 'https:';
  } catch {
    return false;
  }
}

/**
 * Send one link. Resolves to {kind, ...}:
 *   added | duplicate  -> {id}
 *   unpaired           -> no token yet, or the dashboard rejected it
 *   offline            -> the dashboard is not running
 *   rejected | failed  -> {message}
 */
export async function sendLink(url, title) {
  const token = await getToken();
  if (!token) return { kind: 'unpaired' };
  let response;
  try {
    response = await fetch(`${BASE}/api/v1/capture`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, title: (title || '').slice(0, 300) }),
    });
  } catch {
    return { kind: 'offline' };
  }
  if (response.status === 401) {
    await clearToken();
    return { kind: 'unpaired' };
  }
  if (response.status === 429) return { kind: 'rejected', message: 'Too many links at once. Wait a minute.' };
  if (response.status === 422) return { kind: 'rejected', message: 'JobPilot cannot use this link.' };
  if (!response.ok) return { kind: 'failed', message: `Dashboard error (${response.status}).` };
  const data = await response.json();
  return { kind: data.created ? 'added' : 'duplicate', id: data.id };
}

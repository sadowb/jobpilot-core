/**
 * Pull job links out of pasted text. Pure parsing, no business rules: the server still validates every address
 * (HttpUrl), recognises the board and decides what a duplicate is.
 */
export const MAX_LINKS = 500;

export interface ExtractedLinks {
  /** Normalised, de-duplicated http(s) addresses in the order they were first seen (at most `max`). */
  links: string[];
  /** Fragments that looked like links but cannot be used, with the reason. */
  skipped: { text: string; reason: string }[];
  /** Repeats merged into an earlier link. */
  duplicates: number;
  /** Valid links dropped because of the cap. */
  overflow: number;
}

const NAMED_ENTITIES: Record<string, string> = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
  nbsp: ' ',
};

export function decodeEntities(text: string): string {
  if (!text.includes('&')) return text;
  return text.replace(
    /&(?:#(\d{1,7})|#x([\da-f]{1,6})|([a-z]{2,6}));/gi,
    (whole, dec, hex, name) => {
      if (name) return NAMED_ENTITIES[name.toLowerCase()] ?? whole;
      const code = dec ? Number.parseInt(dec, 10) : Number.parseInt(hex, 16);
      return code > 0 && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff)
        ? String.fromCodePoint(code)
        : whole;
    },
  );
}

// A link starts at http(s):// and runs to whitespace or a character that cannot be part of one. A second scheme inside
// the run starts the next link ("https://a.comhttps://b.com").
const CANDIDATE = /https?:\/\/(?:(?!https?:\/\/)[^\s<>"'` ​])+/gi;
// Something that was meant to be a link but has no http(s) scheme or a scheme we never open.
const LOOSE =
  /(?:^|[\s,;(<[])((?:www\.[^\s,;<>"']+)|(?:(?:ftp|file|javascript|data|mailto):[^\s,;<>"']+))/gi;

const TRAILING = /[.,;:!?'"’”*_]+$/;
const BRACKETS: [string, string][] = [
  [')', '('],
  [']', '['],
  ['}', '{'],
];

function trimTrailing(raw: string): string {
  let url = raw;
  for (;;) {
    const before = url;
    url = url.replace(TRAILING, '');
    for (const [close, open] of BRACKETS) {
      while (url.endsWith(close) && count(url, close) > count(url, open)) url = url.slice(0, -1);
    }
    if (url === before) return url;
  }
}

function count(text: string, char: string): number {
  let n = 0;
  for (const c of text) if (c === char) n += 1;
  return n;
}

const TRACKING_PARAM =
  /^(utm_[\w-]*|gclid|dclid|fbclid|msclkid|mc_cid|mc_eid|gh_src|lever-source|lever-origin)$/i;
// `ref` is only dropped when it is a plain source label (ref=linkedin); an id-like value may identify the posting.
const SOURCE_LABEL = /^[a-z][a-z_-]*$/i;

function isTracking(name: string, value: string): boolean {
  if (TRACKING_PARAM.test(name)) return true;
  return name.toLowerCase() === 'ref' && SOURCE_LABEL.test(value);
}

/** Lower-case host, no default port, no tracking parameters, no fragment (unless it is a client-side route). */
export function normalizeLink(raw: string): { url: string } | { error: string } {
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return { error: 'not a valid address' };
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:')
    return { error: 'not an http(s) link' };
  if (parsed.username || parsed.password) return { error: 'contains a login' };
  if (!parsed.hostname.includes('.') && !/^\[.*\]$/.test(parsed.hostname))
    return { error: 'no website name' };
  const kept = parsed.search
    .replace(/^\?/, '')
    .split('&')
    .filter((part) => {
      if (!part) return false;
      const eq = part.indexOf('=');
      const name = safeDecode(eq < 0 ? part : part.slice(0, eq));
      const value = safeDecode(eq < 0 ? '' : part.slice(eq + 1));
      return !isTracking(name, value);
    });
  parsed.search = kept.length ? `?${kept.join('&')}` : '';
  if (!/^#[/!]/.test(parsed.hash)) parsed.hash = '';
  return { url: parsed.toString() };
}

function safeDecode(text: string): string {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

function dedupeKey(url: string): string {
  const parsed = new URL(url);
  const path = parsed.pathname.length > 1 ? parsed.pathname.replace(/\/+$/, '') : parsed.pathname;
  return `${parsed.protocol}//${parsed.host}${path}${parsed.search}${parsed.hash}`;
}

export function extractLinks(input: string, max: number = MAX_LINKS): ExtractedLinks {
  const text = decodeEntities(input);
  const result: ExtractedLinks = { links: [], skipped: [], duplicates: 0, overflow: 0 };
  const seen = new Set<string>();
  let consumed = text;
  for (const match of text.matchAll(CANDIDATE)) {
    const raw = trimTrailing(match[0]);
    consumed = consumed.replace(match[0], ' ');
    if (!/^https?:\/\/[^/?#]/i.test(raw)) continue;
    const normal = normalizeLink(raw);
    if ('error' in normal) {
      result.skipped.push({ text: raw, reason: normal.error });
      continue;
    }
    const key = dedupeKey(normal.url);
    if (seen.has(key)) {
      result.duplicates += 1;
      continue;
    }
    seen.add(key);
    if (result.links.length < max) result.links.push(normal.url);
    else result.overflow += 1;
  }
  for (const match of consumed.matchAll(LOOSE)) {
    const piece = trimTrailing(match[1] ?? '');
    if (piece) {
      result.skipped.push({
        text: piece,
        reason: /^www\./i.test(piece) ? 'missing https://' : 'not an http(s) link',
      });
    }
  }
  return result;
}

/** "host/path", shortened in the middle, for compact lists. The full address stays in a title attribute. */
export function shortLink(url: string, limit = 64): string {
  let label: string;
  try {
    const parsed = new URL(url);
    const path = parsed.pathname === '/' ? '' : parsed.pathname.replace(/\/+$/, '');
    label = `${parsed.host}${path}${parsed.search}`;
  } catch {
    label = url;
  }
  if (label.length <= limit) return label;
  const head = Math.ceil((limit - 1) * 0.6);
  return `${label.slice(0, head)}…${label.slice(label.length - (limit - 1 - head))}`;
}

export function chunk<T>(items: readonly T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

export const CHUNK_SIZE = 50;

/**
 * Send links in batches of `size`, strictly in order, so a long paste shows progress and one slow or failed request
 * cannot lose the rest. The result list always has one entry per link, in order: a batch whose request fails (and every
 * batch after it) is reported through `failed` and nothing more is sent.
 */
export async function sendInChunks<R>(
  links: readonly string[],
  send: (batch: string[]) => Promise<R[]>,
  failed: (link: string, message: string) => R,
  onProgress: (done: number, total: number) => void = () => {},
  size: number = CHUNK_SIZE,
): Promise<R[]> {
  const results: R[] = [];
  onProgress(0, links.length);
  const batches = chunk(links, size);
  for (let i = 0; i < batches.length; i += 1) {
    const batch = batches[i]!;
    try {
      const reply = await send(batch);
      // The server answers one entry per link; pad defensively so positions never shift.
      for (let j = 0; j < batch.length; j += 1) {
        results.push(reply[j] ?? failed(batch[j]!, 'No answer from the server for this link.'));
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      for (const rest of batches.slice(i))
        for (const link of rest) results.push(failed(link, message));
      break;
    }
    onProgress(results.length, links.length);
  }
  return results;
}

/**
 * Keeps an open tab from running an old build. The server's index.html names the current entry bundle; when it differs
 * from the one this tab loaded, the tab reloads itself as soon as that cannot lose anything the owner typed.
 */
const ENTRY = /\/assets\/index-[\w-]+\.js/;
const POLL_MS = 60_000;

const loadedEntry = (): string | null => {
  const script = document.querySelector<HTMLScriptElement>(
    'script[type="module"][src*="/assets/index-"]',
  );
  return script ? (ENTRY.exec(script.src)?.[0] ?? null) : null;
};

const hasUnsavedInput = (): boolean =>
  [
    ...document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(
      'input, textarea, select',
    ),
  ].some(
    (el) =>
      !(
        el instanceof HTMLInputElement &&
        ['search', 'checkbox', 'radio', 'button', 'submit'].includes(el.type)
      ) && el.value.trim() !== '',
  );

async function currentEntry(): Promise<string | null> {
  try {
    const response = await fetch('/', { cache: 'no-store', headers: { Accept: 'text/html' } });
    if (!response.ok) return null;
    return ENTRY.exec(await response.text())?.[0] ?? null;
  } catch {
    return null; // offline or restarting: try again later
  }
}

export function watchForNewBuild(): void {
  const mine = loadedEntry();
  if (!mine || window.location.port === '5173') return; // 5173 is the Vite dev server: no hashed bundles
  let pending = false;
  const check = async () => {
    if (pending) return;
    const latest = await currentEntry();
    if (!latest || latest === mine) return;
    pending = true;
    if (document.visibilityState === 'hidden' || !hasUnsavedInput()) window.location.reload();
    else pending = false; // the owner is mid-answer: look again at the next check
  };
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void check();
  });
  window.addEventListener('focus', () => void check());
  window.setInterval(() => void check(), POLL_MS);
}

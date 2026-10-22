// Blocking bootstrap for production's script-src 'self' CSP.
(() => {
  if (document.documentElement.dataset.theme) return;
  let saved;
  try { saved = localStorage.getItem('jobpilot-theme'); } catch { /* Storage may be unavailable. */ }
  let system = 'light';
  try { system = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'; } catch { /* Use the light fallback. */ }
  document.documentElement.dataset.theme = saved === 'light' || saved === 'dark' ? saved : system;
})();

import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import { AppProviders } from './app/providers';
import { initializeTheme } from './app/theme';
import { watchForNewBuild } from './app/versionWatch';
import './styles/tailwind.css';

initializeTheme();

// A rebuild replaces the hashed chunk files; a tab opened before it fails to load lazy pages and looks empty.
// Reload once (guarded, so a genuinely missing file cannot loop) to pick up the current build.
window.addEventListener('vite:preloadError', (event) => {
  event.preventDefault();
  const last = Number(sessionStorage.getItem('jp-chunk-reload') ?? 0);
  if (Date.now() - last > 15_000) {
    sessionStorage.setItem('jp-chunk-reload', String(Date.now()));
    window.location.reload();
  }
});
watchForNewBuild();
createRoot(document.getElementById('root')!).render(
  <AppProviders>
    <App />
  </AppProviders>,
);

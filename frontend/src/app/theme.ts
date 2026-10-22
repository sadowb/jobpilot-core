import { useEffect, useState } from 'react';
type Theme = 'light' | 'dark';
function savedTheme(): Theme | null {
  try {
    const value = localStorage.getItem('jobpilot-theme');
    return value === 'light' || value === 'dark' ? value : null;
  } catch {
    return null;
  }
}
function systemTheme(): Theme {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}
export function initializeTheme() {
  try {
    document.documentElement.dataset.theme = savedTheme() ?? systemTheme();
  } catch {
    /* CSS supplies the system fallback if initialization is unavailable. */
  }
}
export function useTheme() {
  const [manual, setManual] = useState<Theme | null>(savedTheme);
  const [system, setSystem] = useState<Theme>(systemTheme);
  const theme = manual ?? system;
  useEffect(() => {
    try {
      const media = window.matchMedia('(prefers-color-scheme: dark)');
      const change = () => setSystem(media.matches ? 'dark' : 'light');
      media.addEventListener('change', change);
      return () => media.removeEventListener('change', change);
    } catch {
      /* The initialized theme still works without media queries. */
    }
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  function toggle() {
    const next = theme === 'dark' ? 'light' : 'dark';
    setManual(next);
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem('jobpilot-theme', next);
    } catch {
      /* Theme still works when storage is unavailable. */
    }
  }
  return { theme, toggle };
}

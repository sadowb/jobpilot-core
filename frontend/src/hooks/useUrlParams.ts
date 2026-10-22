import { useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';

type Patch = Record<string, string | null | undefined>;

/**
 * List filters live in the query string so a reload, a shared link or Back restores the exact view.
 * Empty values (and the per-key default) are removed, keeping URLs short. Updates use `replace` so typing in a search
 * box does not flood the history stack.
 */
export function useUrlParams() {
  const [params, setParams] = useSearchParams();
  const get = useCallback((key: string, fallback = '') => params.get(key) ?? fallback, [params]);
  const set = useCallback(
    (patch: Patch) =>
      setParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          for (const [key, value] of Object.entries(patch)) {
            if (value === null || value === undefined || value === '') next.delete(key);
            else next.set(key, value);
          }
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );
  return { get, set, params };
}

/** One URL parameter restricted to a known set of values; anything else (typos, stale links) reads as the fallback. */
export function pick<T extends string>(value: string, allowed: readonly T[], fallback: T): T {
  return (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

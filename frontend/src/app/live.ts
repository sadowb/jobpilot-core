import { useEffect, useState } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';

// Reference data that nothing in the dashboard can change: server-side registries, and queries keyed by a content hash
// (a new CV version is a new key). Neither live events nor owner actions should refetch these.
export const REFERENCE_KEYS = new Set(['session', 'countries', 'answer-bank', 'track-source']);
// Owner-edited data is refreshed on global hints/reconnects, rather than every job hint or safety poll.
const STATIC_KEYS = new Set([
  ...REFERENCE_KEYS,
  'track-history',
  'profile',
  'facts',
  'answers',
  'tracks',
  'history',
  'boards',
  'settings',
]);

// An infinite query refetches every loaded page in sequence. Once the owner has paged back into older updates, a busy
// agent pool would re-download all of them every refresh window, so the feed is only marked stale (it refetches on the
// next mount or window focus) until it is back to one page.
const multiPage = (data: unknown) =>
  ((data as { pages?: unknown[] } | undefined)?.pages?.length ?? 0) > 1;

/** Refetch visible views and mark the rest stale; global hints also refresh mutable reference data. */
export function refreshLive(client: QueryClient, job?: string, all = false) {
  return Promise.all([
    client.invalidateQueries({
      predicate: (query) =>
        !(all ? REFERENCE_KEYS : STATIC_KEYS).has(String(query.queryKey[0])) &&
        (!job ||
          !['job', 'approval-review'].includes(String(query.queryKey[0])) ||
          query.queryKey[1] === job) &&
        !(
          query.queryKey[0] === 'tracker' && client.isMutating({ mutationKey: ['tracking'] }) > 0
        ) &&
        !(query.queryKey[0] === 'updates' && multiPage(query.state.data)),
    }),
    client.invalidateQueries({
      queryKey: ['updates'],
      predicate: (query) => multiPage(query.state.data),
      refetchType: 'none',
    }),
  ]);
}

/** One native EventSource at the dashboard root; reconnection belongs to the browser. */
export function useLiveUpdates(): boolean {
  const client = useQueryClient();
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    const events = new EventSource('/api/v1/events');
    events.onopen = () => {
      setConnected(true);
      // NOTIFY has no replay store. Refetch on every open, including reconnects.
      void refreshLive(client, undefined, true);
    };
    events.onerror = () => setConnected(false);
    events.addEventListener('job', (event: MessageEvent<string>) => {
      try {
        const hint: unknown = JSON.parse(event.data);
        if (
          typeof hint !== 'object' ||
          hint === null ||
          !('job' in hint) ||
          typeof hint.job !== 'string' ||
          !/^j_[a-z2-7]{8}$/.test(hint.job) ||
          !('kind' in hint) ||
          typeof hint.kind !== 'string'
        )
          return;
        void refreshLive(client, hint.job);
      } catch {
        // Malformed hints never become query keys or dashboard data.
      }
    });
    events.addEventListener('automation', () => void refreshLive(client, undefined, true));
    const onVisible = () => {
      if (!document.hidden) void refreshLive(client);
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      events.close();
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [client]);

  useEffect(() => {
    // Worker heartbeats and missed notifications are still covered; errors restore fast polling.
    const timer = window.setInterval(
      () => {
        if (!document.hidden) void refreshLive(client);
      },
      connected ? 30_000 : 5_000,
    );
    return () => window.clearInterval(timer);
  }, [client, connected]);
  return connected;
}

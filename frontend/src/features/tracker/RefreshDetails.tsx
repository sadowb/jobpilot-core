import { api } from '../../api/api';
import { useAction } from '../../hooks/useAction';
import { ErrorMessage, LinkButton } from '../../components/ui';

const refreshReasons: Record<string, string> = {
  nothing_found: 'The posting did not show anything new.',
  nothing_missing: 'Nothing was missing.',
  browser_offline: 'The browser (Neo) is not reachable.',
  browser_busy: 'The browser has no free tab right now.',
  page_unreadable: 'The page could not be read.',
  timeout: 'It took too long.',
  gone: 'This job no longer exists.',
};

/** Re-reads the posting's details for any job, submitted ones included. Never changes the application's state. */
export function RefreshDetails({
  id,
  refresh,
}: {
  id: string;
  refresh?: Record<string, unknown> | null;
}) {
  const action = useAction(
    () => api.refreshDetails(id),
    (result) =>
      result.already ? 'A refresh is already running.' : 'Refreshing details from the posting.',
  );
  const state = typeof refresh?.state === 'string' ? refresh.state : null;
  const busy = state === 'queued' || state === 'running';
  const reason = typeof refresh?.reason === 'string' ? refresh.reason : null;
  return (
    <>
      <LinkButton
        disabled={busy}
        loading={action.isPending}
        onClick={() => action.mutate()}
        className="font-medium"
      >
        {busy ? 'Refreshing details…' : 'Refresh details'}
      </LinkButton>
      {state === 'failed' && (
        <span className="text-xs text-muted">
          Last refresh failed: {(reason && refreshReasons[reason]) || 'try again later.'}
        </span>
      )}
      <ErrorMessage error={action.error} />
    </>
  );
}

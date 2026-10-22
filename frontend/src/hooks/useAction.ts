import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useToast } from '../components/ui';
import { friendlyError } from '../utils/humanize';
import { REFERENCE_KEYS } from '../app/live';
export function useAction<T, V = void>(
  action: (variables: V) => Promise<T>,
  message: string | ((result: T) => string) = 'Changes saved.',
) {
  const client = useQueryClient();
  const notify = useToast();
  return useMutation({
    mutationFn: action,
    onSuccess: (result) => {
      const failed =
        Array.isArray(result) &&
        result.some(
          (item) =>
            item &&
            typeof item === 'object' &&
            (('ok' in item && !item.ok) || ('error' in item && Boolean(item.error))),
        );
      notify(
        failed
          ? 'Some items need attention. Review the results below.'
          : typeof message === 'function'
            ? message(result)
            : message,
        failed ? 'error' : 'success',
      );
      // The owner just changed something: refetch everything, settings-like data included, except immutable reference data
      // (and the session, whose refetch could only ever log the dashboard out mid-action).
      // Keep mutation controls pending until the refreshed server state is visible.
      return client.invalidateQueries({
        predicate: (query) =>
          !REFERENCE_KEYS.has(String(query.queryKey[0])) &&
          !(
            query.queryKey[0] === 'tracker' && client.isMutating({ mutationKey: ['tracking'] }) > 0
          ),
      });
    },
    onError: (error) => notify(friendlyError(error), 'error'),
  });
}

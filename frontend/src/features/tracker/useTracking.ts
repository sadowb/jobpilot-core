import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/api';
import type { TrackerRow, TrackingUpdate } from '../../api/models';
import { useToast } from '../../components/ui';
import { friendlyError } from '../../utils/humanize';
import { beginTrackingEdit, finishTrackingEdit } from './optimisticTracking';
import { refreshLive } from '../../app/live';

// Wire (snake_case) field -> row (camelCase) field, for the optimistic patch.
const rowField: Partial<Record<keyof TrackingUpdate, keyof TrackerRow>> = {
  status: 'status',
  follow_up_on: 'followUpOn',
  date_contacted: 'dateContacted',
  interview_on: 'interviewOn',
  applied_on: 'appliedOn',
  resume_label: 'resumeLabel',
  submitted_via: 'submittedVia',
  job_level: 'jobLevel',
  location: 'location',
  work_mode: 'workMode',
  employment_type: 'employmentType',
  pay: 'salary',
  role: 'role',
  notes: 'notes',
};

/** Optimistic row edit: the table changes instantly, rolls back with a message if the server refuses. */
export function useTracking(rowId: string, success = 'Saved.') {
  const client = useQueryClient();
  const notify = useToast();
  return useMutation({
    mutationKey: ['tracking'],
    scope: { id: `tracking:${rowId}` },
    mutationFn: (patch: TrackingUpdate) => api.updateTracking(rowId, patch),
    onMutate: async (patch) => {
      await client.cancelQueries({ queryKey: ['tracker'] });
      const change: Partial<TrackerRow> = {};
      for (const [key, value] of Object.entries(patch)) {
        const field = rowField[key as keyof TrackingUpdate];
        if (field) (change as Record<string, unknown>)[field] = value ?? null;
      }
      return { token: beginTrackingEdit(client, rowId, change) };
    },
    onError: (error, _patch, context) => {
      if (context) finishTrackingEdit(client, rowId, context.token, false);
      notify(friendlyError(error), 'error');
    },
    onSuccess: (_result, _patch, context) => {
      if (context) finishTrackingEdit(client, rowId, context.token, true);
      notify(success);
    },
    // Wait for the last edit across all rows, so a refetch cannot wipe another pending layer.
    onSettled: () => {
      if (client.isMutating({ mutationKey: ['tracking'] }) === 1)
        return Promise.all([
          refreshLive(client),
          client.invalidateQueries({ queryKey: ['tracker'] }),
        ]);
    },
  });
}

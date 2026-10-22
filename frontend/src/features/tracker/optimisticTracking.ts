import type { QueryClient, QueryKey } from '@tanstack/react-query';
import type { TrackerRow } from '../../api/models';

type Edit = { token: symbol; patch: Partial<TrackerRow> };
type Snapshot = { key: QueryKey; row: TrackerRow };
type PendingRow = { snapshots: Snapshot[]; edits: Edit[] };
const pending = new WeakMap<QueryClient, Map<string, PendingRow>>();

/** Layer pending edits over each row's last confirmed values, rather than replacing the whole table on failure. */
export function beginTrackingEdit(
  client: QueryClient,
  rowId: string,
  patch: Partial<TrackerRow>,
): symbol {
  let rows = pending.get(client);
  if (!rows) {
    rows = new Map();
    pending.set(client, rows);
  }
  let row = rows.get(rowId);
  if (!row) {
    const snapshots = client
      .getQueriesData<TrackerRow[]>({ queryKey: ['tracker'] })
      .flatMap(([key, data]) => {
        const item = data?.find((item) => item.id === rowId);
        return item ? [{ key, row: item }] : [];
      });
    row = { snapshots, edits: [] };
    rows.set(rowId, row);
  }
  const token = Symbol('tracking edit');
  row.edits.push({ token, patch });
  renderRow(client, rowId, row);
  return token;
}

/** A success advances the baseline; a failure removes only its own pending layer. */
export function finishTrackingEdit(
  client: QueryClient,
  rowId: string,
  token: symbol,
  succeeded: boolean,
): void {
  const rows = pending.get(client);
  const row = rows?.get(rowId);
  const edit = row?.edits.find((item) => item.token === token);
  if (!row || !edit) return;
  if (succeeded)
    row.snapshots = row.snapshots.map((snapshot) => ({
      ...snapshot,
      row: { ...snapshot.row, ...edit.patch },
    }));
  row.edits = row.edits.filter((item) => item.token !== token);
  renderRow(client, rowId, row);
  if (!row.edits.length) rows?.delete(rowId);
}

function renderRow(client: QueryClient, rowId: string, pendingRow: PendingRow): void {
  for (const snapshot of pendingRow.snapshots) {
    const row = pendingRow.edits.reduce(
      (current, edit) => ({ ...current, ...edit.patch }),
      snapshot.row,
    );
    client.setQueryData<TrackerRow[]>(snapshot.key, (data) =>
      data?.map((item) => (item.id === rowId ? row : item)),
    );
  }
}

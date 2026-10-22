/** Report provenance is stored with each inventory field, surviving event pagination. */
export function isReportedDraft(job: { inventory: Array<Record<string, unknown>> }): boolean {
  return job.inventory.some((field) => field.reported === true);
}
export const reportedDraftLabel = 'filled by the agent (reported, not code-verified)';

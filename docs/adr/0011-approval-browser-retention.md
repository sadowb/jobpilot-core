# 0011 — Preserve browser sessions during approval

Status: accepted

## Context

An application waiting for human approval has a verified draft in PostgreSQL and a filled form in an isolated browser. The database does not contain every custom widget, authentication or navigation state. Closing the browser can require refilling and can make some forms impossible to restore. The user wants to preserve that work during approval.

## Decision

- Retain browser sessions for jobs in `awaiting_approval`. Stop the agent run and release the execution lease at the approval boundary.
- Automatic cleanup must preserve those sessions when their task becomes blocked or its lease expires. Recheck this under the existing browser lock before closing.
- Explicit close requests and archived jobs remain eligible for cleanup. Other blocked, completed and expired sessions follow the existing cleanup and retry lifecycle.
- Submission still requires the server-written approval and verification of the live draft. If the session has disappeared, use the existing guarded recovery path; restoration failure or draft drift requires human review.

## Draft invalidation (2026-10-12)

Every draft invalidation locks the job, clears fill/verification/hash, deletes its
unconsumed submit approval rows and emits `approval_revoked` when rows were deleted.
A ready or approval-waiting job returns to `preparing` through the state machine;
fresh verification is required before `ready` and a fresh owner approval is required
before submission, even if verification later reproduces the old hash. Consumed
approvals and the submit-click fence are preserved. Only `Services.approve_submit`
writes submit approvals.

This applies to CV replacement, changed plans/answers, inventory replacement,
agent edits, replay/navigation and verification drift or failure. Plan comparison
and invalidation happen under the job lock in one transaction. Identical base CV
selection, planning and normalized persisted answers are no-ops: no events, plan
reset or resume/enqueue. A real owner base-CV replacement also resumes preparation.
A live form opened again is reused with its cursor intact; replay is explicit and
invalidates before navigation. Recovery after losing a tab now requires fresh
verification and owner approval rather than retaining an approval across refill.
If the restored draft verifies unchanged, return it to `awaiting_approval` and tell
the owner: "The browser tab was lost and the form was filled again; please approve
submission again."

## Consequences

This restores the intended preservation of filled forms after an overly aggressive cleanup change. Approval browsers still consume memory even though they do not occupy an agent execution slot. Worker concurrency alone therefore does not bound total browser memory. A future retention cap or expiry policy must explicitly account for loss of page state; this change does not introduce automatic eviction of approval forms.

Browser lifecycle regressions cover approval preservation, reuse without calling fill for an already-open form, cleanup retry, active-model protection, and guarded recovery of a missing session.

> Amended by [0014](0014-shared-chrome-tabs.md): a parked form is a parked tab of the node's shared Chrome; the daemon idle timeout no longer ends it and no retention setting exists.
> Extended by [0015](0015-direct-browser-harness.md#parked-question-tabs-and-resume-runs): a direct-mode form waiting on owner answers is parked the same way and resumed, not refilled.

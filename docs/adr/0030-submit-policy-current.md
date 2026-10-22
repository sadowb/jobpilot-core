# 0030. Submit policy as it stands (consolidates 0026 and its amendments)

Status: accepted (documentation consolidation, 2026-10-19). Changes no behavior.

## Why this ADR exists
ADR 0026 is titled "the agent never clicks final Submit" but was amended three times in one day: a review/auto mode
toggle, a parked-tab watcher, and an emailed-code run. Read alone, the title and `docs/architecture.md` contradicted the
code (`worker/runner.py:_run_direct`, `service/submit_mode.py`, `service/approvals.py`). This file is the single current
statement. `AGENTS.md` hard rule 3 is the short form; 0026 keeps the history and rationale.

## Policy
1. **Two modes, chosen by the server, never by the model.** `runtime_settings.submitMode` (`review` default | `auto`) with a
   per-job override `jobs.submit_mode`. `SubmitModeService.decide` snapshots the mode once at run start, records a
   `submit_mode` event and builds the instruction from it. A running run keeps its snapshot.
2. **Review.** The agent fills, uploads the CV, does email-verification steps that come before the final step, stops on the
   final review page with the button untouched and returns `filled`. The job parks at `awaiting_approval` with its Neo tab
   kept. The owner clicks Submit in Neo. The server learns of it by the owner's "I submitted it" (user session only,
   idempotent), by the park watcher (`method: auto-detected`), or by "Mark as submitted elsewhere".
   An agent `submitted` in a review run is `unapproved-reported`: recorded, automation stopped (`unapproved_submit`), Needs You.
3. **Auto.** The server writes the durable `submit_clicked` fence (`auto: true`) **before** the agent starts; the agent clicks
   the final Submit at most once. `submitted` -> system approval (`auto-mode`) bound to the reported draft hash, evidence read
   from the tab. `filled`/`needs_answers` -> only the auto fence is released (`submit_fence_released`). Anything else
   (blocked, invalid, timeout) -> the fence stays, the tab is observed once, otherwise "submission uncertain" in Needs You.
   Never retried automatically.
4. **Forced review.** Auto is downgraded to review for a job when: the board policy is not `auto`; the job override is
   `review`; required questions were answered via "try anyway"; the CV was chosen without reading the posting
   (`cv_select_fallback`); an email-verification step failed (`email_verification_failed`).
5. **Board policy.** A board with policy `manual` is never automated: the runner parks the job in Needs You
   ("apply manually") before any browser tab opens. Workday is `manual` (see 0006/0007 and `service/jobs.py:board_policy`).
6. **Emailed security code (0026 part C).** The only agent run allowed on an `awaiting_approval` job is kind
   `verify_email_code`, and only after the owner's click is on record.
7. **Backstops that do not depend on the model:** the state machine gate for `submitted` (approval hash == current draft
   hash, plus confirmation evidence), the no-repeat fence, strict result validation, and the `unapproved_submit` kill switch.
   There is still no browser-side submit guard (0026 "Not done").

## Consequences
- The most safety-relevant behavior has one authoritative description; `architecture.md` step 6 and "Trust boundaries"
  point here instead of restating it.
- 0026's title is marked amended. Future changes to submit behavior supersede this ADR, not 0026.

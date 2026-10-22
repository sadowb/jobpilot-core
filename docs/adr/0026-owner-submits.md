# 0026. The owner submits; the agent never clicks final Submit (in review mode)

Status: accepted (owner decision, 2026-10-18); **amended the same day** (auto mode, parked-tab watcher, emailed code).
Current consolidated policy: [ADR 0030](0030-submit-policy-current.md). Decision 1 below holds for review runs only.

(Original status line:) Supersedes the submit-run, approval-gate and "detection only" parts of
ADR 0020 and AGENTS.md hard rule 3.

## Context
The Haiku agent clicked the final Submit in a preparation run twice live (job `j_tmvqizck` and an earlier fixture)
despite the stop rule. The server could only detect it afterwards. Letting the model also perform the approved click
(a "submit run") kept the same failure mode alive: one wrong click is one real application.

Owner: "reconstruct the prompt so it doesn't submit until I verify and leave the submission to me, not to it".

## Decision
1. **The agent never clicks final Submit, in any run.** `agent/NEO.md` and every per-run instruction have one job:
   fill, upload the CV, do email-verification steps that come before the final step, stop on the final review page with
   the button untouched, return `filled`. The submit-run procedure, the `approved_fields` / `approved_draft_hash` DATA
   and the confirmation-reporting duties are gone.
2. **No approval dispatches a run.** `POST /approvals/submit` and the agent submit run are removed. A job at
   `awaiting_approval` is waiting on the owner; the runner never starts an agent on it (a leftover job still marked
   `waiting_on=agent` is handed to the owner).
3. **Owner flow.** Needs You and Job detail show the filled application: review answers, "Find agent tab" (focus the
   parked Neo tab, ADR on tab focus), then after clicking Submit in Neo: **"I submitted it"**
   (`POST /jobs/{id}/owner-submitted`) or **"Mark as submitted elsewhere"** (`/jobs/{id}/user-confirmed`).
   `owner-submitted` is user-session only (Bearer tokens get 403), bound to the job, idempotent (a second call returns
   `already: true` and changes nothing) and only valid at `awaiting_approval` with a verified draft.
4. **What it records.** An approval row for the *current draft hash* (what the owner reviewed; consumed in the same
   transaction), a `submit_clicked` event with actor `user` (the durable no-repeat fence), the transition to
   `submitted` through the state machine, and an `applications` row.
5. **Read-only verification.** Before the transition closes the tab, the server reads the parked tab up to three times
   (2 s apart) with `page_text`, matches the board's confirmation patterns, and stores one capture (URL without
   query/fragment, title, excerpt, screenshot, verdict `seen`/`not_seen`) in `job_forms.submit_evidence`. No agent, no
   click, no navigation. `seen` records `live-verified`; otherwise `user-confirmed` with the text "no confirmation page
   was seen", shown to the owner. An unreadable tab or Neo outage is `not_seen`; the owner's word still stands.
6. **Safety net kept.** Any agent result (or page observation after a run) that still reports `submitted` is recorded
   as `unapproved-reported`, writes `unapproved_submit_reported` and the `submit_clicked` fence, stops automation
   (`unapproved_submit`) and shows the Needs You check card. The parked-tab rules (kept while the owner decides,
   counted in `maxOpenBrowsers`, closed after submission) are unchanged.
7. **Draft hash kept**, because it now records which draft the owner saw. DB columns are unchanged.
8. **Dispatch.** The claim query never admits a job at `awaiting_approval`; the SQL that prioritised unconsumed submit
   approvals, `Dispatch.submit`, the cap exemption for submit runs, `ActiveWorker.phase` and the approval-progress API
   (`JobDetail.submit`) are removed. Migration `0027_owner_submits.sql` hands a job approved under the old flow
   (`awaiting_approval`, waiting on the agent) to the owner and closes its queued task. `check_dispatch.py` scenarios
   s6, s7 and s17 now use resumed jobs with a kept tab; s31 and s32 are new.

## Not done: a browser-side guard
A guard that blocks submit clicks inside the browser would need an init script that survives navigations. Neo's `run`
SDK exposes raw CDP (`page.cdp`, `browser.cdp`) but each `run` is bounded to 30 s and nothing documents a registration
(for example `Page.addScriptToEvaluateOnNewDocument`) that outlives the call, and it could not be tested here. A guard
that works only until the first navigation would be a fragile half-guard, so none was added; the protection is the
instruction plus the detection and kill switch in 6.

## Consequences and limits
- Confirmation detection is text-pattern based on the parked tab's final text and cannot tell a confirmation from a
  posting that itself says "thank you for applying" (the old run compared against a pre-run baseline; there is no
  baseline now). A false `seen` only upgrades the label to `live-verified`; the owner sees the excerpt.
- "I submitted it" holds the request for up to ~4 s while the tab is read.
- The Haiku prompt can still be ignored; item 6 is the backstop, not prevention.

## Amendment: submit mode toggle (2026-10-18, owner request)
Owner: "an option where I click and toggle between either let it go full wild or I need to see what it wrote and me
click submit". Decision 1 ("never in any run") becomes "never in a **review** run"; review is still the default.

1. **Setting.** `runtime_settings.submitMode` = `review` | `auto` (live: applies to runs that start after the change;
   a running run keeps its snapshot). Per-job override `jobs.submit_mode` (`review` | `auto` | null = default), set on
   Job detail until submission begins. Changes are `mode_changed` events (actor user).
2. **Snapshot.** The runner calls `SubmitModeService.decide(job)` once before the browser run, writes a
   `submit_mode` event (`mode`, `requested`, `override`, `reason`) and builds the instruction (`DirectService.prepare`
   with `mode`) from it. The model never chooses the mode.
3. **Forced review.** `auto` is downgraded to `review` for that job when: the board policy is not `auto`; the job
   has a per-job `review` override; required questions were answered by "try anyway"; the CV was chosen by the
   fallback without reading the posting (`cv_select_fallback`); an email-verification step failed
   (`email_verification_failed`). The reason is returned in `JobDetail.submitMode.reason` and shown to the owner.
   Limit: `email_verification_failed` is inferred from the wording of an agent `blocked` note (regex), because no
   structured signal exists today.
4. **Auto run.** Before the agent starts, the durable fence (`submit_clicked`, payload `auto: true`) is written, so a
   crash, timeout or invalid output after a possible click can never lead to a second one. After the run:
   - `submitted`: the draft hash is computed from the reported fields, a system approval (scope `auto-mode`, actor
     system) is bound to it and consumed in the same transaction, the application is recorded
     (`agent-reported`), read-only evidence is captured from the tab first (same service as the owner flow).
   - `filled` / `needs_answers`: the agent says it stopped before clicking, so only the *auto* fence rows are deleted
     (`submit_fence_released` event) and the review flow continues. This is the one place a `job_events` row is
     deleted; the fence is a lock, and the release is itself recorded.
   - `blocked`, invalid output, timeout, interruption: the fence stays; the tab is observed once for the board's
     confirmation text (recorded as an auto submit if seen), otherwise Needs You shows "submission uncertain". Never
     retried automatically. The owner resolves it with "Mark as submitted elsewhere" or by checking the tab.
5. **Kill-switch semantics.** An agent `submitted` is the *expected* outcome of an auto run and the *unapproved
   kill switch* (automation stopped, Needs You card) in a review run. The decision depends only on the server's
   snapshot for that run.
6. **Instructions.** `agent/NEO.md` has the stop-before-Submit variant and the fill-and-submit-once variant, selected by
   DATA `submit_mode` and the per-run instruction (the instruction wins); the review instruction keeps the stop rule at
   both ends, the auto instruction states the one-click rule at both ends.

### Amendment part B: noticing the owner's manual submit
"I submitted it" stays, but is optional in review mode.
1. **Baseline.** When a run parks a job at `awaiting_approval`, the runner stores `job_forms.park_baseline`
   (migration 0029): query-free URL, title, sha-256 of the normalised visible text, which reported field labels are
   on the page, and whether the board's confirmation pattern already matched. Best effort, outside the apply transaction.
2. **Watcher.** Celery task `jobpilot.park_watch` (control queue, beat every 10 s, 45 s soft limit) reads each
   parked tab on this node with `NeoBrowser.page_state` only: no model, no clicks, a pool of 4 reads with a 20 s
   deadline, at most 40 jobs per pass. It compares with the baseline (`service/park_watch.py: judge`):
   - confirmation text that the baseline did not contain AND (URL path changed OR the field labels are gone) →
     re-read 2 s later; if the verdict holds, `auto_detected_submit` records it exactly like the button
     (actor `user`, evidence captured first, confirmation `live-verified`, `method: auto-detected` in the events,
     tab closed under the normal rules). Idempotent against the button: whichever commits second sees the
     application and does nothing.
   - page moved / form gone / confirmation text with the form still present, but not both → `park_signal =
     looks_submitted` (one `park_signal` event); Needs You and Job detail ask "This looks submitted. Can you confirm?"
     with **Yes, I submitted it** / **Not yet** (`POST /jobs/{id}/park-signal/dismiss` clears it and re-baselines).
   - tab no longer listed by Neo → `park_signal = tab_closed`: "Did you submit?" with **Yes, I submitted it** /
     **No, reopen it** (`POST /jobs/{id}/reopen`: back to `preparing`, fresh run; refused after a submit began).
   - A baseline that already says "thank you for applying" can never produce the first outcome.
3. **Limits.** The watcher is text-pattern based like the verification. It runs on whichever node's control worker
   receives the beat task and only watches tabs recorded for that node (single-Neo deployments are unaffected; a
   multi-node deployment needs a per-node schedule). A Neo outage is an unreadable tick, never a signal. Auto-mode jobs
   have no parked tab to watch; their uncertain outcomes are handled in 4 above.


### Amendment part C: the emailed security code after the owner's click
Some sites (Greenhouse) answer the owner's Submit click with "enter the security code we emailed you". In review mode no
agent runs at that point, so the code sat in Gmail. A dedicated short run, **kind `verify_email_code`**, may now finish
that step. It is the only agent run allowed on a job at `awaiting_approval`, under these server-enforced rules
(`service/verification.py`, migration 0031):
1. **Only after the owner's click is on record.** The trigger is either the watcher seeing a code prompt that the parked
   baseline did not have (`domain/challenge.py`; such a prompt exists only after a submit click; confirmed by a second read
   2 s later) or the owner pressing **Get the email code from Gmail** (`POST /jobs/{id}/email-code`, user-session only).
   Either way the `submit_clicked` fence is written (`owner: true`, `method: challenge-observed | owner-requested`) and a
   `verification_challenge` event is recorded; all no-repeat rules apply from then on (no reopen, no second submit).
2. **One attempt per challenge.** `job_forms.verify_requested_at` is a one-shot; the watcher can never ask twice, the
   owner's button may retry only after the earlier run finished (`verify_done_at`). The claim lets a job through
   `awaiting_approval` only while a request is open.
3. **Same tab, narrow job.** The instruction (and `agent/NEO.md`) allows: open Gmail in a NEW tab, read the newest
   employer/ATS mail, type the code into the application tab, click ONLY the button that confirms the code, close the Gmail
   tab. The code is never returned, logged or stored; only `email_verification_used {source: email}` is recorded.
4. **The report is not trusted.** The submit is recorded (`method: email-code`, actor `user` on the owner's behalf) only
   when a read-only look at the tab sees the board's confirmation page. Anything else (no mail, rejected code, no
   confirmation) ends in a Needs You card "Paste the code yourself". The watcher keeps running meanwhile and may record
   the confirmation first; whichever commits second does nothing.

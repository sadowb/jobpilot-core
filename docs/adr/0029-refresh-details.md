# 0029. Refresh details for any job

Status: accepted (owner request, 2026-10-18).

## Context
Some jobs keep a placeholder role ("(role not captured yet)") or an ATS host as company (`ats.rippling.com`), and many
have no location, level or work mode. The owner wants to re-capture them at any time, including after the application was
submitted. Starting a normal agent run is wrong for that: runs are admitted through `jobs.waiting_on='agent'`, count
against browser slots, can change status/forms and, for a submitted job, must never start.

## Decision
1. **`POST /jobs/{id}/refresh-details`** (user session only, any status except deleted) and
   **`POST /jobs/refresh-missing-details`** (bulk, at most 25 live jobs whose role or company is a placeholder, not retried
   for 6 h after a finished attempt). One row in `job_detail_requests` per job is the request and the lock:
   queued -> running -> done | failed. A second click while queued/running is a no-op.
2. **Not a job run.** No `tasks` row, no lease, no change to status, `waiting_on`, forms, approvals or the submit fence. The
   control queue runs `details_run`; the existing dispatch beat tick only claims queued rows (at most
   `min(2, workerConcurrency)` at once) and expires runs lost for 6 minutes.
3. **Cheapest source first, each stage only while a gap is open:** the ATS public API (no model) -> the stored posting text
   through one tool-less Haiku extraction -> the public page / JSON-LD through the same extraction -> a browser read.
   Each stage that touches the employer is paced per domain (`pace_browser`).
4. **The browser stage is server-driven, not an agent run** (deviation from "a `details` agent run kind"): the server opens
   one new Neo tab, navigates, waits for stable text (the code CV selection already uses), reads it, closes that same tab in
   `finally`, and hands the text to the tool-less extraction. No model has a browser tool, so "no clicks, no form, no account
   steps, no submit" is structural, not an instruction. Cost: it cannot dismiss a cookie banner or expand "show more".
5. **Merge, field by field** (`domain/details.py`): only placeholders and empty values are filled. Placeholders: empty role or
   `(role not captured…)`; empty company, an ATS vendor name, a known ATS host, or the host of the job's own link (a real
   employer named like `Booking.com` is kept). A real value is never replaced. A field the owner edited is never touched:
   the marker is the existing `tracking_updated` event (actor `user`), re-read under the job row lock at write time.
   Posting text and `posting_sha256` are not written (`meta_captured` and the CV selection stay as they were).
6. **Records.** `details_refreshed` with the changed field names and the stages that contributed (never values);
   `details_refresh_failed` with a reason code. The vault note follows through `project()` only.

## Limits
- Owner edits made before `tracking_updated` events existed are not known and could be overwritten if still blank.
- A changed role/company/location can create a probable duplicate; unlike `set_meta` this does not flag it (flagging would
  change `waiting_on`).
- Haiku can still return a wrong string for a blank field; the owner can edit it, and it is then protected.
- JS-only pages that need a click to show the posting are not readable.

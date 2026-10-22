# 0032. Board policy is the owner's: Workday no longer forced manual; board recipes and entry hooks

Status: accepted (owner decision, 2026-10-19). Supersedes the "Workday forced manual" sentence of ADR 0007.

## Context
Workday was hardcoded to manual in three places (`JobsService.board_policy`, `JobsService._order_state`,
`ProfileService.board_update`), plus a migration and the seed row, with the reason "its wizard cannot be safely rebuilt".
The blocker was the account step. ADR 0031 removed it: the server creates/stores/types the login, and the agent only fills
ordinary fields.

## Decision
1. `BoardAdapter.max_automation` is the highest policy the owner may set for a board. Workday: `assisted`; every other
   board: `auto`. `boards.cap_policy` clamps the effective policy, `boards.exceeds_ceiling` guards `board_update`.
   **Owner decision (2026-10-19): Workday's ceiling is `auto`**, so the owner may choose `manual`, `assisted` or `auto`
   like for any other board. The mechanism stays for a board that needs a lower ceiling. Auto submission keeps every
   existing safeguard (ADR 0026/0030): the global/per-job submit mode must also be `auto`, unanswered required questions,
   a CV chosen without reading the posting or a failed email verification force review, the server writes the
   `submit_clicked` fence before the click, and the confirmation must match the board's patterns (the common set:
   "application (has been) submitted/received", "thank you for applying"; Workday's own text is not yet verified live).
2. **Off by default.** The stored policy stays `manual` (migration 0009 seed, 0006 reset). The owner opts in with
   `POST /boards/workday {automation: "assisted" | "auto"}` (automation stopped, leases drained, as for any board change).
3. **Entry.** `boards.entry_url` starts every Workday run at `<posting>/apply/applyManually`; the agent never uses
   "Autofill with Resume" (it re-parses the CV and overwrites fields) or "Use My Last Application".
4. **Prefetch.** `posting_fetch._workday` reads the public CXS document
   (`/wday/cxs/{tenant}/{site}/job/{path}`, host must be `{tenant}.wdN.myworkdayjobs.com`) and fills role, company,
   location, work mode, type, description with no browser and no model. Any failure falls back to the browser path. The
   document publishes no question list, so the wizard itself is the agent's job.
5. **Wizard guidance** is the WORKDAY paragraph in `agent/NEO.md`: six steps, `Save and Continue` is never the final
   submit, EEO/voluntary-disclosure answers only from saved answers, stop on the Review step, return `filled`.

## Board-generic mechanisms (Workday was the first user, not the only one)
- **Policy.** Every board has an owner-chosen policy (`manual|assisted|auto`) with an optional `max_automation` ceiling
  (default `auto`, so none is capped). Seeds: Greenhouse, Lever, Ashby, Workable, BambooHR, SmartRecruiters, Eightfold
  `auto`; iCIMS, Taleo, SuccessFactors, Handshake and company sites (`custom`) `assisted` (migration 0034 moved the first three: candidate accounts and multi-page wizards, not verified end to end); LinkedIn, Indeed and Workday
  `manual`. `auto` still only takes effect when the submit mode is `auto` (ADR 0030).
- **Entry hook.** `BoardAdapter.entry` rewrites the start URL per board (`boards.entry_url`); only Workday uses it today.
- **Board recipes.** `agent/boards/_common.md` plus `agent/boards/<id>.md` (one short file per board, including `custom`)
  are appended to the run instruction as BOARD NOTES by `boards.recipe_text` (id validated against the adapters, 2000
  characters max). They replace the dead `jp`-era files; NEO.md says the rules there win on conflict.
- **Accounts on any site.** ACCOUNT WALL / `AccountWallService` / `AccountFlow` are keyed by the page's own origin, not
  the board, so they serve company sites, iCIMS/Taleo/SuccessFactors candidate accounts and any ATS a company site redirects
  to. DATA `login_hosts` tells the agent which hosts the server can sign in to.
- **Prefetch.** Public-API prefetch exists for Greenhouse, Lever, Ashby, Workable, BambooHR, SmartRecruiters, CareerPlug,
  JazzHR and now Workday; the others use the browser path.

## Consequences and what is not verified
- Steps 2-5 of the wizard have not been driven end to end: creating a real Workday account is the owner's action, and
  the recon so far was read-only (CXS, apply entry, step list). Expect tenant variance (custom questions, widgets). The
  existing safety nets apply: `audit_filled` re-reads the page, required questions go to Needs You, a submit in a review
  run stops automation (`unapproved_submit`).
- Employer terms may forbid automated applications; enabling `assisted` is the owner's per-board call.
- Recommended order: run `assisted` on a first posting to verify the wizard and Workday's confirmation text, then choose `auto`. A Workday confirmation the patterns miss makes an auto run report `blocked`/uncertain (fence stays, Needs You), never a second submit.

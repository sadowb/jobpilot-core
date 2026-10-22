# 0011 — Read-only email verification (code / link) without exposing mail to the model

Status: accepted (2026-10-11)

## Context
Sites send email codes or magic links during sign-up/apply. Handing every such job to the owner stalls
fast parallel runs. Hard rule 4 forbids codes reaching prompts/logs/DB; email bodies are untrusted data.

## Decision
- `jp mail fill-code <job> <selector>` and `jp mail open-link <job>` are deterministic code paths. The model
  supplies only a selector; code reads the inbox, extracts the code/link, and types/opens it itself
  (same pattern as `fill-secret`). The model receives `{ok, from, ageSeconds}` only.
- Access is IMAP read-only (`BODY.PEEK`, `INBOX` select readonly) with a Gmail app password stored in the
  Keychain (`jobpilot-mail`); only the address is on disk (`data/mail-account`). Setup is owner-only and
  interactive (`bin/jp mail setup`, TTY required, not callable by the agent role).
- Filters: message < 15 min old; sender domain equals the job host's registrable domain or a known
  employer-mail relay (`domain/mailcode.py`); link must be `https`, hint-matched, and on the job's own site.
  Code field must be a visible single-line input. Events record sender domain + age, never the value.

A same-site verification-link navigation invalidates the draft before opening
the link, just as filling a code does. It revokes unconsumed submit approvals and
returns ready/approval-waiting jobs to preparation (ADR 0011). Neither the link
nor the code enters the revocation event.

## Consequences
- Prompt-injection through email cannot steer the agent: body text never leaves `mail.py`.
- Residual risk: a relay-domain sender (e.g. greenhouse-mail.io) could be spoofed by another tenant of that
  relay; the code is only ever typed into the job's own origin, so impact is a wrong code, not exfiltration.
- The app password grants whole-mailbox read. Mitigation: dedicated app password, revocable at
  myaccount.google.com/apppasswords; consider a Gmail filter forwarding only verification mail to a
  dedicated mailbox later.

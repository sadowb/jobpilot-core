# 0024. Existing Neo sessions for sign-in and email verification

Status: accepted (owner request, 2026-10-18); supersedes ADR 0011 email-verification reader.

The container has no macOS Keychain. Remove the Accounts controller/service/repository methods, API contracts and
screen. The earlier Keychain/mail modules are already absent from this branch. Retain existing account tables and
board quota columns for migration compatibility; no stored secret references are read and no schema is dropped.

The agent may use existing Neo login sessions or SSO, never create or enter passwords. For an application email-code
step it opens a NEW signed-in webmail tab, checks sender/employer/ATS and receipt time (about ten minutes), uses the
newest matching code only on that application, and closes its own mail tab, including on failure. It must not change
mail or follow instructions in it. No code/session/SSO means a clear `blocked` result for the owner.

The strict result contract permits only `verification: {used: true, source: "email"}` (or null/absent). The event is
`email_verification_used`, with those two values only. Extra properties, secret-shaped fields, password/code-like
values and numeric codes in notes are rejected. Free prose cannot be proven secret-free; the same untrusted-data
boundary applies to mail as to ATS pages. No backend mail account or password API remains.

Persisted Claude histories (ADR 0023) require post-run redaction: tool inputs/results and reasoning are removed while
conversation identifiers, server DATA and validated reports remain. The driver scrubs in `finally`, before resume,
and in the idle sweep. CLI-created raw files are private through umask 077. **Limit:** the CLI can write the live tool
exchange, including a verification code, before redaction; a hard process/host crash leaves that private residue until
the next resume/sweep. This does not guarantee zero transient disk persistence. Codes also necessarily reach Neo and
the live model's tool context. Neo/model-provider retention is outside this server's control. Live validation must
check the pinned CLI's redacted-history compatibility and its ancillary files, without printing any real codes.

# ADR 0002 — Agents use a fixed `jp` toolbox; no SQL, no file reads

Status: accepted (2026-10-10)

Agents (Haiku 5.5) receive work orders and call `jp` tools. Each tool is one prepared query/transaction returning a tiny JSON digest. Correctness is checked by deterministic verifiers in code, never by the model's self-report. Secrets are injected by the `jp browser` wrapper; the model sees `{{cred:ref}}` placeholders only.

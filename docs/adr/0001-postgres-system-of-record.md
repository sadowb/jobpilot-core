# ADR 0001 — PostgreSQL is the system of record; the vault is a generated view

Status: accepted (2026-10-10)

## Context
Thousands of jobs, many parallel workers, step-level logs. File-per-job logs and frontmatter-as-database do not scale and need two-way sync with Obsidian.

## Decision
Native PostgreSQL (dedicated cluster, port 5433, launchd, no Docker) holds all structured data. The Obsidian vault is a one-way generated projection (`Job.md` frontmatter) plus CV files. The body below `<!-- notes -->` belongs to the user and is never overwritten. Git versions CV files only.

## Consequences
+ Relational tables, indexed dedup, `SKIP LOCKED` queue, readable in any SQL client.
+ No bidirectional sync bugs.
- Frontmatter edited in Obsidian is overwritten; edit through the UI instead.

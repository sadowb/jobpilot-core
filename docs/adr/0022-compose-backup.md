# 0022. Compose-native backup

Status: accepted (2026-10-17)

## Context
`infra/pg-backup.sh` ran from launchd against Postgres.app. With the stack on Compose (ADR 0019) nothing backed up the
database.

## Decision
A `backup` service (`postgres:16-bookworm`, so `pg_dump` matches the server major) running `docker/backup.sh`:
- **Daemon:** checks hourly and writes a bundle when the newest is older than `BACKUP_INTERVAL_HOURS` (24), so it
  survives restarts and a sleeping laptop. Bundle = `jobpilot.dump` (`pg_dump -Fc`, validated with `pg_restore --list`
  before it is accepted) + `vault.tar.gz` (symlinks, `_cv-store`, git history, uncommitted notes), mode 0600 in
  `data/backups/` (0700). Newest `BACKUP_KEEP` (14) kept; only `jobpilot-*.tar.gz` is ever pruned.
- **Runs as `JP_UID:JP_GID`**, read-only vault mount, `cap_drop: ALL`, no network beyond the compose network.
- **Commands:** `pnpm backup` (now), `pnpm backup:list`, and `docker compose run --rm backup verify|restore-db|restore-vault`.
  `restore-db <bundle> [dbname]` creates the database when missing and uses `pg_restore --clean --if-exists`;
  `restore-vault <bundle> <dir>` refuses a non-empty directory. Restoring over the live database is deliberate and
  needs `docker compose stop server worker control beat` first.

## Verified
Backup, `verify`, `restore-db` into throwaway `jp_restore_check` (jobs 14, companies 26, tasks 14, migrations 22 equal
the live database), `restore-vault` into an empty directory (identical to `data/vault`, `.git` included), refusal of a
non-empty target.

## Limits
- Not encrypted (see SECURITY.md); no off-machine copy; the volume `pgdata` itself is not snapshotted.
- A backup taken while the app writes is a consistent dump (MVCC) but the vault tarball is not atomic with it.

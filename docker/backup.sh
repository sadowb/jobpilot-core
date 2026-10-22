#!/usr/bin/env bash
# Compose-native backup of the JobPilot database and vault (replaces infra/pg-backup.sh).
#   backup.sh daemon                       default: a bundle whenever the newest one is older than BACKUP_INTERVAL_HOURS
#   backup.sh now                          one bundle immediately, prints its path
#   backup.sh list                         bundles, newest first
#   backup.sh verify <bundle>              bundle structure + pg_restore --list, touches nothing
#   backup.sh restore-db <bundle> [dbname] restore the dump into a database (created when missing), default $PGDATABASE
#   backup.sh restore-vault <bundle> <dir> extract the vault into an EMPTY directory
# A bundle is jobpilot-YYYYmmdd-HHMMSS.tar.gz = jobpilot.dump (pg_dump -Fc) + vault.tar.gz, mode 0600, in $BACKUP_DIR.
# The newest BACKUP_KEEP bundles are kept. Nothing here deletes anything else.
set -euo pipefail
umask 077
BACKUP_DIR="${BACKUP_DIR:-/backups}"
VAULT="${JOBPILOT_VAULT:-/vault}"
KEEP="${BACKUP_KEEP:-14}"
INTERVAL_HOURS="${BACKUP_INTERVAL_HOURS:-24}"
export PGHOST="${PGHOST:-postgres}" PGPORT="${PGPORT:-5432}" PGUSER="${PGUSER:-jobpilot}" PGDATABASE="${PGDATABASE:-jobpilot}"
log() { echo "$(date -u +%FT%TZ) backup: $*" >&2; }

bundles() { ls -1t "$BACKUP_DIR"/jobpilot-*.tar.gz 2>/dev/null || true; }

backup_now() {
  mkdir -p "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"
  local out stage
  out="$BACKUP_DIR/jobpilot-$(date +%Y%m%d-%H%M%S).tar.gz"
  stage="$(mktemp -d "$BACKUP_DIR/.stage-XXXXXXXX")"
  trap 'rm -rf "$stage"' RETURN
  pg_dump -Fc -f "$stage/jobpilot.dump" "$PGDATABASE"
  pg_restore --list "$stage/jobpilot.dump" >/dev/null   # the dump must be readable, or the bundle is worthless
  # tar keeps CV symlinks, the content-addressed _cv-store, git history and the owner's uncommitted notes
  tar -czf "$stage/vault.tar.gz" -C "$VAULT" .
  tar -czf "$stage/bundle.tar.gz" -C "$stage" jobpilot.dump vault.tar.gz
  mv "$stage/bundle.tar.gz" "$out"
  chmod 600 "$out"
  # retention: one window over completed bundles only
  bundles | tail -n +"$((KEEP + 1))" | while IFS= read -r old; do rm -f -- "$old"; done
  echo "$out"
}

need_bundle() {
  local f="$1"
  [ -f "$f" ] || { log "no such bundle: $f"; exit 2; }
  local listing
  listing="$(tar -tzf "$f")"   # not piped into grep -q: pipefail would fail on SIGPIPE
  grep -qx jobpilot.dump <<<"$listing" && grep -qx vault.tar.gz <<<"$listing" || { log "not a JobPilot bundle: $f"; exit 2; }
}

case "${1:-daemon}" in
  now) backup_now ;;
  list) bundles ;;
  verify)
    need_bundle "${2:?bundle path}"
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    tar -xzf "$2" -C "$tmp" jobpilot.dump
    pg_restore --list "$tmp/jobpilot.dump" >/dev/null && log "ok: $2" ;;
  restore-db)
    need_bundle "${2:?bundle path}"
    target="${3:-$PGDATABASE}"
    [[ "$target" =~ ^[a-zA-Z_][a-zA-Z0-9_]{0,62}$ ]] || { log "invalid database name: $target"; exit 2; }
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    tar -xzf "$2" -C "$tmp" jobpilot.dump
    if ! psql -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='$target'" | grep -q 1; then createdb "$target"; fi
    # --clean --if-exists replaces objects of an existing database: restoring over the live one is deliberate, stop the app first
    pg_restore -d "$target" --clean --if-exists --no-owner "$tmp/jobpilot.dump"
    log "restored $2 into database $target" ;;
  restore-vault)
    need_bundle "${2:?bundle path}"
    dir="${3:?empty target directory}"
    mkdir -p "$dir"
    [ -z "$(ls -A "$dir")" ] || { log "refusing: $dir is not empty"; exit 2; }
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    tar -xzf "$2" -C "$tmp" vault.tar.gz
    tar -xzf "$tmp/vault.tar.gz" -C "$dir"
    log "vault restored into $dir" ;;
  daemon)
    log "daemon: every ${INTERVAL_HOURS}h, keeping $KEEP, into $BACKUP_DIR"
    while true; do
      newest="$(bundles | head -n1)"
      if [ -z "$newest" ] || [ -n "$(find "$newest" -mmin +"$((INTERVAL_HOURS * 60))" 2>/dev/null)" ]; then
        backup_now >/dev/null && log "bundle written" || log "FAILED (will retry in 1h)"
      fi
      sleep 3600
    done ;;
  *) log "unknown command: $1"; exit 2 ;;
esac

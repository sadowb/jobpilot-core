#!/usr/bin/env bash
# Create (idempotently) a dedicated PostgreSQL cluster for JobPilot on 127.0.0.1:5433.
# Native Postgres, no Docker. Data lives in data/pgdata (persisted on disk, WAL + fsync default).
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PGBIN="${PGBIN:-}"
for c in /Applications/Postgres.app/Contents/Versions/16/bin /Library/PostgreSQL/16/bin /opt/homebrew/opt/postgresql@16/bin; do
  [ -z "$PGBIN" ] && [ -x "$c/initdb" ] && PGBIN="$c"
done
[ -n "$PGBIN" ] || { echo "No PostgreSQL 16 binaries found; set PGBIN" >&2; exit 1; }

PGDATA="$ROOT/data/pgdata"
PORT="${PGPORT:-5433}"
ENVFILE="$ROOT/.env"

mkdir -p "$ROOT/data/backups"

if [ ! -f "$PGDATA/PG_VERSION" ]; then
  PW="$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-32)"
  echo "$PW" > "$ROOT/data/.pgpass-init" && chmod 600 "$ROOT/data/.pgpass-init"
  "$PGBIN/initdb" -D "$PGDATA" -U jobpilot --pwfile="$ROOT/data/.pgpass-init" \
    --auth-local=scram-sha-256 --auth-host=scram-sha-256 -E UTF8 --locale=en_US.UTF-8
  {
    echo "listen_addresses = '127.0.0.1'"
    echo "port = $PORT"
    echo "unix_socket_directories = '$PGDATA'"
    echo "fsync = on"
    echo "synchronous_commit = on"
    echo "full_page_writes = on"
    echo "max_connections = 100"
    echo "log_destination = 'stderr'"
  } >> "$PGDATA/postgresql.conf"
  if [ ! -f "$ENVFILE" ]; then cp "$ROOT/.env.example" "$ENVFILE"; fi
  # shellcheck disable=SC2016
  sed -i '' "s|^PGPASSWORD=.*|PGPASSWORD=$PW|" "$ENVFILE"
  chmod 600 "$ENVFILE"
  rm -f "$ROOT/data/.pgpass-init"
  echo "initialized cluster at $PGDATA"
fi

# Native collector rotates hourly into 168 weekday/hour slots with private permissions.
# Appended outside initdb so an existing cluster receives the settings on restart.
mkdir -p "$PGDATA/log"
chmod 700 "$PGDATA/log"
cat >> "$PGDATA/postgresql.conf" <<'PGLOG'
logging_collector = on
log_directory = 'log'
log_filename = 'postgres-%a-%H.log'
log_file_mode = 0600
log_rotation_age = 60
log_rotation_size = 0
log_truncate_on_rotation = on
PGLOG
# launchd captures startup failures; normal server output goes to the native collector.
touch "$ROOT/data/postgres.log"
chmod 600 "$ROOT/data/postgres.log"
# Preserve a previous startup log rather than deleting it.
if [ "$(wc -c < "$ROOT/data/postgres.log")" -gt 1048576 ]; then
  mv "$ROOT/data/postgres.log" "$ROOT/data/postgres-startup-$(date +%Y%m%d-%H%M%S).log"
  touch "$ROOT/data/postgres.log"
  chmod 600 "$ROOT/data/postgres.log"
fi

# launchd agent: auto-start on login, auto-restart on crash
PLIST="$HOME/Library/LaunchAgents/com.jobpilot.postgres.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.jobpilot.postgres</string>
  <key>ProgramArguments</key><array>
    <string>$PGBIN/postgres</string><string>-D</string><string>$PGDATA</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>$ROOT/data/postgres.log</string>
  <key>StandardOutPath</key><string>$ROOT/data/postgres.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

# create the database (retry while the server starts)
for _ in $(seq 1 30); do
  "$PGBIN/pg_isready" -h 127.0.0.1 -p "$PORT" -q && break || sleep 1
done
PGPASSWORD="$(grep '^PGPASSWORD=' "$ENVFILE" | cut -d= -f2-)" \
  "$PGBIN/psql" -h 127.0.0.1 -p "$PORT" -U jobpilot -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='jobpilot'" | grep -q 1 || \
PGPASSWORD="$(grep '^PGPASSWORD=' "$ENVFILE" | cut -d= -f2-)" \
  "$PGBIN/createdb" -h 127.0.0.1 -p "$PORT" -U jobpilot jobpilot
echo "JobPilot Postgres ready on 127.0.0.1:$PORT (db jobpilot). Next: bin/jp-migrate"

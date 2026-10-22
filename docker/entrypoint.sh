#!/bin/sh
# Containers reach the host's BrowserOS neo through a loopback relay: Neo rejects requests whose Host header is not a
# loopback name (DNS-rebinding protection), so clients inside the container must see http://127.0.0.1:<NEO_PORT>.
# NEO_PORT is the only Neo variable (compose.yaml derives NEO_MCP_URL from it); the host is always host.docker.internal.
set -eu
if [ -n "${NEO_PORT:-}" ]; then
  socat "TCP-LISTEN:${NEO_PORT},bind=127.0.0.1,fork,reuseaddr" "TCP:host.docker.internal:${NEO_PORT}" &
fi
exec "$@"

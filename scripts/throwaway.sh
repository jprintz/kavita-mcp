#!/usr/bin/env bash
# A throwaway Kavita + File Browser laid out like the VPS (vps-apps apps/kavita):
# the volume kmcp-it-lib is File Browser's root (the upload page) and holds Books/
# and Reports/; Kavita mounts Books at /books and Reports at /reports, read-only.
# Network aliases "kavita" and "kavita-upload" match kavita-mcp's defaults.
#
# Usage: scripts/throwaway.sh up | mcp | down
#   up    start both and set Kavita up (admin, libraries, folder watching, keys);
#         writes .throwaway/env (keys inside: never print it)
#   mcp   build this checkout's image and (re)start three kavita-mcp containers:
#         :18000 with the admin key, :18001 with a non-admin key, :18002 with a wrong key
#   down  remove the containers, the network and the volumes
set -euo pipefail
cd "$(dirname "$0")/.."

KAVITA_IMAGE=ghcr.io/kareadita/kavita:0.9.1
FILEBROWSER_IMAGE=docker.io/filebrowser/filebrowser:v2.63.23
HELPER_IMAGE=docker.io/library/alpine:3.22
NET=kmcp-it
LIB=kmcp-it-lib
STATE=.throwaway
MCP_CONTAINERS="kmcp-it-mcp kmcp-it-mcp-reader kmcp-it-mcp-badkey"

down() {
  # shellcheck disable=SC2086
  docker rm -f kmcp-it-kavita kmcp-it-upload $MCP_CONTAINERS >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  docker volume rm "$LIB" kmcp-it-kavita kmcp-it-fbdb >/dev/null 2>&1 || true
  rm -rf "$STATE"
}

up() {
  down
  mkdir -p "$STATE"
  docker network create "$NET" >/dev/null
  uid=$(docker run --rm --entrypoint id "$FILEBROWSER_IMAGE" -u)
  gid=$(docker run --rm --entrypoint id "$FILEBROWSER_IMAGE" -g)
  # File Browser owns the library and its database, like :U does on the server.
  docker run --rm -v "$LIB":/v -v kmcp-it-fbdb:/db "$HELPER_IMAGE" sh -c \
    "mkdir -p /v/Books /v/Reports && chown -R $uid:$gid /v /db && chmod 0755 /v /v/Books /v/Reports"
  docker run -d --name kmcp-it-upload --network "$NET" --network-alias kavita-upload \
    -p 127.0.0.1:8088:8080 -v "$LIB":/srv -v kmcp-it-fbdb:/database \
    -v "$PWD/scripts/upload-start.sh":/upload-start.sh:ro \
    --entrypoint /bin/sh "$FILEBROWSER_IMAGE" /upload-start.sh >/dev/null
  docker run -d --name kmcp-it-kavita --network "$NET" --network-alias kavita \
    -p 127.0.0.1:5055:5000 -v kmcp-it-kavita:/kavita/config \
    --mount "type=volume,src=$LIB,dst=/books,volume-subpath=Books,readonly" \
    --mount "type=volume,src=$LIB,dst=/reports,volume-subpath=Reports,readonly" \
    "$KAVITA_IMAGE" >/dev/null
  uv run python scripts/throwaway_setup.py
}

start_mcp() { # name host-port key
  docker rm -f "$1" >/dev/null 2>&1 || true
  docker run -d --name "$1" --network "$NET" -p "127.0.0.1:$2:8000" \
    --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
    -e KAVITA_API_KEY="$3" -e KAVITA_PUBLIC_URL=http://127.0.0.1:5055 -e SCAN_TIMEOUT_SECONDS=60 \
    kavita-mcp:dev >/dev/null
}

mcp() {
  [ -f "$STATE/env" ] || { echo "run '$0 up' first" >&2; exit 1; }
  # shellcheck disable=SC1091
  . "$STATE/env"
  docker build -q -t kavita-mcp:dev . >/dev/null
  start_mcp kmcp-it-mcp 18000 "$KAVITA_API_KEY"
  start_mcp kmcp-it-mcp-reader 18001 "$KAVITA_NONADMIN_KEY"
  start_mcp kmcp-it-mcp-badkey 18002 "WrongKeyWrongKeyWrongKey0123456"
  for port in 18000 18001 18002; do
    for _ in $(seq 1 30); do
      curl -fsS -o /dev/null "http://127.0.0.1:$port/healthz" 2>/dev/null && break
      curl -s -o /dev/null "http://127.0.0.1:$port/healthz" 2>/dev/null && break
      sleep 1
    done
  done
  echo "kavita-mcp: http://127.0.0.1:18000/mcp (admin key), :18001 (non-admin), :18002 (wrong key)"
}

case "${1:-}" in
  up) up ;;
  mcp) mcp ;;
  down) down ;;
  *) echo "usage: $0 up | mcp | down" >&2; exit 2 ;;
esac

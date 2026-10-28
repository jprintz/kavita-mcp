#!/bin/sh
# Copy of vps-apps apps/kavita/config/upload-start.sh (github.com/jprintz/vps-apps), so the
# throwaway File Browser behaves like the one on the server.
# Starts File Browser (kavita-upload.container) with two settings it only keeps
# in its database, not as flags:
#   - no login: Cloudflare Access (julius only) and cloudflared's token check
#     are the gate;
#   - uploads world-readable (0644 files, 0755 directories): Kavita reads the
#     library through the "other" permission bits, from another user namespace.
#     File Browser's defaults (0640, 0750) would hide every book from it.
set -eu
db=/database/filebrowser.db
settings="--auth.method=noauth --fileMode=0644 --dirMode=0755"
if [ ! -f "$db" ]; then
  # shellcheck disable=SC2086
  filebrowser -d "$db" config init $settings
  # noauth acts as the first user, so one must exist. Nobody logs in with it.
  filebrowser -d "$db" users add kavita "$(head -c 32 /dev/urandom | base64)" --perm.admin
else
  # shellcheck disable=SC2086
  filebrowser -d "$db" config set $settings
fi
exec filebrowser -d "$db" --address 0.0.0.0 --port 8080 --root /srv

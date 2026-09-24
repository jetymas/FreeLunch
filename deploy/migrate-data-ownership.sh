#!/bin/sh
set -eu

# This helper is run only by the one-shot Compose migration service. Keep the
# target fixed: it is the dedicated SQLite data bind mount, never the install
# directory or another host path.
DATA_DIR=/app/data
APP_UID=10001
APP_GID=10001
MARKER="$DATA_DIR/.freelunch-ownership-v1"

if [ "$(id -u)" -ne 0 ]; then
    echo "ownership migration must run as root" >&2
    exit 1
fi
[ -d "$DATA_DIR" ] || { echo "data bind mount is missing: $DATA_DIR" >&2; exit 1; }
[ ! -L "$DATA_DIR" ] || { echo "refusing symlinked data directory: $DATA_DIR" >&2; exit 1; }
[ ! -L "$MARKER" ] || { echo "refusing symlinked ownership marker: $MARKER" >&2; exit 1; }

if [ "${FREELUNCH_ALLOW_DATA_CHOWN:-0}" = "1" ]; then
    if [ ! -e "$MARKER" ]; then
        if find "$DATA_DIR" -xdev ! -type f ! -type d ! -type l -print -quit | grep -q .; then
            echo "refusing unexpected special files in $DATA_DIR" >&2
            exit 1
        fi
        # This may touch only contents of the dedicated FreeLunch data mount.
        find "$DATA_DIR" -xdev \( -type f -o -type d \) -exec chown "$APP_UID:$APP_GID" {} +
        chmod 0700 "$DATA_DIR"
        : > "$MARKER"
        chown "$APP_UID:$APP_GID" "$MARKER"
        chmod 0600 "$MARKER"
    fi
else
    # Docker Desktop/Windows shared paths have host-managed permission
    # translation. Never recursively chown them; prove the app UID can write.
    python - "$DATA_DIR" "$APP_UID" "$APP_GID" <<'PY'
import os
import sys
import tempfile

path, uid, gid = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
pid = os.fork()
if pid == 0:
    try:
        os.setgid(gid)
        os.setuid(uid)
        for entry in os.scandir(path):
            if entry.is_file(follow_symlinks=False) and not os.access(entry.path, os.W_OK):
                raise PermissionError(f"existing data file is not writable: {entry.name}")
        fd, probe = tempfile.mkstemp(prefix=".freelunch-write-check-", dir=path)
        os.close(fd)
        os.unlink(probe)
    except Exception as exc:
        print(f"data directory is not writable by FreeLunch UID {uid}: {exc}", file=sys.stderr)
        os._exit(1)
    os._exit(0)
_, status = os.waitpid(pid, 0)
if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
    print("On Docker Desktop, grant write access to the selected data directory in Windows, or move the existing data into a Docker named volume before upgrading. No ownership changes were made.", file=sys.stderr)
    sys.exit(1)
PY
fi

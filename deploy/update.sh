#!/usr/bin/env bash
# =============================================================================
#  RHM Extractor – upgrade an existing installation with a newer release.
#  Unpack the new release anywhere on the server, then (as root):
#      sudo bash deploy/update.sh
#  Steps: pre-update backup -> sync code -> pip install -> migrate ->
#         collectstatic -> restart (with automatic health check).
# =============================================================================
set -euo pipefail
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER=rhm; BASE=/opt/rhm; APP_DIR=$BASE/app; VENV=$BASE/venv; ENV_FILE=/etc/rhm/rhm.env

info() { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mxx  %s\033[0m\n' "$*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "Run as root."
[ -f "$ENV_FILE" ] || die "No installation found – run deploy/install.sh first."
[ -f "$SRC_DIR/manage.py" ] || die "Run from inside the unpacked release folder."
[ "$SRC_DIR" != "$APP_DIR" ] || die "Run update.sh from the NEW release folder, not from $APP_DIR."

manage() { runuser -u "$APP_USER" -- env RHM_ENV_FILE="$ENV_FILE" PYTHONDONTWRITEBYTECODE=1 "$VENV/bin/python" "$APP_DIR/manage.py" "$@"; }
PORT="$(grep -E '^GUNICORN_BIND=' "$ENV_FILE" | cut -d: -f2)"; PORT="${PORT:-8001}"

info "Pre-update backup"
systemctl start rhm-backup.service || die "Backup failed – aborting update."

info "Keeping previous release for rollback in $BASE/app.previous"
rm -rf "$BASE/app.previous"; cp -a "$APP_DIR" "$BASE/app.previous"

info "Syncing code"
rsync -a --delete \
  --exclude '.env' --exclude '.venv/' --exclude 'db.sqlite3' --exclude 'private_media/' \
  --exclude 'staticfiles/' --exclude 'backups/' --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude '*.bat' --exclude '.git/' "$SRC_DIR/" "$APP_DIR/"
chown -R root:"$APP_USER" "$APP_DIR"
find "$APP_DIR" -type d -exec chmod 0750 {} +
find "$APP_DIR" -type f -exec chmod 0640 {} +
chmod 0750 "$APP_DIR"/deploy/*.sh "$APP_DIR"/deploy/bin/*
install -m 0755 "$APP_DIR/deploy/bin/rhm-manage" /usr/local/bin/rhm-manage

info "Python packages"
"$VENV/bin/pip" install -q -r "$APP_DIR/requirements-prod.txt"

info "Migrations and static files"
( cd "$APP_DIR" && manage migrate --noinput && manage createcachetable && manage collectstatic --noinput -v0 \
  && manage check --deploy --fail-level ERROR )

info "Refreshing systemd units and restarting"
install -m 0644 "$APP_DIR"/deploy/systemd/*.service "$APP_DIR"/deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl restart rhm-gunicorn.service
sleep 3
if curl -fsS "http://127.0.0.1:$PORT/healthz" -H "Host: localhost" >/dev/null; then
  info "Update complete – application healthy."
else
  echo "Health check FAILED. Roll back code with:"
  echo "  sudo rsync -a --delete $BASE/app.previous/ $APP_DIR/ && sudo systemctl restart rhm-gunicorn"
  echo "(database migrations are not reverted automatically; restore from /var/lib/rhm/backups if needed)"
  exit 1
fi

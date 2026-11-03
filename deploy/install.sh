#!/usr/bin/env bash
# =============================================================================
#  RHM Extractor – production installer (Ubuntu/Debian; RHEL/Rocky/Alma best-effort)
#  Web server: Apache (auto-selected when present) or Nginx.
#
#  Usage (from the unpacked project folder, as root):
#     sudo DOMAIN=rhm.example.org LE_EMAIL=admin@example.org bash deploy/install.sh
#
#  Options (environment variables):
#     DOMAIN            Server name (FQDN). Omit for IP-only access.
#     LE_EMAIL          With DOMAIN: obtain a free Let's Encrypt certificate.
#     SSL_CERT/SSL_KEY  Use your own certificate files instead of Let's Encrypt.
#     DB_MODE           local (default – installs MySQL on this server) | external
#     DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD   (required when DB_MODE=external)
#     ADMIN_EMAIL ADMIN_PASSWORD DOWNLOAD_PASSWORD   (prompted if not given)
#     APP_PORT          Gunicorn port on 127.0.0.1 (default 8001)
#     WEB_SERVER        auto (default: Apache if already installed, else Nginx) | apache | nginx
#
#  Re-running is safe: existing secrets in /etc/rhm/rhm.env are kept.
# =============================================================================
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER=rhm
BASE=/opt/rhm
APP_DIR=$BASE/app
VENV=$BASE/venv
STATIC_DIR=$BASE/static
DATA_DIR=/var/lib/rhm
LOG_DIR=/var/log/rhm
ETC_DIR=/etc/rhm
ENV_FILE=$ETC_DIR/rhm.env
DB_CNF=$ETC_DIR/mysql-backup.cnf

DOMAIN="${DOMAIN:-}"
LE_EMAIL="${LE_EMAIL:-}"
SSL_CERT="${SSL_CERT:-}"
SSL_KEY="${SSL_KEY:-}"
DB_MODE="${DB_MODE:-local}"
APP_PORT="${APP_PORT:-8001}"
WEB_SERVER="${WEB_SERVER:-auto}"

c_info() { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
c_warn() { printf '\033[1;33m!!  %s\033[0m\n' "$*"; }
die()    { printf '\033[1;31mxx  %s\033[0m\n' "$*" >&2; exit 1; }
rand()   { python3 -c "import secrets,string as s; print(''.join(secrets.choice(s.ascii_letters+s.digits) for _ in range(${1:-40})))"; }
# DB password: always contains upper, lower, digit and a symbol (-_.) so it passes
# MySQL's validate_password policy, and is still safe in env files, my.cnf and SQL.
dbpass() { python3 -c "
import secrets, string as s
pools = [s.ascii_uppercase, s.ascii_lowercase, s.digits, '-_.']
chars = [secrets.choice(p) for p in pools] + [secrets.choice(''.join(pools)) for _ in range(${1:-32} - 4)]
secrets.SystemRandom().shuffle(chars)
print(''.join(chars))"; }
envget() { [ -f "$ENV_FILE" ] && grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }

[ "$(id -u)" -eq 0 ] || die "Run as root (sudo bash deploy/install.sh)."
[ -f "$SRC_DIR/manage.py" ] || die "Run this script from inside the unpacked rhm_extractor folder."

# ----------------------------------------------------------------- platform
if command -v apt-get >/dev/null 2>&1; then
  PKG=apt
  NGINX_SITE=/etc/nginx/sites-available/rhm; NGINX_LINK=/etc/nginx/sites-enabled/rhm
  APACHE_SVC=apache2; APACHE_ETC=/etc/apache2; APACHE_SITE=/etc/apache2/sites-available/rhm.conf
elif command -v dnf >/dev/null 2>&1; then
  PKG=dnf
  NGINX_SITE=/etc/nginx/conf.d/rhm.conf; NGINX_LINK=""
  APACHE_SVC=httpd; APACHE_ETC=/etc/httpd; APACHE_SITE=/etc/httpd/conf.d/rhm.conf
else
  die "Unsupported distribution (need apt or dnf)."
fi

# ----------------------------------------------------------------- web server
if [ "$WEB_SERVER" = auto ]; then
  if command -v apache2 >/dev/null 2>&1 || command -v httpd >/dev/null 2>&1 \
     || systemctl is-active --quiet "$APACHE_SVC" 2>/dev/null; then
    WEB_SERVER=apache
  else
    WEB_SERVER=nginx
  fi
fi
case "$WEB_SERVER" in apache|nginx) ;; *) die "WEB_SERVER must be apache or nginx." ;; esac
c_info "Web server: $WEB_SERVER"

# ----------------------------------------------------------------- TLS mode
if [ -n "$SSL_CERT" ] && [ -n "$SSL_KEY" ]; then
  [ -f "$SSL_CERT" ] && [ -f "$SSL_KEY" ] || die "SSL_CERT / SSL_KEY files not found."
  TLS_MODE=custom
elif [ -n "$DOMAIN" ] && [ -n "$LE_EMAIL" ]; then
  TLS_MODE=letsencrypt
else
  TLS_MODE=none
  c_warn "No certificate configured – the site will run on plain HTTP. Use HTTPS for production."
fi
HTTPS_ENABLED=$([ "$TLS_MODE" = none ] && echo False || echo True)

# ----------------------------------------------------------------- packages
c_info "Installing system packages ($PKG)"
if [ "$PKG" = apt ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  PKGS=(python3 python3-venv python3-dev build-essential pkg-config default-libmysqlclient-dev
        rsync curl ca-certificates default-mysql-client)
  [ "$WEB_SERVER" = nginx ] && PKGS+=(nginx) || PKGS+=(apache2)
  [ "$DB_MODE" = local ] && PKGS+=(default-mysql-server)
  [ "$TLS_MODE" = letsencrypt ] && PKGS+=(certbot "python3-certbot-$WEB_SERVER")
  apt-get install -y -q "${PKGS[@]}"
  PY=python3
else
  dnf install -y -q epel-release || true
  PKGS=(python3.11 python3.11-devel gcc pkgconf-pkg-config mysql-devel rsync curl mysql)
  [ "$WEB_SERVER" = nginx ] && PKGS+=(nginx) || PKGS+=(httpd mod_ssl)
  [ "$DB_MODE" = local ] && PKGS+=(mysql-server)
  [ "$TLS_MODE" = letsencrypt ] && PKGS+=(certbot "python3-certbot-$WEB_SERVER")
  dnf install -y -q "${PKGS[@]}"
  PY=python3.11
fi
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python 3.10+ is required (found $($PY -V)). Use Ubuntu 22.04+/Debian 12+ or install python3.11."

# ----------------------------------------------------------------- user & dirs
c_info "Creating service user and directories"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$APP_USER"
install -d -m 0755 -o root -g root "$BASE" "$APP_DIR"
install -d -m 0755 -o "$APP_USER" -g "$APP_USER" "$STATIC_DIR"
install -d -m 0700 -o "$APP_USER" -g "$APP_USER" "$DATA_DIR" "$DATA_DIR/private_media" "$DATA_DIR/backups"
install -d -m 0750 -o "$APP_USER" -g "$APP_USER" "$LOG_DIR"
install -d -m 0750 -o root -g "$APP_USER" "$ETC_DIR"

# ----------------------------------------------------------------- code
c_info "Copying application code to $APP_DIR"
rsync -a --delete \
  --exclude '.env' --exclude '.venv/' --exclude 'db.sqlite3' --exclude 'private_media/' \
  --exclude 'staticfiles/' --exclude 'backups/' --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude '*.bat' --exclude '.git/' \
  "$SRC_DIR/" "$APP_DIR/"
chown -R root:"$APP_USER" "$APP_DIR"
find "$APP_DIR" -type d -exec chmod 0750 {} +
find "$APP_DIR" -type f -exec chmod 0640 {} +
chmod 0750 "$APP_DIR"/deploy/*.sh "$APP_DIR"/deploy/bin/*

# ----------------------------------------------------------------- python env
c_info "Creating virtualenv and installing Python packages"
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip wheel
"$VENV/bin/pip" install -q -r "$APP_DIR/requirements-prod.txt"

# ----------------------------------------------------------------- database
DB_NAME="${DB_NAME:-$(envget DB_NAME)}";         DB_NAME="${DB_NAME:-rhm_extractor}"
DB_USER="${DB_USER:-$(envget DB_USER)}";         DB_USER="${DB_USER:-rhm}"
DB_PASSWORD="${DB_PASSWORD:-$(envget DB_PASSWORD)}"
DB_HOST="${DB_HOST:-$(envget DB_HOST)}";         DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-$(envget DB_PORT)}";         DB_PORT="${DB_PORT:-3306}"

if [ "$DB_MODE" = local ]; then
  c_info "Configuring local MySQL"
  systemctl enable --now mysql 2>/dev/null || systemctl enable --now mariadb 2>/dev/null || systemctl enable --now mysqld
  DB_PASSWORD="${DB_PASSWORD:-$(dbpass 32)}"
  # Relax MySQL's password-validation policy to LOW (length >= 8 only) if the component is active.
  mysql -uroot -e "SET PERSIST validate_password.policy = LOW; SET PERSIST validate_password.length = 8;" 2>/dev/null \
    || mysql -uroot -e "SET GLOBAL validate_password_policy = LOW; SET GLOBAL validate_password_length = 8;" 2>/dev/null \
    || true
  # Time-zone tables are required for date grouping with USE_TZ (dashboard charts)
  if [ "$(mysql -N -uroot -e 'SELECT COUNT(*) FROM mysql.time_zone_name' 2>/dev/null || echo 0)" = "0" ]; then
    (mysql_tzinfo_to_sql /usr/share/zoneinfo 2>/dev/null || mariadb-tzinfo-to-sql /usr/share/zoneinfo 2>/dev/null) \
      | mysql -uroot mysql
  fi
  mysql -uroot <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASSWORD';
ALTER USER '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASSWORD';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost';
FLUSH PRIVILEGES;
SQL
else
  [ -n "$DB_PASSWORD" ] || die "DB_MODE=external needs DB_HOST, DB_NAME, DB_USER and DB_PASSWORD."
  c_warn "Using external database $DB_USER@$DB_HOST:$DB_PORT/$DB_NAME – make sure its time-zone tables are loaded."
fi

# ----------------------------------------------------------------- env file
SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
HOSTS="localhost,127.0.0.1${SERVER_IP:+,$SERVER_IP}${DOMAIN:+,$DOMAIN}"
SCHEME=$([ "$HTTPS_ENABLED" = True ] && echo https || echo http)
ORIGINS="${DOMAIN:+$SCHEME://$DOMAIN}"
[ -n "$SERVER_IP" ] && ORIGINS="${ORIGINS:+$ORIGINS,}$SCHEME://$SERVER_IP"

if [ ! -f "$ENV_FILE" ]; then
  c_info "Writing $ENV_FILE"
  umask 027
  cat > "$ENV_FILE" <<ENV
# RHM Extractor production settings – generated $(date -Is). Keep this file secret.
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=$(rand 64)
DJANGO_ALLOWED_HOSTS=$HOSTS
DJANGO_CSRF_TRUSTED_ORIGINS=$ORIGINS
HTTPS_ENABLED=$HTTPS_ENABLED
TIME_ZONE=Africa/Nairobi

DB_ENGINE=mysql
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASSWORD
DB_HOST=$DB_HOST
DB_PORT=$DB_PORT

STATIC_ROOT=$STATIC_DIR
PRIVATE_MEDIA_ROOT=$DATA_DIR/private_media
BACKUP_DIR=$DATA_DIR/backups
BACKUP_KEEP_DAYS=14
MPESAV3_SCRIPT_PATH=$APP_DIR/legacy/mpesav3.py

GUNICORN_BIND=127.0.0.1:$APP_PORT
GUNICORN_TIMEOUT=120

DOWNLOAD_MAX_ATTEMPTS=5
DOWNLOAD_LOCKOUT_SECONDS=900
MAX_UPLOAD_MB=5
MAX_MESSAGES_PER_BATCH=2000

RETENTION_DAYS_TRANSACTIONS=0
RETENTION_DAYS_REPORTS=365
RETENTION_DAYS_AUDIT=730

# Password-reset emails. Configure SMTP for real delivery:
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
# EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
# EMAIL_HOST=smtp.office365.com
# EMAIL_PORT=587
# EMAIL_HOST_USER=
# EMAIL_HOST_PASSWORD=
# DEFAULT_FROM_EMAIL=RHM Extractor <no-reply@example.org>
# DJANGO_ADMINS=it@example.org
LOG_LEVEL=INFO
ENV
  umask 022
else
  c_info "Keeping existing $ENV_FILE"
  sed -i "s/^DB_PASSWORD=.*/DB_PASSWORD=$DB_PASSWORD/" "$ENV_FILE"
fi
chown root:"$APP_USER" "$ENV_FILE"; chmod 0640 "$ENV_FILE"

cat > "$DB_CNF" <<CNF
[client]
user=$DB_USER
password=$DB_PASSWORD
host=$DB_HOST
port=$DB_PORT
CNF
chown root:"$APP_USER" "$DB_CNF"; chmod 0640 "$DB_CNF"

# ----------------------------------------------------------------- django
install -m 0755 "$APP_DIR/deploy/bin/rhm-manage" /usr/local/bin/rhm-manage
manage() { runuser -u "$APP_USER" -- env RHM_ENV_FILE="$ENV_FILE" PYTHONDONTWRITEBYTECODE=1 "$VENV/bin/python" "$APP_DIR/manage.py" "$@"; }
c_info "Running migrations and collecting static files"
( cd "$APP_DIR" && manage migrate --noinput && manage createcachetable && manage collectstatic --noinput -v0 )
( cd "$APP_DIR" && manage check --deploy --fail-level ERROR )

c_info "Ensuring an administrator and the download password exist"
BOOT_ARGS=()
[ -n "${ADMIN_EMAIL:-}" ] && BOOT_ARGS+=(--email "$ADMIN_EMAIL")
[ -n "${ADMIN_PASSWORD:-}" ] && BOOT_ARGS+=(--password "$ADMIN_PASSWORD")
[ -n "${DOWNLOAD_PASSWORD:-}" ] && BOOT_ARGS+=(--download-password "$DOWNLOAD_PASSWORD")
( cd "$APP_DIR" && manage bootstrap "${BOOT_ARGS[@]}" )

# ----------------------------------------------------------------- systemd
c_info "Installing systemd services and timers"
install -m 0644 "$APP_DIR"/deploy/systemd/*.service "$APP_DIR"/deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now rhm-gunicorn.service
systemctl restart rhm-gunicorn.service
systemctl enable --now rhm-backup.timer rhm-maintenance.timer

# ----------------------------------------------------------------- web server config
port_owner() { ss -ltnpH "sport = :$1" 2>/dev/null | grep -oE 'users:\(\("[^"]+' | head -1 | cut -d'"' -f2; }

setup_nginx() {
  c_info "Configuring Nginx ($TLS_MODE)"
  local owner; owner="$(port_owner 80)"
  if [ -n "$owner" ] && [ "$owner" != nginx ]; then
    die "Port 80 is already used by '$owner'. Re-run with WEB_SERVER=apache (if it is Apache) or stop '$owner' first."
  fi
  install -d /etc/nginx/snippets
  install -m 0644 "$APP_DIR/deploy/nginx/rhm-proxy.conf" /etc/nginx/snippets/rhm-proxy.conf
  sed "s#__STATIC_ROOT__#$STATIC_DIR#g" "$APP_DIR/deploy/nginx/rhm-locations.conf" > /etc/nginx/snippets/rhm-locations.conf
  sed -i "s#127.0.0.1:8001#127.0.0.1:$APP_PORT#" /etc/nginx/snippets/rhm-proxy.conf
  install -m 0644 "$APP_DIR/deploy/nginx/rhm-ratelimit.conf" /etc/nginx/conf.d/rhm-ratelimit.conf
  local server_name="${DOMAIN:-_}"
  if [ "$TLS_MODE" = custom ]; then
    sed -e "s#__SERVER_NAME__#$server_name#g" -e "s#__SSL_CERT__#$SSL_CERT#g" -e "s#__SSL_KEY__#$SSL_KEY#g" \
      "$APP_DIR/deploy/nginx/rhm-https.conf.template" > "$NGINX_SITE"
  elif [ "$TLS_MODE" = letsencrypt ] && grep -q "managed by Certbot" "$NGINX_SITE" 2>/dev/null; then
    c_info "Keeping existing certbot-managed Nginx site"
  else
    sed "s#__SERVER_NAME__#$server_name#g" "$APP_DIR/deploy/nginx/rhm-http.conf.template" > "$NGINX_SITE"
  fi
  if [ ! -s /proc/net/if_inet6 ]; then
    sed -i '/listen \[::\]/d' "$NGINX_SITE"   # host has IPv6 disabled
  fi
  if [ -n "$NGINX_LINK" ]; then
    ln -sf "$NGINX_SITE" "$NGINX_LINK"
    rm -f /etc/nginx/sites-enabled/default
  fi
  nginx -t
  systemctl enable --now nginx
  systemctl reload nginx
  if [ "$TLS_MODE" = letsencrypt ] && ! grep -q "managed by Certbot" "$NGINX_SITE"; then
    c_info "Requesting Let's Encrypt certificate for $DOMAIN"
    certbot --nginx -d "$DOMAIN" -m "$LE_EMAIL" --agree-tos --non-interactive --redirect
    systemctl reload nginx
  fi
}

setup_apache() {
  c_info "Configuring Apache ($TLS_MODE)"
  # Nginx may have been installed by an earlier run; it must not hold ports 80/443.
  if systemctl is-enabled --quiet nginx 2>/dev/null || systemctl is-active --quiet nginx 2>/dev/null; then
    c_warn "Disabling Nginx so Apache can use ports 80/443"
    systemctl disable --now nginx || true
  fi
  local common="$APACHE_ETC/rhm-common.conf" name_line le_site
  sed -e "s#__STATIC_ROOT__#$STATIC_DIR#g" -e "s#__APP_PORT__#$APP_PORT#g" \
    "$APP_DIR/deploy/apache/rhm-common.conf" > "$common"
  if [ -n "$DOMAIN" ]; then
    name_line="ServerName $DOMAIN"
  else
    name_line="ServerName ${SERVER_IP:-localhost}"
  fi
  le_site="${APACHE_SITE%.conf}-le-ssl.conf"
  if [ "$TLS_MODE" = custom ]; then
    sed -e "s#__SERVER_NAME_LINE__#$name_line#g" -e "s#__COMMON__#$common#g" \
        -e "s#__SSL_CERT__#$SSL_CERT#g" -e "s#__SSL_KEY__#$SSL_KEY#g" \
      "$APP_DIR/deploy/apache/rhm-https.conf.template" > "$APACHE_SITE"
  elif [ "$TLS_MODE" = letsencrypt ] && [ -f "$le_site" ]; then
    c_info "Keeping existing certbot-managed Apache site"
  else
    sed -e "s#__SERVER_NAME_LINE__#$name_line#g" -e "s#__COMMON__#$common#g" \
      "$APP_DIR/deploy/apache/rhm-http.conf.template" > "$APACHE_SITE"
  fi

  if [ "$PKG" = apt ]; then
    a2enmod -q proxy proxy_http headers rewrite expires deflate >/dev/null
    [ "$TLS_MODE" = custom ] && a2enmod -q ssl >/dev/null
    install -m 0644 "$APP_DIR/deploy/apache/rhm-hardening.conf" /etc/apache2/conf-available/zz-rhm-hardening.conf
    a2enconf -q zz-rhm-hardening >/dev/null
    a2ensite -q rhm >/dev/null
    if [ -z "$DOMAIN" ]; then
      # IP-only access: our site must be the default virtual host.
      c_warn "No DOMAIN given – disabling Apache's 000-default site so RHM answers on the server IP"
      a2dissite -q 000-default >/dev/null 2>&1 || true
    fi
  else
    sed -i 's#${APACHE_LOG_DIR}#logs#g' "$APACHE_SITE"
    install -m 0644 "$APP_DIR/deploy/apache/rhm-hardening.conf" /etc/httpd/conf.d/zz-rhm-hardening.conf
  fi
  apachectl configtest
  systemctl enable --now "$APACHE_SVC"
  systemctl reload "$APACHE_SVC"
  if [ "$TLS_MODE" = letsencrypt ] && [ ! -f "$le_site" ]; then
    c_info "Requesting Let's Encrypt certificate for $DOMAIN"
    certbot --apache -d "$DOMAIN" -m "$LE_EMAIL" --agree-tos --non-interactive --redirect
    systemctl reload "$APACHE_SVC"
  fi
}

if [ "$PKG" = dnf ] && command -v setsebool >/dev/null 2>&1; then
  setsebool -P httpd_can_network_connect 1 || true   # allow the web server -> gunicorn on localhost
  chcon -R -t httpd_sys_content_t "$STATIC_DIR" 2>/dev/null || true
fi
if [ "$WEB_SERVER" = apache ]; then setup_apache; else setup_nginx; fi

# ----------------------------------------------------------------- firewall
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --add-service=http --add-service=https >/dev/null && firewall-cmd --reload >/dev/null
fi

# ----------------------------------------------------------------- health
sleep 2
if curl -fsS "http://127.0.0.1:$APP_PORT/healthz" -H "Host: localhost" >/dev/null; then
  c_info "Application is healthy."
else
  c_warn "Health check failed – inspect: journalctl -u rhm-gunicorn -n 100"
fi
URL="$SCHEME://${DOMAIN:-$SERVER_IP}/"
cat <<DONE

  RHM Extractor is installed.
    URL:           $URL
    Config:        $ENV_FILE
    Code:          $APP_DIR      Data: $DATA_DIR
    Web server:    $WEB_SERVER
    Logs:          journalctl -u rhm-gunicorn -f
    Manage:        sudo rhm-manage <command>     (e.g. createsuperuser)
    Update:        sudo bash deploy/update.sh    (from a newer unpacked release)
    Backups:       $DATA_DIR/backups  (daily 02:15, kept 14 days)

DONE

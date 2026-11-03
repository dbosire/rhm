# Deploying RHM Extractor to a Linux server (MySQL)

Target: **Ubuntu 22.04 / 24.04 or Debian 12** (RHEL/Rocky/Alma 9 supported best-effort).
Stack: **Nginx → Gunicorn (systemd) → Django → MySQL**, with nightly backups.

```
Browser ──HTTPS──► Nginx :443 ──► Gunicorn 127.0.0.1:8001 ──► Django ──► MySQL (local or external)
                   │ /static/ served directly          │
                   │ rate-limits sign-in & download    └─► /var/lib/rhm/private_media (templates, reports)
```

## 1. Prepare
* A server with 1–2 vCPU, 2 GB RAM, 20 GB disk is plenty.
* DNS: point your domain (e.g. `rhm.example.org`) at the server if you want a free Let's Encrypt certificate.
* Open ports 80 and 443 (and 22 for SSH).

## 2. Copy the release to the server
From Windows (PowerShell), in `Documents\RHM`:
```powershell
scp rhm_extractor.zip user@SERVER_IP:/tmp/
```
(or use WinSCP). Then on the server:
```bash
sudo apt-get install -y unzip
cd /tmp && unzip -o rhm_extractor.zip && cd rhm_extractor
```

## 3. Install (one command)
Pick the line that matches your setup:

```bash
# A) Public domain + free Let's Encrypt certificate (recommended)
sudo DOMAIN=rhm.example.org LE_EMAIL=you@example.org bash deploy/install.sh

# B) Your own / company certificate
sudo DOMAIN=rhm.internal SSL_CERT=/etc/ssl/rhm/cert.pem SSL_KEY=/etc/ssl/rhm/key.pem bash deploy/install.sh

# C) Internal network, plain HTTP by IP (testing only)
sudo bash deploy/install.sh
```
The script asks for the **first administrator's email and password** and the **Excel download password**.
For unattended installs, add `ADMIN_EMAIL=… ADMIN_PASSWORD=… DOWNLOAD_PASSWORD=…`.

**External MySQL** (managed DB / another server) instead of installing MySQL locally:
```bash
sudo DB_MODE=external DB_HOST=10.0.0.5 DB_NAME=rhm_extractor DB_USER=rhm DB_PASSWORD='…' \
     DOMAIN=rhm.example.org LE_EMAIL=you@example.org bash deploy/install.sh
```
The external database must be `utf8mb4` and have **time-zone tables loaded**
(`mysql_tzinfo_to_sql /usr/share/zoneinfo | mysql -u root mysql`) – the dashboard groups by date in Africa/Nairobi time.

### What the installer does
| Step | Result |
|---|---|
| Packages | Python venv, build tools, MySQL client libs, Nginx, (MySQL server), (certbot) |
| User | system user `rhm` (no login shell) |
| Code | `/opt/rhm/app` (read-only to the app), venv `/opt/rhm/venv`, static `/opt/rhm/static` |
| Data | `/var/lib/rhm/private_media` (templates/reports, mode 700), `/var/lib/rhm/backups` |
| Secrets | `/etc/rhm/rhm.env` (random `SECRET_KEY`, DB password; mode 640 root:rhm) |
| MySQL | database `rhm_extractor` (utf8mb4), user `rhm`, time-zone tables |
| Django | migrate, cache table, collectstatic, `check --deploy`, first admin + hashed download password |
| systemd | `rhm-gunicorn.service` (hardened), `rhm-backup.timer` (02:15), `rhm-maintenance.timer` (03:30) |
| Nginx | security headers + CSP, static caching, POST rate-limits on sign-in and downloads, 10 MB upload cap |
| TLS | certbot (auto-renewing) or your certificate; HTTP → HTTPS redirect; HSTS |
| Firewall | opens HTTP/HTTPS if `ufw`/`firewalld` is active |

It is **safe to re-run** – existing secrets, data and certbot config are kept.

## 4. After installing
* Browse to the URL printed at the end and sign in.
* **Settings** → confirm the download password, picklists, defaults.
* **Users** → add staff (Administrator / Reviewer / Standard user).
* **Email for password resets** – edit `/etc/rhm/rhm.env`:
  ```
  EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
  EMAIL_HOST=smtp.office365.com
  EMAIL_PORT=587
  EMAIL_HOST_USER=no-reply@example.org
  EMAIL_HOST_PASSWORD=…
  DEFAULT_FROM_EMAIL=RHM Extractor <no-reply@example.org>
  ```
  then `sudo systemctl restart rhm-gunicorn`.

## 5. Day-to-day operations
| Task | Command |
|---|---|
| Service status / restart | `systemctl status rhm-gunicorn` · `sudo systemctl restart rhm-gunicorn` |
| Live logs | `journalctl -u rhm-gunicorn -f` · `/var/log/nginx/error.log` |
| Health check | `curl http://127.0.0.1:8001/healthz` |
| Django commands | `sudo rhm-manage createsuperuser` · `sudo rhm-manage changepassword you@x.org` |
| Backup now | `sudo systemctl start rhm-backup` → `/var/lib/rhm/backups/db_*.sql.gz`, `files_*.tar.gz` |
| Timers | `systemctl list-timers 'rhm-*'` |
| Retention preview | `sudo rhm-manage purge_old_data --dry-run` (policy in `rhm.env`: `RETENTION_DAYS_*`) |
| Certificate renewal | automatic (`systemctl list-timers certbot*`) |

**Copy backups off the server** (e.g. nightly `rsync`/`rclone` of `/var/lib/rhm/backups` to another machine or cloud storage) – a backup on the same disk does not survive a disk failure.

### Restore
```bash
sudo systemctl stop rhm-gunicorn
gunzip -c /var/lib/rhm/backups/db_YYYYMMDD_HHMMSS.sql.gz | sudo mysql rhm_extractor
sudo tar -xzf /var/lib/rhm/backups/files_YYYYMMDD_HHMMSS.tar.gz -C /var/lib/rhm
sudo chown -R rhm:rhm /var/lib/rhm/private_media
sudo systemctl start rhm-gunicorn
```

## 6. Updating to a new release
```bash
cd /tmp && unzip -o rhm_extractor.zip && cd rhm_extractor
sudo bash deploy/update.sh
```
`update.sh` takes a backup first, keeps the previous code in `/opt/rhm/app.previous`, installs
packages, runs migrations and collectstatic, restarts and health-checks. If the check fails it prints
the one-line rollback command.

## 7. Tuning
In `/etc/rhm/rhm.env`: `GUNICORN_WORKERS` (default 2×CPU+1, max 9), `GUNICORN_TIMEOUT` (120 s),
`MAX_UPLOAD_MB`, `MAX_MESSAGES_PER_BATCH`, `DOWNLOAD_MAX_ATTEMPTS`, `DOWNLOAD_LOCKOUT_SECONDS`,
`SECURE_HSTS_INCLUDE_SUBDOMAINS`. Restart `rhm-gunicorn` after changes.
If you change Nginx rate-limit zones, use `sudo systemctl restart nginx` (a reload does not resize zones).

## Troubleshooting
| Symptom | Fix |
|---|---|
| 502 Bad Gateway | `journalctl -u rhm-gunicorn -n 100` – usually a typo in `rhm.env` or DB unreachable |
| 400 Bad Request | host not in `DJANGO_ALLOWED_HOSTS` in `rhm.env` |
| 403 CSRF on sign-in | add your URL (with `https://`) to `DJANGO_CSRF_TRUSTED_ORIGINS` |
| Cannot sign in over plain HTTP | `HTTPS_ENABLED=False` is required for HTTP-only installs (option C sets it) |
| Dashboard chart empty / DB error on MySQL | load MySQL time-zone tables (see step 3) |
| 429 Too Many Requests | sign-in/download rate limit hit – wait a minute |
| RHEL: 502 with SELinux | `sudo setsebool -P httpd_can_network_connect 1` |

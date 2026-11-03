#!/usr/bin/env bash
# Nightly backup: MySQL dump + private files (templates/reports). Run by rhm-backup.timer.
set -euo pipefail
umask 077

BACKUP_DIR="${BACKUP_DIR:-/var/lib/rhm/backups}"
MEDIA_DIR="${PRIVATE_MEDIA_ROOT:-/var/lib/rhm/private_media}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
DB_CNF="${DB_BACKUP_CNF:-/etc/rhm/mysql-backup.cnf}"
STAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"

# 1) Database (consistent snapshot for InnoDB, routines + triggers included)
mysqldump --defaults-extra-file="$DB_CNF" --single-transaction --quick --routines --triggers \
  --default-character-set=utf8mb4 --no-tablespaces "${DB_NAME:-rhm_extractor}" \
  | gzip -9 > "$BACKUP_DIR/db_${STAMP}.sql.gz.part"
mv "$BACKUP_DIR/db_${STAMP}.sql.gz.part" "$BACKUP_DIR/db_${STAMP}.sql.gz"

# 2) Uploaded templates + generated reports
if [ -d "$MEDIA_DIR" ]; then
  tar -czf "$BACKUP_DIR/files_${STAMP}.tar.gz.part" -C "$(dirname "$MEDIA_DIR")" "$(basename "$MEDIA_DIR")"
  mv "$BACKUP_DIR/files_${STAMP}.tar.gz.part" "$BACKUP_DIR/files_${STAMP}.tar.gz"
fi

# 3) Retention
find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'db_*.sql.gz' -o -name 'files_*.tar.gz' \) -mtime +"$KEEP_DAYS" -delete
find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.part' -mmin +240 -delete

echo "Backup complete: $BACKUP_DIR/db_${STAMP}.sql.gz"

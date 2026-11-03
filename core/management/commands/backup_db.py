"""Database backup: `python manage.py backup_db [--keep 14]`.

Writes a compressed, database-agnostic JSON dump (works for PostgreSQL, MySQL
and SQLite) to BACKUP_DIR and prunes old backups. Schedule it with Windows
Task Scheduler or cron. For PostgreSQL/MySQL you can additionally use
pg_dump / mysqldump – see README.
"""
import gzip
import io
from datetime import datetime

from django.conf import settings
from django.core.management import BaseCommand, call_command


class Command(BaseCommand):
    help = "Create a compressed JSON backup of the database and prune old backups."

    def add_arguments(self, parser):
        parser.add_argument("--keep", type=int, default=14, help="Number of backups to keep")

    def handle(self, *args, **opts):
        out_dir = settings.BACKUP_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        call_command(
            "dumpdata", "--natural-foreign", "--natural-primary", "--indent", "0",
            "--exclude", "contenttypes", "--exclude", "auth.permission", "--exclude", "sessions",
            stdout=buf,
        )
        path = out_dir / f"rhm_backup_{datetime.now():%Y%m%d_%H%M%S}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(buf.getvalue())
        backups = sorted(out_dir.glob("rhm_backup_*.json.gz"))
        for old in backups[: max(len(backups) - opts["keep"], 0)]:
            old.unlink(missing_ok=True)
        self.stdout.write(self.style.SUCCESS(f"Backup written to {path}"))

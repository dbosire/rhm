"""Apply the configurable data-retention policy.

    python manage.py purge_old_data [--dry-run]

RETENTION_DAYS_TRANSACTIONS / _REPORTS / _AUDIT (env vars). 0 = keep forever.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management import BaseCommand
from django.utils import timezone

from core.models import AuditLog, ExtractionBatch, GeneratedReport, Transaction, private_storage


class Command(BaseCommand):
    help = "Delete data older than the configured retention periods."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run=False, **opts):
        now = timezone.now()
        summary = []

        days = settings.RETENTION_DAYS_REPORTS
        if days:
            old = GeneratedReport.objects.filter(created_at__lt=now - timedelta(days=days), state="generated")
            summary.append(f"report files: {old.count()}")
            if not dry_run:
                for r in old:
                    if r.file:
                        private_storage.delete(r.file.name)
                    r.file = ""
                    r.state = GeneratedReport.State.PURGED
                    r.save(update_fields=["file", "state"])

        days = settings.RETENTION_DAYS_TRANSACTIONS
        if days:
            cutoff = now - timedelta(days=days)
            txns = Transaction.objects.filter(created_at__lt=cutoff)
            batches = ExtractionBatch.objects.filter(created_at__lt=cutoff)
            summary.append(f"transactions: {txns.count()}, batches: {batches.count()}")
            if not dry_run:
                txns.delete()
                batches.delete()

        days = settings.RETENTION_DAYS_AUDIT
        if days:
            logs = AuditLog.objects.filter(created_at__lt=now - timedelta(days=days))
            summary.append(f"audit entries: {logs.count()}")
            if not dry_run:
                logs.delete()

        if not dry_run:
            AuditLog.objects.create(action="retention.purge", details={"summary": "; ".join(summary)})
        prefix = "[dry-run] would purge" if dry_run else "Purged"
        self.stdout.write(self.style.SUCCESS(f"{prefix} – " + ("; ".join(summary) or "nothing configured")))

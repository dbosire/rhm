"""First-run helper (idempotent): cache table, first administrator, download password.

    python manage.py bootstrap --email admin@example.com --password "..." --download-password "..."

Anything already configured is left untouched, so it is safe to run on every deploy.
Values not supplied are prompted for when a terminal is attached.
"""
import getpass
import sys

from django.core.management import BaseCommand, CommandError, call_command

from accounts.models import User
from core.models import AppSetting


def _ask(prompt, secret=False):
    if not sys.stdin.isatty():
        raise CommandError(f"{prompt.strip(': ')} is required (pass it as an option when not running interactively).")
    return getpass.getpass(prompt) if secret else input(prompt).strip()


class Command(BaseCommand):
    help = "Create cache table, initial administrator and download password (skips anything already set)."

    def add_arguments(self, parser):
        parser.add_argument("--email")
        parser.add_argument("--password")
        parser.add_argument("--download-password")

    def handle(self, *args, **o):
        call_command("createcachetable", verbosity=0)

        email = (o.get("email") or "").strip().lower()
        if User.objects.filter(is_superuser=True).exists() and not email:
            self.stdout.write("An administrator already exists – skipped.")
        else:
            email = email or _ask("Administrator email: ")
            if User.objects.filter(email__iexact=email).exists():
                self.stdout.write(f"User {email} already exists – skipped.")
            else:
                pw = o.get("password") or _ask("Administrator password: ", secret=True)
                User.objects.create_superuser(email=email, password=pw, full_name="Administrator")
                self.stdout.write(self.style.SUCCESS(f"Administrator {email} created."))

        setting = AppSetting.load()
        if setting.has_download_password:
            self.stdout.write("Download password already configured – skipped.")
        else:
            dpw = o.get("download_password") or _ask("Excel download password: ", secret=True)
            setting.set_download_password(dpw)
            setting.save()
            self.stdout.write(self.style.SUCCESS("Download password set (stored hashed)."))
        self.stdout.write(self.style.SUCCESS("Bootstrap complete."))

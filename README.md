# RHM Extractor – M-Pesa & KCB SMS → Excel (Django)

A Django web application that extracts transaction parameters from M-Pesa and KCB SMS
messages, lets users review and edit them, and produces Excel reports – either a new
13-column workbook or a populated copy of an uploaded template. Glassmorphism UI with
light/dark themes, responsive down to phones.

## Quick start (Windows)

```bat
cd C:\Users\Administrator\Documents\RHM\rhm_extractor
setup_windows.bat        :: creates .venv, installs packages, migrates, asks for admin + download password
run_windows.bat          :: http://127.0.0.1:8000
```

Manual equivalent:

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env            # edit DB settings etc.
python manage.py migrate
python manage.py bootstrap        # admin user + hashed download password + cache table
python manage.py runserver
python manage.py test core        # 19 automated tests
```

### Database
Production uses **MySQL** (`DB_ENGINE=mysql`; MariaDB also works). `sqlite` is for a quick local trial only.

## Deploying to a Linux server
See **[deploy/DEPLOY.md](deploy/DEPLOY.md)** – one command installs Apache or Nginx + Gunicorn + MySQL,
HTTPS, systemd services, nightly backups and maintenance:
```bash
sudo DOMAIN=rhm.example.org LE_EMAIL=you@example.org bash deploy/install.sh
```

## How it works

| Step | Where |
|---|---|
| Paste SMS (single or bulk) or upload TXT / CSV / XLSX, choose Auto / M-Pesa / KCB, set defaults (Report Type, Item, Qty, Unit Cost, Status, optional Submission Date) | **Extract SMS** |
| Review the batch grid: edit any cell, see warnings (missing values, Amount ≠ Qty × Unit Cost, duplicates), retry failed messages with corrected text/institution | **Batches → Review** |
| Select records → **Generate Excel**: new 13-column workbook, or populate a template using a saved/auto column mapping | **Generate Excel** |
| Download behind the predefined password (server-verified, rate-limited, audited) | **Report History** |

### Your `mpesav3.py`
`core/parsing/mpesav3_legacy.py` loads `parse_mpesa_message()` straight from
`MPESAV3_SCRIPT_PATH` by parsing the file and compiling **only that function** – the Tkinter GUI
never starts. If the file is not reachable (e.g. deployed on a server), an identical vendored copy is
used. Its output is the first pass; the extended parser (`core/parsing/extractor.py`) then fills
gaps and validates results (e.g. a "Ref" that isn't a real receipt is discarded, balances are never
mistaken for amounts, KCB `FT…` bank references are not treated as M-Pesa receipts).
Anything that can't be found confidently is left blank.

New SMS wordings can be supported without code changes: **Settings → Custom parsing rules**
(regex with one capture group per field, or a fixed Bank/Channel value).

### Excel output
* New workbook: exact order – User Email, Report Type, Item Name, Quantity, Unit Cost, Amount, Status,
  Reference, MPESA Receipt, Submission Date, Account Number, Bank, Channel; styled header, filters,
  frozen header row, auto column widths, `#,##0.00` money and `dd/mm/yyyy hh:mm AM/PM` dates,
  optional total row. Reference / Receipt / Account are written as text.
* Template: the original is never modified. Rows are appended after the last existing data row;
  a "Total" row is pushed down and ranges such as `=SUM(B2:B11)` are extended. Styles of the last
  data row are copied to new rows. (openpyxl limitation: charts/images in templates are not kept.)
* All text written from SMS/user input is neutralised against formula injection.

### Roles
* **Administrator** – everything, incl. users, settings (download password, defaults, picklists,
  parser rules), audit log, deleting any record.
* **Reviewer** – sees all records, approves/rejects.
* **Standard user** – sees only their own batches, transactions and reports.

## Security notes
* Email login, Django auth, CSRF on every form, session cookies HTTP-only.
* Templates and reports live in `PRIVATE_MEDIA_ROOT`, outside any public URL; served only by
  permission-checked views.
* Uploaded `.xlsx` checks: real ZIP/Office structure, no macros, no external links, no embedded
  binaries, zip-bomb limits; parsed with `defusedxml`.
* Download password stored as a salted PBKDF2 hash; checked server-side only; 5 wrong attempts per
  user/IP → 15-minute lock (configurable); every attempt in the audit log.
* Production settings (secure cookies, HSTS, proxy headers) switch on automatically when
  `DJANGO_DEBUG=False`; `deploy/install.sh` configures everything.

## Maintenance
On the Linux server backups (mysqldump + files) and retention run automatically via systemd timers –
see deploy/DEPLOY.md. Manual equivalents:
```bash
python manage.py backup_db --keep 14      # DB-agnostic gzip JSON dump to BACKUP_DIR
python manage.py purge_old_data --dry-run # apply RETENTION_DAYS_* policy
```

## Project layout
```
accounts/            custom email User + roles, auth URLs, user forms
core/models.py       SMSMessage, Transaction, ExtractionBatch, ExcelTemplate, ColumnMapping,
                     GeneratedReport, AuditLog, AppSetting, Option, ParserRule
core/parsing/        mpesav3 integration + M-Pesa/KCB extractor + bulk splitter
core/services/       ingest, validation & duplicates, excel generation, upload safety, audit, access
core/views/          dashboard, extraction, transactions, reports, templates, admin
templates/ static/   glassmorphism UI (Chart.js bundled locally – no CDN needed)
legacy/mpesav3.py    your original script (its parse_mpesa_message() is reused)
deploy/              Linux deployment kit (install/update scripts, systemd, nginx, backups)
```

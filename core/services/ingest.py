"""Reading SMS input (paste / TXT / CSV / XLSX) and running extraction batches."""
import csv
import hashlib
import io
from decimal import Decimal

from django.conf import settings
from django.db import transaction as db_transaction
from django.utils import timezone

from core.models import ExtractionBatch, ParserRule, SMSMessage, Transaction
from core.parsing.extractor import extract, normalize, split_messages, to_decimal

from . import validation
from .excel_safety import UnsafeWorkbook, open_safe_workbook

MESSAGE_HEADERS = {"message", "messages", "sms", "body", "text", "content", "msg", "sms text", "sms body", "details"}


class IngestError(Exception):
    pass


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            if enc == "utf-16" and "\x00" in text:
                continue
            return text
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise IngestError("Could not decode the file. Please save it as UTF-8 text.")


def _pick_column(rows):
    """Return index of the SMS column: by header name, else the longest-text column."""
    if not rows:
        return 0, rows
    header = [str(c or "").strip().lower() for c in rows[0]]
    for i, h in enumerate(header):
        if h in MESSAGE_HEADERS:
            return i, rows[1:]
    width = max(len(r) for r in rows)
    lengths = [0] * width
    for r in rows[:200]:
        for i, c in enumerate(r):
            lengths[i] += len(str(c or ""))
    return max(range(width), key=lambda i: lengths[i]), rows


def _cells_to_messages(rows):
    idx, body = _pick_column(rows)
    messages = []
    for r in body:
        if idx < len(r) and r[idx] not in (None, ""):
            messages.extend(split_messages(str(r[idx])))
    return messages


def read_upload(upload):
    """Return (messages, method) from an uploaded file. Validates type and size."""
    name = (upload.name or "").lower()
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if upload.size > max_bytes:
        raise IngestError(f"File is larger than {settings.MAX_UPLOAD_MB} MB.")
    raw = upload.read()
    if name.endswith(".txt"):
        return split_messages(_decode(raw)), ExtractionBatch.Method.TXT
    if name.endswith(".csv"):
        text = _decode(raw)
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect))
        return _cells_to_messages(rows), ExtractionBatch.Method.CSV
    if name.endswith(".xlsx"):
        try:
            wb = open_safe_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except UnsafeWorkbook as exc:
            raise IngestError(str(exc))
        try:
            ws = wb.worksheets[0]
            rows = [list(r) for _, r in zip(range(20000), ws.iter_rows(values_only=True))]
        finally:
            wb.close()
        return _cells_to_messages(rows), ExtractionBatch.Method.XLSX
    raise IngestError("Unsupported file type. Upload a .txt, .csv or .xlsx file.")


# ------------------------------------------------------------------ batch processing
def _parse_defaults(defaults: dict) -> dict:
    out = dict(defaults or {})
    out["quantity"] = to_decimal(out.get("quantity")) if out.get("quantity") not in (None, "") else None
    out["unit_cost"] = to_decimal(out.get("unit_cost")) if out.get("unit_cost") not in (None, "") else None
    return out


def _apply_extraction(txn: Transaction, result, defaults: dict, user):
    d = _parse_defaults(defaults)
    txn.user_email = user.email
    txn.report_type = d.get("report_type") or ""
    txn.item_name = d.get("item_name") or ""
    txn.quantity = d.get("quantity")
    txn.unit_cost = d.get("unit_cost")
    txn.status = d.get("status") or ""
    txn.source = result.source
    txn.extracted_amount = result.amount
    txn.amount = result.amount if result.amount is not None else txn.calculated_amount
    txn.reference = result.reference
    txn.mpesa_receipt = result.mpesa_receipt
    txn.account_number = result.account_number
    txn.bank = result.bank
    txn.channel = result.channel
    txn.direction = result.direction
    txn.counterparty = result.counterparty[:200]
    txn.transaction_date = result.transaction_date
    sub = d.get("submission_date")
    if sub:
        txn.submission_date = sub if not isinstance(sub, str) else _parse_iso(sub)
    else:
        txn.submission_date = result.transaction_date or timezone.now()


def _parse_iso(value):
    from django.utils.dateparse import parse_datetime

    dt = parse_datetime(value)
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def _active_rules():
    return list(ParserRule.objects.filter(is_active=True))


def process_message(batch, user, text, position, source_hint, defaults, rules):
    clean = normalize(text)
    result = extract(clean, source_hint, rules)
    sms = SMSMessage.objects.create(
        batch=batch, created_by=user, raw_text=text.strip(), position=position,
        text_hash=hashlib.sha256(clean.encode()).hexdigest(),
        detected_source=result.source if result.source else SMSMessage.Source.UNKNOWN,
        selected_source=source_hint,
        parse_status=SMSMessage.ParseStatus.PARSED if result.recognized else (
            SMSMessage.ParseStatus.UNRECOGNIZED if not result.errors or "Unrecognized" in result.errors[0]
            else SMSMessage.ParseStatus.FAILED),
        error="; ".join(result.errors),
    )
    if not result.recognized:
        return sms, None
    txn = Transaction(sms=sms, batch=batch, created_by=user, workflow=Transaction.Workflow.EXTRACTED)
    _apply_extraction(txn, result, defaults, user)
    txn.save()
    validation.refresh(txn, result.warnings)
    return sms, txn


def run_batch(user, messages, *, source_hint="AUTO", defaults=None, method="paste", filename=""):
    if not messages:
        raise IngestError("No SMS messages were found in the input.")
    if len(messages) > settings.MAX_MESSAGES_PER_BATCH:
        raise IngestError(f"Too many messages ({len(messages)}). The limit per batch is {settings.MAX_MESSAGES_PER_BATCH}.")
    defaults = dict(defaults or {})
    if defaults.get("submission_date") and not isinstance(defaults["submission_date"], str):
        defaults["submission_date"] = defaults["submission_date"].isoformat()
    for k in ("quantity", "unit_cost"):
        if isinstance(defaults.get(k), Decimal):
            defaults[k] = str(defaults[k])
    rules = _active_rules()
    with db_transaction.atomic():
        batch = ExtractionBatch.objects.create(
            created_by=user, input_method=method, source_hint=source_hint, filename=filename[:255], defaults=defaults,
        )
        for pos, text in enumerate(messages, start=1):
            process_message(batch, user, text, pos, source_hint, defaults, rules)
        batch.refresh_counts()
    return batch


def retry_message(sms: SMSMessage, user, *, new_text=None, source_hint=None):
    """Re-run extraction for a failed (or any) SMS, optionally with corrected text/source."""
    rules = _active_rules()
    text = new_text if new_text is not None else sms.raw_text
    hint = source_hint or sms.selected_source
    result = extract(normalize(text), hint, rules)
    sms.raw_text = text.strip()
    sms.text_hash = hashlib.sha256(normalize(text).encode()).hexdigest()
    sms.selected_source = hint
    sms.detected_source = result.source
    sms.attempts += 1
    sms.error = "; ".join(result.errors)
    sms.parse_status = SMSMessage.ParseStatus.PARSED if result.recognized else SMSMessage.ParseStatus.UNRECOGNIZED
    sms.save()
    txn = getattr(sms, "transaction", None)
    if result.recognized:
        if txn is None:
            txn = Transaction(sms=sms, batch=sms.batch, created_by=sms.created_by, workflow=Transaction.Workflow.EXTRACTED)
        _apply_extraction(txn, result, sms.batch.defaults if sms.batch else {}, sms.created_by)
        txn.updated_by = user
        if txn.workflow == Transaction.Workflow.FAILED:
            txn.workflow = Transaction.Workflow.EXTRACTED
        txn.save()
        validation.refresh(txn, result.warnings)
    if sms.batch:
        sms.batch.refresh_counts()
    return sms, txn

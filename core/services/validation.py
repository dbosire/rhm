"""Validation, discrepancy and duplicate detection for transactions."""
import hashlib
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from core.models import Transaction

TOLERANCE = Decimal("0.01")


def issue(level, field, message):
    return {"level": level, "field": field, "message": message}


def validate(txn: Transaction, extraction_warnings=None) -> list:
    issues = []
    try:
        validate_email(txn.user_email or "")
    except ValidationError:
        issues.append(issue("error", "user_email", "User Email is not a valid email address."))

    if txn.quantity is not None and txn.quantity < 0:
        issues.append(issue("error", "quantity", "Quantity cannot be negative."))
    if txn.unit_cost is not None and txn.unit_cost < 0:
        issues.append(issue("error", "unit_cost", "Unit Cost cannot be negative."))

    if txn.amount is None:
        issues.append(issue("error", "amount", "Amount is missing (not in SMS and Quantity × Unit Cost not provided)."))
    elif txn.amount <= 0:
        issues.append(issue("error", "amount", "Amount must be greater than zero."))

    calc = txn.calculated_amount
    if txn.amount is not None and calc is not None and abs(Decimal(txn.amount) - calc) > TOLERANCE:
        issues.append(issue(
            "warning", "amount",
            f"Amount {txn.amount:,.2f} differs from Quantity × Unit Cost = {calc:,.2f}.",
        ))

    if not txn.submission_date:
        issues.append(issue("error", "submission_date", "Submission Date is missing."))
    elif txn.submission_date > timezone.now() + timedelta(days=1):
        issues.append(issue("warning", "submission_date", "Submission Date is in the future."))

    if not txn.mpesa_receipt and not txn.reference:
        issues.append(issue("warning", "reference", "Neither a Reference nor an MPESA Receipt was found."))
    if not txn.bank:
        issues.append(issue("warning", "bank", "Bank could not be determined."))
    if not txn.channel:
        issues.append(issue("warning", "channel", "Channel could not be determined."))
    if not txn.report_type:
        issues.append(issue("info", "report_type", "Report Type not set."))

    for w in extraction_warnings or []:
        issues.append(issue("warning", "sms", w))
    return issues


_OCR_FOLD = str.maketrans({"O": "0", "I": "1", "L": "1"})


def duplicate_key(txn: Transaction) -> str:
    if txn.mpesa_receipt:
        # Fold characters OCR commonly confuses so a screenshot copy matches the original SMS.
        basis = f"R|{txn.mpesa_receipt.strip().upper().translate(_OCR_FOLD)}"
    else:
        when = timezone.localtime(txn.transaction_date or txn.submission_date).strftime("%Y%m%d%H%M") if (
            txn.transaction_date or txn.submission_date) else ""
        basis = "|".join([
            "C", (txn.reference or "").strip().lower(), f"{txn.amount or ''}", when,
            (txn.bank or "").lower(), (txn.channel or "").lower(),
        ])
    return hashlib.sha256(basis.encode()).hexdigest()


def detect_duplicate(txn: Transaction):
    """Flag txn as a duplicate of the earliest matching record. Never deletes anything."""
    txn.dup_key = duplicate_key(txn)
    original = (
        Transaction.objects.filter(dup_key=txn.dup_key).exclude(pk=txn.pk).order_by("created_at", "id").first()
        if txn.dup_key else None
    )
    if original and (txn.pk is None or (original.created_at, original.id) < (txn.created_at, txn.id)):
        txn.is_duplicate = True
        txn.duplicate_of = original
        if not txn.duplicate_decision:
            txn.duplicate_decision = Transaction.DuplicateDecision.PENDING
    else:
        txn.is_duplicate = False
        txn.duplicate_of = None
        txn.duplicate_decision = ""


def refresh(txn: Transaction, extraction_warnings=None, save=True):
    """Recompute validation issues, duplicate status and review flag."""
    keep_sms_warnings = extraction_warnings
    if keep_sms_warnings is None:
        keep_sms_warnings = [i["message"] for i in (txn.issues or []) if i.get("field") == "sms"]
    txn.issues = validate(txn, keep_sms_warnings)
    detect_duplicate(txn)
    if txn.is_duplicate and txn.duplicate_decision in ("", Transaction.DuplicateDecision.PENDING):
        txn.issues.append(issue("warning", "duplicate", "Possible duplicate of an existing transaction."))
    has_errors = any(i["level"] == "error" for i in txn.issues)
    has_warn = any(i["level"] == "warning" for i in txn.issues)
    txn.needs_review = has_errors or has_warn
    if txn.workflow in (Transaction.Workflow.EXTRACTED, Transaction.Workflow.AWAITING_REVIEW, Transaction.Workflow.PENDING):
        txn.workflow = Transaction.Workflow.AWAITING_REVIEW if txn.needs_review else Transaction.Workflow.EXTRACTED
    if save:
        txn.save()
    return txn

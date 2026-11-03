from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.forms import TransactionForm, combine_date, parse_submission_date
from core.models import Transaction
from core.services import audit, validation
from core.services.access import visible_transactions

from .common import filter_choices, filter_transactions, option_lists, paginate, selected_ids

INLINE_FIELDS = [
    "report_type", "item_name", "quantity", "unit_cost", "amount", "status", "reference", "mpesa_receipt",
    "submission_date", "account_number", "bank", "channel",
]
DECIMAL_FIELDS = {"quantity", "unit_cost", "amount"}
MAX_LEN = {f.name: f.max_length for f in Transaction._meta.fields if getattr(f, "max_length", None)}


def _safe_next(request, fallback):
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return nxt
    return fallback


@login_required
def transaction_list(request):
    base = visible_transactions(request.user)
    qs = filter_transactions(base, request.GET).select_related("duplicate_of")
    sort = request.GET.get("sort", "-created_at")
    allowed = {"created_at", "-created_at", "amount", "-amount", "submission_date", "-submission_date", "bank", "channel"}
    qs = qs.order_by(sort if sort in allowed else "-created_at", "-id")
    page, query = paginate(request, qs)
    return render(request, "core/transaction_list.html", {
        "page": page, "query": query, "filters": request.GET, "sort": sort, **filter_choices(base),
    })


@login_required
def transaction_detail(request, pk):
    txn = get_object_or_404(visible_transactions(request.user), pk=pk)
    if request.method == "POST":
        form = TransactionForm(request.POST, instance=txn, user=request.user)
        if form.is_valid():
            txn = form.save(commit=False)
            if txn.amount is None and txn.calculated_amount is not None:
                txn.amount = txn.calculated_amount
            txn.updated_by = request.user
            validation.refresh(txn)
            audit.log(request, "transaction.edit", txn, fields=",".join(form.changed_data))
            messages.success(request, "Transaction updated.")
            return redirect(_safe_next(request, request.path))
    else:
        form = TransactionForm(instance=txn, user=request.user)
    return render(request, "core/transaction_detail.html", {
        "txn": txn, "form": form, "reports": txn.reports.all()[:10], "back_url": _safe_next(request, ""), **option_lists(),
    })


def _parse_inline(txn, post):
    errors = []
    for field in INLINE_FIELDS:
        key = f"f-{txn.pk}-{field}"
        if key not in post:
            continue
        raw = post.get(key, "").strip()
        if field in DECIMAL_FIELDS:
            if raw == "":
                setattr(txn, field, None)
                continue
            try:
                val = Decimal(raw.replace(",", "")).quantize(Decimal("0.01"))
                if val < 0:
                    raise InvalidOperation
                setattr(txn, field, val)
            except (InvalidOperation, ValueError):
                errors.append(f"#{txn.pk}: invalid {field.replace('_', ' ')} '{raw}'")
        elif field == "submission_date":
            if raw == "":
                setattr(txn, field, None)
                continue
            d = parse_submission_date(raw)
            if d is None:
                errors.append(f"#{txn.pk}: invalid submission date '{raw}' (use MM/DD/YY)")
            else:
                setattr(txn, field, combine_date(d, txn.submission_date))
        else:
            setattr(txn, field, raw[: MAX_LEN.get(field, 255)])
    if txn.amount is None and txn.calculated_amount is not None:
        txn.amount = txn.calculated_amount
    return errors


@login_required
@require_POST
def transaction_bulk(request):
    user = request.user
    action = request.POST.get("action", "save")
    fallback = request.META.get("HTTP_REFERER") or "core:transaction_list"
    next_url = _safe_next(request, "/transactions/")
    qs = visible_transactions(user)

    # Inline edits for every row on the page (submitted with any action)
    row_ids = selected_ids(request, "row_ids")
    edited, errors = 0, []
    if row_ids and action in ("save", "generate", "approve"):
        for txn in qs.filter(pk__in=row_ids):
            before = {f: getattr(txn, f) for f in INLINE_FIELDS}
            errs = _parse_inline(txn, request.POST)
            if errs:
                errors.extend(errs)
                continue
            if any(before[f] != getattr(txn, f) for f in INLINE_FIELDS):
                txn.updated_by = user
                validation.refresh(txn)
                edited += 1
        if edited:
            audit.log(request, "transaction.bulk_edit", None, count=edited)
    for e in errors[:10]:
        messages.error(request, e)

    ids = selected_ids(request)
    selected = qs.filter(pk__in=ids)
    n = selected.count()

    if action == "save":
        messages.success(request, f"Saved changes to {edited} record(s).") if edited else messages.info(request, "No changes to save.")
    elif not n:
        messages.warning(request, "Select at least one transaction first.")
    elif action == "generate":
        request.session["generate_ids"] = list(selected.values_list("pk", flat=True))
        return redirect("core:generate")
    elif action in ("keep_dup", "skip_dup"):
        decision = Transaction.DuplicateDecision.KEEP if action == "keep_dup" else Transaction.DuplicateDecision.SKIP
        count = 0
        for txn in selected.filter(is_duplicate=True):
            txn.duplicate_decision = decision
            txn.updated_by = user
            validation.refresh(txn)
            count += 1
        audit.log(request, f"duplicate.{decision}", None, count=count)
        messages.success(request, f"{count} duplicate(s) marked as '{'retain' if decision == 'keep' else 'skip'}'.")
    elif action in ("approve", "reject"):
        if not user.can_review:
            messages.error(request, "Only reviewers or administrators can approve or reject records.")
        else:
            wf = Transaction.Workflow.APPROVED if action == "approve" else Transaction.Workflow.REJECTED
            selected.update(workflow=wf, updated_by=user, updated_at=timezone.now())
            audit.log(request, f"transaction.{action}", None, count=n)
            messages.success(request, f"{n} record(s) {wf.label.lower()}.")
    elif action == "set_status":
        value = request.POST.get("status_value", "").strip()[:40]
        selected.update(status=value, updated_by=user, updated_at=timezone.now())
        audit.log(request, "transaction.set_status", None, count=n, status=value)
        messages.success(request, f"Status set to '{value}' on {n} record(s).")
    elif action == "set_report_type":
        value = request.POST.get("report_type_value", "").strip()[:80]
        selected.update(report_type=value, updated_by=user, updated_at=timezone.now())
        for txn in selected:
            validation.refresh(txn)
        messages.success(request, f"Report Type set to '{value}' on {n} record(s).")
    elif action == "delete":
        if user.can_delete_records:
            deletable = selected
        else:
            deletable = selected.filter(created_by=user).exclude(
                workflow__in=[Transaction.Workflow.APPROVED, Transaction.Workflow.EXPORTED]
            )
        count = deletable.count()
        if count:
            ref_list = ",".join(str(i) for i in deletable.values_list("pk", flat=True)[:200])
            batches = set(deletable.exclude(batch=None).values_list("batch_id", flat=True))
            deletable.delete()
            audit.log(request, "transaction.delete", None, count=count, ids=ref_list)
            from core.models import ExtractionBatch

            for b in ExtractionBatch.objects.filter(pk__in=batches):
                b.refresh_counts()
        skipped = n - count
        messages.success(request, f"Removed {count} record(s).")
        if skipped:
            messages.warning(request, f"{skipped} record(s) were not removed (approved/exported records can only be deleted by an administrator).")
    else:
        messages.error(request, "Unknown action.")
    return redirect(next_url)

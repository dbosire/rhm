from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.forms import ExtractForm
from core.models import AppSetting, ExtractionBatch, SMSMessage, Transaction
from core.parsing.extractor import split_messages
from core.services import audit, ingest
from core.services.access import visible_batches

from .common import option_lists, paginate


@login_required
def extract(request):
    setting = AppSetting.load()
    if request.method == "POST":
        form = ExtractForm(request.POST, request.FILES)
        if form.is_valid():
            cd = form.cleaned_data
            msgs, method, filename = [], ExtractionBatch.Method.PASTE, ""
            try:
                if cd.get("sms_text", "").strip():
                    msgs.extend(split_messages(cd["sms_text"]))
                if cd.get("upload"):
                    file_msgs, method = ingest.read_upload(cd["upload"])
                    filename = cd["upload"].name
                    msgs.extend(file_msgs)
                defaults = {
                    "report_type": cd.get("report_type") or "",
                    "item_name": cd.get("item_name") or "",
                    "quantity": str(cd["quantity"]) if cd.get("quantity") is not None else "",
                    "unit_cost": str(cd["unit_cost"]) if cd.get("unit_cost") is not None else "",
                    "status": cd.get("status") or setting.default_status,
                    "submission_date": cd["submission_date"].isoformat() if cd.get("submission_date") else "",
                }
                batch = ingest.run_batch(
                    request.user, msgs, source_hint=cd["source"], defaults=defaults, method=method, filename=filename,
                )
            except ingest.IngestError as exc:
                form.add_error(None, str(exc))
            else:
                audit.log(request, "batch.extract", batch, submitted=batch.submitted_count,
                          extracted=batch.extracted_count, failed=batch.failed_count, duplicates=batch.duplicate_count)
                lvl = messages.SUCCESS if not batch.failed_count else messages.WARNING
                messages.add_message(
                    request, lvl,
                    f"Processed {batch.submitted_count} message(s): {batch.extracted_count} extracted, "
                    f"{batch.failed_count} failed, {batch.duplicate_count} possible duplicate(s).",
                )
                return redirect("core:batch_detail", uid=batch.uid)
    else:
        form = ExtractForm(initial={"status": setting.default_status, "report_type": setting.default_report_type})
    return render(request, "core/extract.html", {"form": form, **option_lists()})


@login_required
def batch_list(request):
    qs = visible_batches(request.user)
    state = request.GET.get("state")
    if state:
        qs = qs.filter(state=state)
    page, query = paginate(request, qs)
    return render(request, "core/batch_list.html", {"page": page, "query": query, "states": ExtractionBatch.State.choices, "state": state})


@login_required
def batch_detail(request, uid):
    batch = get_object_or_404(visible_batches(request.user), uid=uid)
    txns = batch.transactions.select_related("sms", "duplicate_of").order_by("sms__position", "id")
    show = request.GET.get("show", "all")
    if show == "review":
        txns = txns.filter(needs_review=True)
    elif show == "duplicates":
        txns = txns.filter(is_duplicate=True)
    failed = batch.messages.exclude(parse_status=SMSMessage.ParseStatus.PARSED).order_by("position")
    summary = batch.transactions.aggregate(
        errors=Count("id", filter=Q(needs_review=True)),
        dups=Count("id", filter=Q(is_duplicate=True, duplicate_decision="pending")),
    )
    return render(request, "core/batch_detail.html", {
        "batch": batch, "txns": txns, "failed": failed, "show": show, "summary": summary,
        "workflow_choices": Transaction.Workflow.choices, **option_lists(),
    })


@login_required
@require_POST
def batch_retry(request, uid):
    batch = get_object_or_404(visible_batches(request.user), uid=uid)
    source = request.POST.get("source") or None
    if source not in (None, "AUTO", "MPESA", "KCB"):
        source = None
    n_ok = 0
    failed = list(batch.messages.exclude(parse_status=SMSMessage.ParseStatus.PARSED))
    for sms in failed:
        sms, txn = ingest.retry_message(sms, request.user, source_hint=source)
        n_ok += txn is not None
    audit.log(request, "batch.retry", batch, retried=len(failed), recovered=n_ok)
    messages.info(request, f"Retried {len(failed)} failed message(s); {n_ok} now extracted.")
    return redirect("core:batch_detail", uid=batch.uid)


@login_required
@require_POST
def sms_retry(request, pk):
    qs = SMSMessage.objects.select_related("batch")
    if not request.user.can_view_all:
        qs = qs.filter(created_by=request.user)
    sms = get_object_or_404(qs, pk=pk)
    text = request.POST.get("raw_text")
    source = request.POST.get("source") or "AUTO"
    if source not in ("AUTO", "MPESA", "KCB"):
        source = "AUTO"
    sms, txn = ingest.retry_message(sms, request.user, new_text=text if text else None, source_hint=source)
    audit.log(request, "sms.retry", sms, success=txn is not None)
    if txn:
        messages.success(request, "Message extracted successfully.")
    else:
        messages.error(request, f"Still could not extract: {sms.error}")
    return redirect("core:batch_detail", uid=sms.batch.uid)

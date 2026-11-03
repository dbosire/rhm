import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.forms import ExtractForm
from core.models import AppSetting, ExtractionBatch, SMSMessage, Transaction
from core.parsing.extractor import normalize, split_messages
from core.services import audit, ingest, ocr
from core.services.access import visible_batches

from .common import option_lists, paginate


@login_required
def extract(request):
    setting = AppSetting.load()
    if request.method == "POST":
        form = ExtractForm(request.POST, request.FILES)
        if form.is_valid():
            cd = form.cleaned_data
            msgs, methods, names = [], set(), []
            try:
                if cd.get("sms_text", "").strip():
                    # Messages that came from an on-page OCR preview keep their OCR warning.
                    try:
                        ocr_set = {normalize(t) for t in json.loads(cd.get("ocr_texts") or "[]") if isinstance(t, str)}
                    except ValueError:
                        ocr_set = set()
                    for m in split_messages(cd["sms_text"]):
                        msgs.append((m, [ocr.OCR_WARNING]) if normalize(m) in ocr_set else m)
                    methods.add(ExtractionBatch.Method.IMAGE if ocr_set and all(
                        normalize(m) in ocr_set for m in split_messages(cd["sms_text"])) else ExtractionBatch.Method.PASTE)
                if cd.get("upload"):
                    file_msgs, file_method = ingest.read_upload(cd["upload"])
                    names.append(cd["upload"].name)
                    methods.add(file_method)
                    msgs.extend(file_msgs)
                if cd.get("images"):
                    img_msgs, img_errors = ingest.read_images(cd["images"])
                    for e in img_errors:
                        messages.warning(request, e)
                    if not img_msgs and not msgs:
                        raise ingest.IngestError("No SMS text could be read from the image(s).")
                    names.extend(f.name for f in cd["images"])
                    methods.add(ExtractionBatch.Method.IMAGE)
                    msgs.extend(img_msgs)
                method = methods.pop() if len(methods) == 1 else ExtractionBatch.Method.MIXED
                filename = ", ".join(names)
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
    ocr_ok, ocr_reason = ocr.availability()
    return render(request, "core/extract.html", {
        "form": form, "ocr_available": ocr_ok, "ocr_reason": ocr_reason, **option_lists(),
    })


@login_required
@require_POST
def ocr_preview(request):
    """Read SMS text from pasted/uploaded images and return it for review (nothing is stored)."""
    ok, reason = ocr.availability()
    if not ok:
        return JsonResponse({"ok": False, "error": f"Image extraction is not available: {reason}"}, status=503)
    files = request.FILES.getlist("images")
    if not files:
        return JsonResponse({"ok": False, "error": "No image received."}, status=400)
    if len(files) > settings.MAX_IMAGES_PER_UPLOAD:
        return JsonResponse({"ok": False, "error": f"At most {settings.MAX_IMAGES_PER_UPLOAD} images at a time."}, status=400)
    results = []
    for f in files:
        item = {"name": f.name[:120]}
        if not ocr.is_image_name(f.name) and not (f.content_type or "").startswith("image/"):
            item["error"] = "Not a supported image type."
        elif f.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
            item["error"] = f"Larger than {settings.MAX_UPLOAD_MB} MB."
        else:
            try:
                text = ocr.ocr_image(f.read())
                found = split_messages(text)
                item.update(text="\n\n".join(found), messages=found, count=len(found))
            except ocr.OCRError as exc:
                item["error"] = str(exc)
        results.append(item)
    audit.log(request, "ocr.preview", None, images=len(files), messages=sum(r.get("count", 0) for r in results))
    return JsonResponse({"ok": True, "results": results})


@login_required
def batch_list(request):
    qs = visible_batches(request.user).select_related("merged_into")
    state = request.GET.get("state")
    if state:
        qs = qs.filter(state=state)
    else:
        qs = qs.exclude(state=ExtractionBatch.State.MERGED)
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
    sources = batch.merged_sources.order_by("created_at")
    return render(request, "core/batch_detail.html", {
        "batch": batch, "sources": sources, "txns": txns, "failed": failed, "show": show, "summary": summary,
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


@login_required
@require_POST
def batch_merge(request):
    """Merge the selected batches into one; optionally continue straight to Excel generation."""
    from core.services.batches import MergeError, merge_batches

    uids = request.POST.getlist("uids")
    batches = list(visible_batches(request.user).filter(uid__in=uids))
    if len(batches) != len(set(uids)):
        messages.error(request, "Some selected batches were not found or are not accessible to you.")
        return redirect("core:batch_list")
    if len({b.created_by_id for b in batches}) > 1 and not request.user.is_admin_role:
        messages.error(request, "Only administrators can merge batches that belong to different users.")
        return redirect("core:batch_list")
    try:
        merged = merge_batches(request.user, batches)
    except MergeError as exc:
        messages.error(request, str(exc))
        return redirect("core:batch_list")
    audit.log(request, "batch.merge", merged, sources=",".join(b.short_uid for b in batches),
              messages=merged.submitted_count, transactions=merged.extracted_count)
    messages.success(
        request,
        f"Merged {len(batches)} batches into batch {merged.short_uid}: {merged.submitted_count} message(s), "
        f"{merged.extracted_count} extracted, {merged.duplicate_count} possible duplicate(s).",
    )
    if request.POST.get("then") == "generate":
        return redirect("core:batch_generate", uid=merged.uid)
    return redirect("core:batch_detail", uid=merged.uid)

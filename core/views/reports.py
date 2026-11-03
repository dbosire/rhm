import io

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction as db_transaction
from django.db.models import Q, Sum
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from core.forms import GenerateForm
from core.models import AppSetting, GeneratedReport, Transaction
from core.services import audit, excel
from core.services.access import (
    visible_batches, visible_mappings, visible_reports, visible_templates, visible_transactions,
)
from core.services.excel_safety import UnsafeWorkbook

from .common import paginate


def _selection(request):
    return visible_transactions(request.user).filter(pk__in=request.session.get("generate_ids", [])).order_by(
        "submission_date", "id"
    )


@login_required
def generate_from_batch(request, uid):
    batch = get_object_or_404(visible_batches(request.user), uid=uid)
    ids = list(
        batch.transactions.exclude(duplicate_decision=Transaction.DuplicateDecision.SKIP)
        .exclude(workflow=Transaction.Workflow.REJECTED).values_list("pk", flat=True)
    )
    request.session["generate_ids"] = ids
    request.session["generate_batch"] = batch.pk
    return redirect("core:generate")


@login_required
def generate(request):
    setting = AppSetting.load()
    txns = _selection(request)
    excluded = txns.filter(Q(duplicate_decision=Transaction.DuplicateDecision.SKIP) | Q(workflow=Transaction.Workflow.REJECTED))
    eligible = txns.exclude(pk__in=excluded.values("pk"))
    with_errors = [t for t in eligible if t.error_count]
    pending_dups = eligible.filter(is_duplicate=True, duplicate_decision=Transaction.DuplicateDecision.PENDING)
    warnings = [t for t in eligible if t.warning_count]
    templates = visible_templates(request.user)
    mappings = visible_mappings(request.user)

    if request.method == "POST":
        form = GenerateForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            records = list(eligible)
            if not records:
                form.add_error(None, "There are no eligible transactions to export.")
            elif (with_errors or pending_dups.exists()) and not cd.get("acknowledge"):
                form.add_error("acknowledge", "Some records have validation errors or unresolved duplicates. Tick the box to generate anyway.")
            else:
                try:
                    report = _build_report(request, cd, records, templates, mappings, setting)
                except (ValueError, UnsafeWorkbook) as exc:
                    form.add_error(None, f"Could not generate the workbook: {exc}")
                else:
                    request.session.pop("generate_ids", None)
                    request.session.pop("generate_batch", None)
                    messages.success(request, f"Report '{report.name}' generated with {report.record_count} record(s).")
                    return redirect("core:report_detail", uid=report.uid)
    else:
        default_name = f"Extracted Transactions {timezone.localtime():%Y-%m-%d %H%M}"
        form = GenerateForm(initial={
            "report_name": default_name, "sheet_name": setting.default_sheet_name,
            "include_total": setting.include_total_row, "mode": request.GET.get("mode", "new"),
        })
    return render(request, "core/generate.html", {
        "form": form, "txns": txns, "eligible_count": eligible.count(), "excluded_count": excluded.count(),
        "with_errors": with_errors, "warnings": warnings, "pending_dups": pending_dups,
        "templates": templates, "mappings": mappings,
        "total": eligible.aggregate(s=Sum("amount"))["s"] or 0,
    })


def _build_report(request, cd, records, templates, mappings, setting):
    user = request.user
    report_types = {r.report_type for r in records if r.report_type}
    batch_id = request.session.get("generate_batch")
    report = GeneratedReport(
        name=cd["report_name"].strip()[:200], created_by=user, mode=cd["mode"],
        record_count=len(records), total_amount=sum((r.amount or 0) for r in records),
        report_type=report_types.pop() if len(report_types) == 1 else ("Mixed" if report_types else ""),
        batch=visible_batches(user).filter(pk=batch_id).first() if batch_id else None,
    )
    if cd["mode"] == "template":
        tpl = templates.filter(pk=cd.get("template")).first()
        if not tpl:
            raise ValueError("Select an Excel template.")
        mapping_obj = mappings.filter(pk=cd.get("mapping"), template=tpl).first() if cd.get("mapping") else None
        if mapping_obj:
            sheet, header_row, data_row, mapping = (
                mapping_obj.sheet_name, mapping_obj.header_row, mapping_obj.data_start_row, mapping_obj.mapping)
        else:
            wb = excel.load_template(tpl.file, read_only=False)
            sheet = cd.get("template_sheet") or wb.sheetnames[0]
            if sheet not in wb.sheetnames:
                raise ValueError("Selected worksheet not found.")
            ws = wb[sheet]
            header_row = cd.get("header_row") or excel.guess_header_row(ws)
            data_row = cd.get("data_start_row") or header_row + 1
            mapping = excel.auto_map(excel.read_headers(ws, header_row))
            if not mapping:
                raise ValueError("No template headers matched the standard parameters. Create a column mapping first.")
        data, _ = excel.populate_template(tpl.file, sheet, header_row, data_row, mapping, records)
        report.template, report.mapping, report.sheet_name = tpl, mapping_obj, sheet
    else:
        report.sheet_name = cd["sheet_name"]
        data = excel.build_new_workbook(records, cd["sheet_name"], cd.get("include_total"))

    with db_transaction.atomic():
        report.file.save("report.xlsx", excel.to_contentfile(data), save=False)
        report.save()
        report.transactions.set(records)
        Transaction.objects.filter(pk__in=[r.pk for r in records]).exclude(
            workflow=Transaction.Workflow.REJECTED
        ).update(workflow=Transaction.Workflow.EXPORTED, updated_at=timezone.now())
    audit.log(request, "report.generate", report, mode=report.mode, records=report.record_count)
    return report


@login_required
def report_list(request):
    qs = visible_reports(request.user)
    rt = request.GET.get("report_type")
    state = request.GET.get("state")
    d_from, d_to = parse_date(request.GET.get("date_from") or ""), parse_date(request.GET.get("date_to") or "")
    if rt:
        qs = qs.filter(report_type=rt)
    if state:
        qs = qs.filter(state=state)
    if d_from:
        qs = qs.filter(created_at__date__gte=d_from)
    if d_to:
        qs = qs.filter(created_at__date__lte=d_to)
    page, query = paginate(request, qs)
    report_types = [r for r in visible_reports(request.user).order_by().values_list("report_type", flat=True).distinct() if r]
    return render(request, "core/report_list.html", {
        "page": page, "query": query, "filters": request.GET, "report_types": sorted(set(report_types)),
        "states": GeneratedReport.State.choices,
    })


@login_required
def report_detail(request, uid):
    report = get_object_or_404(visible_reports(request.user), uid=uid)
    return render(request, "core/report_detail.html", {
        "report": report, "txns": report.transactions.order_by("submission_date")[:200],
    })


def _lock_keys(request):
    ip = audit.client_ip(request) or "unknown"
    return f"dlfail:user:{request.user.pk}", f"dlfail:ip:{ip}"


@login_required
@require_POST
def report_download(request, uid):
    """Validate the predefined download password server-side, then stream the file."""
    report = get_object_or_404(visible_reports(request.user), uid=uid)
    max_attempts = settings.DOWNLOAD_MAX_ATTEMPTS
    window = settings.DOWNLOAD_LOCKOUT_SECONDS
    keys = _lock_keys(request)
    attempts = max(cache.get(k, 0) for k in keys)
    if attempts >= max_attempts:
        audit.log(request, "report.download.locked", report, success=False)
        return JsonResponse(
            {"ok": False, "error": f"Too many incorrect attempts. Try again in {window // 60} minutes."}, status=429)

    setting = AppSetting.load()
    if not setting.has_download_password:
        audit.log(request, "report.download.denied", report, success=False, reason="password not configured")
        return JsonResponse({"ok": False, "error": "A download password has not been configured. Ask an administrator."}, status=403)

    password = request.POST.get("password", "")
    if not setting.check_download_password(password):
        for k in keys:
            cache.set(k, cache.get(k, 0) + 1, window)
        remaining = max(max_attempts - attempts - 1, 0)
        audit.log(request, "report.download.denied", report, success=False, remaining=remaining)
        return JsonResponse({"ok": False, "error": f"Incorrect password. {remaining} attempt(s) remaining."}, status=403)

    for k in keys:
        cache.delete(k)
    if not report.file or report.state != GeneratedReport.State.GENERATED:
        return JsonResponse({"ok": False, "error": "The report file is no longer available."}, status=410)
    try:
        report.file.open("rb")
        data = report.file.read()
        report.file.close()
    except FileNotFoundError:
        return JsonResponse({"ok": False, "error": "The report file is missing on the server."}, status=410)
    GeneratedReport.objects.filter(pk=report.pk).update(download_count=report.download_count + 1)
    audit.log(request, "report.download", report, success=True)
    resp = FileResponse(io.BytesIO(data), as_attachment=True, filename=report.filename,
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Cache-Control"] = "no-store"
    return resp

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db import connection
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.utils import timezone

from core.models import SMSMessage, Transaction
from core.services.access import visible_batches, visible_reports, visible_transactions

from .common import filter_choices, filter_transactions


@login_required
def dashboard(request):
    user = request.user
    base = visible_transactions(user)
    txns = filter_transactions(base, request.GET)

    sms = SMSMessage.objects.all() if user.can_view_all else SMSMessage.objects.filter(created_by=user)
    if request.GET.get("date_from") or request.GET.get("date_to"):
        sms = sms.filter(transaction__in=txns) | sms.filter(parse_status__in=["failed", "unrecognized"])
    sms_total = sms.count()
    sms_ok = sms.filter(parse_status=SMSMessage.ParseStatus.PARSED).count()

    reports = visible_reports(user)
    totals = txns.aggregate(total=Sum("amount"))
    stats = {
        "sms_total": sms_total,
        "sms_ok": sms_ok,
        "sms_failed": sms_total - sms_ok,
        "mpesa": txns.filter(source="MPESA").count(),
        "kcb": txns.filter(source="KCB").count(),
        "duplicates": txns.filter(is_duplicate=True).count(),
        "review": txns.filter(needs_review=True).count(),
        "reports": reports.count(),
        "total_amount": totals["total"] or 0,
        "txn_count": txns.count(),
    }

    since = timezone.now() - timedelta(days=30)
    daily = (
        txns.filter(submission_date__gte=since)
        .annotate(day=TruncDate("submission_date"))
        .values("day").annotate(total=Sum("amount"), n=Count("id")).order_by("day")
    )
    by_bank = txns.values("bank").annotate(n=Count("id"), total=Sum("amount")).order_by("-total")[:8]
    by_channel = txns.values("channel").annotate(n=Count("id"), total=Sum("amount")).order_by("-n")[:8]
    by_workflow = txns.values("workflow").annotate(n=Count("id")).order_by("-n")
    wf_labels = dict(Transaction.Workflow.choices)

    chart_data = {
        "daily": {"labels": [d["day"].strftime("%d %b") for d in daily], "values": [float(d["total"] or 0) for d in daily]},
        "bank": {"labels": [b["bank"] or "Unknown" for b in by_bank], "values": [float(b["total"] or 0) for b in by_bank]},
        "channel": {"labels": [c["channel"] or "Unknown" for c in by_channel], "values": [c["n"] for c in by_channel]},
        "workflow": {"labels": [wf_labels.get(w["workflow"], w["workflow"]) for w in by_workflow], "values": [w["n"] for w in by_workflow]},
    }
    return render(request, "core/dashboard.html", {
        "stats": stats,
        "chart_data": chart_data,
        "recent": txns.order_by("-created_at")[:8],
        "recent_batches": visible_batches(user)[:5],
        "recent_reports": reports[:5],
        "filters": request.GET,
        **filter_choices(base),
    })


def healthz(request):
    """Unauthenticated liveness/readiness probe (no data exposed)."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception:
        return JsonResponse({"status": "db_error"}, status=503)

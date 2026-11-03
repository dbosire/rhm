from datetime import datetime, time

from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.models import Option, Transaction


def paginate(request, qs, per_page=25):
    try:
        per_page = min(max(int(request.GET.get("per_page", per_page)), 10), 200)
    except ValueError:
        pass
    page = Paginator(qs, per_page).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return page, params.urlencode()


def _aware(d, end=False):
    if not d:
        return None
    dt = datetime.combine(d, time.max if end else time.min)
    return timezone.make_aware(dt)


def filter_transactions(qs, params):
    """Shared search + filter used by the transaction list, dashboard and exports."""
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(reference__icontains=q) | Q(mpesa_receipt__icontains=q) | Q(account_number__icontains=q)
            | Q(item_name__icontains=q)
        )
    for field in ("bank", "channel", "status", "report_type", "workflow", "source"):
        val = (params.get(field) or "").strip()
        if val:
            qs = qs.filter(**{field: val})
    d_from = parse_date(params.get("date_from") or "")
    d_to = parse_date(params.get("date_to") or "")
    if d_from:
        qs = qs.filter(submission_date__gte=_aware(d_from))
    if d_to:
        qs = qs.filter(submission_date__lte=_aware(d_to, end=True))
    flag = params.get("flag")
    if flag == "review":
        qs = qs.filter(needs_review=True)
    elif flag == "duplicates":
        qs = qs.filter(is_duplicate=True)
    elif flag == "pending_duplicates":
        qs = qs.filter(is_duplicate=True, duplicate_decision=Transaction.DuplicateDecision.PENDING)
    return qs


def filter_choices(base_qs):
    def distinct(field):
        return [v for v in base_qs.order_by(field).values_list(field, flat=True).distinct() if v]

    statuses = sorted(set(Option.values(Option.Kind.STATUS)) | set(distinct("status")))
    report_types = sorted(set(Option.values(Option.Kind.REPORT_TYPE)) | set(distinct("report_type")))
    return {
        "banks": distinct("bank"),
        "channels": distinct("channel"),
        "statuses": statuses,
        "report_types": report_types,
        "workflows": Transaction.Workflow.choices,
    }


def option_lists():
    return {
        "status_options": Option.values(Option.Kind.STATUS),
        "report_type_options": Option.values(Option.Kind.REPORT_TYPE),
        "item_name_options": Option.values(Option.Kind.ITEM_NAME),
    }


def selected_ids(request, key="ids"):
    ids = []
    for raw in request.POST.getlist(key):
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    return ids

"""Row-level access control: standard users only see their own records."""
from django.db.models import Q
from django.shortcuts import get_object_or_404

from core.models import ColumnMapping, ExcelTemplate, ExtractionBatch, GeneratedReport, Transaction


def visible_transactions(user):
    qs = Transaction.objects.select_related("created_by", "sms", "batch")
    return qs if user.can_view_all else qs.filter(created_by=user)


def visible_batches(user):
    qs = ExtractionBatch.objects.select_related("created_by")
    return qs if user.can_view_all else qs.filter(created_by=user)


def visible_reports(user):
    qs = GeneratedReport.objects.select_related("created_by", "template")
    return qs if user.can_view_all else qs.filter(created_by=user)


def visible_templates(user):
    qs = ExcelTemplate.objects.select_related("owner")
    return qs if user.is_admin_role else qs.filter(Q(owner=user) | Q(is_shared=True))


def visible_mappings(user):
    qs = ColumnMapping.objects.select_related("template")
    return qs if user.is_admin_role else qs.filter(Q(owner=user) | Q(template__is_shared=True))


def editable_transactions(user):
    """Reviewers/admins may edit everything; standard users only their own."""
    return visible_transactions(user)


def get_visible(qs_func, user, **lookup):
    return get_object_or_404(qs_func(user), **lookup)

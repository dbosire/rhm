import logging

from core.models import AuditLog

logger = logging.getLogger("rhm")


def client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    return ip or None


def log(request, action, obj=None, success=True, user=None, **details):
    """Record a sensitive operation. Never raises."""
    try:
        if user is None and request is not None and getattr(request, "user", None) and request.user.is_authenticated:
            user = request.user
        AuditLog.objects.create(
            user=user,
            action=action,
            object_type=obj.__class__.__name__ if obj is not None else "",
            object_id=str(getattr(obj, "pk", "") or "") if obj is not None else "",
            success=success,
            ip_address=client_ip(request),
            details={k: (v if isinstance(v, (int, float, bool, type(None))) else str(v)) for k, v in details.items()},
        )
    except Exception as exc:  # pragma: no cover
        logger.error("Audit log failure for %s: %s", action, exc)

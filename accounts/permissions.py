from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def role_required(check):
    """Decorator: login required + `check(user)` must be truthy."""

    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not check(request.user):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


admin_required = role_required(lambda u: u.is_admin_role)
reviewer_required = role_required(lambda u: u.can_review)

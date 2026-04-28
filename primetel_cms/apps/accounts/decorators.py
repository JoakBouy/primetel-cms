"""
Primetel CMS — Role-based access control decorators.
Server-side enforcement of RBAC on views.
"""
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import login_required


def requires_role(*role_codes):
    """
    Decorator that restricts view access to users with specific roles.

    Usage:
        @requires_role('CLINICIAN', 'NURSE')
        def my_view(request):
            ...

    Superusers bypass all role checks.
    """

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped_view(request, *args, **kwargs):
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            if not request.user.has_role(*role_codes):
                raise PermissionDenied(
                    "You do not have the required role to access this resource."
                )
            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator


def requires_any_role(view_func):
    """
    Decorator that requires any authenticated user with a role.
    Used for views accessible to all roles but not unauthenticated users.
    """

    @wraps(view_func)
    @login_required
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.role and not request.user.is_superuser:
            raise PermissionDenied("You must be assigned a role to access this resource.")
        return view_func(request, *args, **kwargs)

    return _wrapped_view

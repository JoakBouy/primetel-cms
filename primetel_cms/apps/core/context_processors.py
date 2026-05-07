"""
Primetel CMS — Core Context Processors
Provides global template context across all pages.
"""
from django.conf import settings


# Role → set of nav module keys the role should see.
# Keys correspond to the module slugs used in templates (top nav + sidebar).
# Dashboard is always visible to authenticated users.
NAV_BY_ROLE = {
    "ADMIN": {
        "patients", "appointments", "encounters",
        "pharmacy", "lab", "billing", "reports",
    },
    "RECEPTIONIST": {"patients", "appointments", "billing"},
    "NURSE": {"patients", "appointments", "encounters"},
    "CLINICIAN": {"patients", "appointments", "encounters", "pharmacy", "lab", "reports"},
    "COUNSELLOR": {"patients", "appointments", "encounters", "reports"},
    "PHARMACY": {"pharmacy", "patients", "lab"},
    "LAB": {"lab", "patients"},
    "FINANCE": {"billing", "reports", "patients"},
}


def _nav_visibility(user):
    """Return a dict of {module_key: bool} for the given user's role."""
    keys = ["patients", "appointments", "encounters", "pharmacy", "lab", "billing", "reports"]
    if not getattr(user, "is_authenticated", False):
        return {k: False for k in keys}
    if user.is_superuser:
        return {k: True for k in keys}
    role_code = getattr(getattr(user, "role", None), "code", None)
    allowed = NAV_BY_ROLE.get(role_code, set())
    return {k: (k in allowed) for k in keys}


def global_context(request):
    """Add global context variables available in all templates."""
    user = getattr(request, "user", None)
    # Defer the import so the apps registry is ready by the time we hit it.
    try:
        from .notifications import unread_count
        notif_unread = unread_count(user)
    except Exception:
        notif_unread = 0
    return {
        "SITE_NAME": "Primetel CMS",
        "CLINIC_NAME": "Monduli Clinic",
        "CLINIC_LOCATION": "Arusha, Tanzania",
        "AVAILABLE_LANGUAGES": settings.LANGUAGES,
        "CURRENT_LANGUAGE": getattr(request, "LANGUAGE_CODE", settings.LANGUAGE_CODE),
        "nav_visible": _nav_visibility(user),
        "notif_unread": notif_unread,
    }

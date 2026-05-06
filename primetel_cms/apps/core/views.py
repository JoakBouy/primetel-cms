"""
Primetel CMS — Core Views.
System-level views: CSRF failure page, health check, etc.
"""
from django.http import HttpResponseForbidden
from django.template.loader import render_to_string


def csrf_failure(request, reason=""):
    """
    Custom CSRF failure view.
    Shows a user-friendly page instead of the raw Django 403 so that clinic
    staff know to reload the page rather than panic.
    """
    html = render_to_string("accounts/csrf_failed.html", {
        "reason": reason,
    }, request=request)
    return HttpResponseForbidden(html)

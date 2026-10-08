"""
Primetel CMS — Core middleware.

- AuditLogMiddleware: logs every authenticated request to AuditLog.
- IdleSessionTimeoutMiddleware: forces re-authentication after a period of
  inactivity (settings.IDLE_SESSION_SECONDS, default 15 minutes) and after an
  absolute session lifetime (settings.SESSION_COOKIE_AGE, one shift).

Paths are compared after stripping the language prefix, so "/en/patients/..."
and "/patients/..." are treated the same.
"""
import re
import time
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .models import AuditLog
from .utils import get_client_ip, strip_language_prefix


# URL patterns that trigger specific audit entries (matched on the
# language-neutral path).
PATIENT_CHART_PATTERN = re.compile(r"^/patients/([0-9a-f-]+)/?$", re.IGNORECASE)

# Background requests the browser fires on its own (HTMX polling). They must
# not count as user activity, otherwise an open tab never goes idle.
BACKGROUND_POLL_PATHS = (
    "/api/notifications/badge/",
    "/appointments/queue/summary/",
)

_AUDIT_SKIP = (
    "/static/", "/media/", "/healthz/", "/health/", "/favicon", "/sw.js",
) + BACKGROUND_POLL_PATHS

# Paths exempted from the session timeouts (authentication itself, language, health).
_IDLE_EXEMPT = ("/login/", "/logout/", "/healthz/", "/health/", "/i18n/", "/static/", "/sw.js")

LAST_ACTIVITY_KEY = "_last_activity"
SESSION_STARTED_KEY = "_session_started"


def is_background_poll(path: str) -> bool:
    return strip_language_prefix(path).startswith(BACKGROUND_POLL_PATHS)


class AuditLogMiddleware:
    """
    Middleware that logs every authenticated request to the AuditLog model.
    Special handling for patient chart views (logged as READ).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Only log authenticated requests
        if not hasattr(request, "user") or not request.user.is_authenticated:
            return response

        # Skip static/media/health requests AND noisy background polls — the
        # bell badge polls every 30s per user and would otherwise generate an
        # audit row per poll, bloating the table without adding signal.
        path = strip_language_prefix(request.path)
        if path.startswith(_AUDIT_SKIP):
            return response

        try:
            self._log_request(request, response, path)
        except Exception:
            # Never let audit logging break the application
            pass

        return response

    def _log_request(self, request, response, path):
        """Create an audit log entry based on the request."""
        method_to_action = {
            "GET": "READ",
            "POST": "CREATE",
            "PUT": "UPDATE",
            "PATCH": "UPDATE",
            "DELETE": "DELETE",
        }
        action = method_to_action.get(request.method, "READ")

        # Check for patient chart access specifically
        entity_type = "Request"
        entity_id = None
        match = PATIENT_CHART_PATTERN.match(path)
        if match:
            entity_type = "Patient"
            try:
                entity_id = uuid.UUID(match.group(1))
            except ValueError:
                pass
            action = "READ"

        AuditLog.objects.create(
            actor=request.user,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            metadata={
                "path": request.path,
                "method": request.method,
                "status_code": response.status_code,
            },
            ip_address=get_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
        )


class IdleSessionTimeoutMiddleware:
    """
    Logs the user out after `settings.IDLE_SESSION_SECONDS` of inactivity, or
    once the session is older than `settings.SESSION_COOKIE_AGE` regardless of
    activity. Background polls are checked but never refresh the idle timer.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        path = strip_language_prefix(request.path)
        if user is not None and user.is_authenticated and not path.startswith(_IDLE_EXEMPT):
            idle_seconds = int(getattr(settings, "IDLE_SESSION_SECONDS", 15 * 60))
            max_age = int(getattr(settings, "SESSION_COOKIE_AGE", 8 * 60 * 60))
            now = int(time.time())
            session = request.session

            started = session.get(SESSION_STARTED_KEY)
            last = session.get(LAST_ACTIVITY_KEY)
            idle_expired = last is not None and now - int(last) > idle_seconds
            absolute_expired = started is not None and now - int(started) > max_age
            if idle_expired or absolute_expired:
                logout(request)
                if idle_expired:
                    messages.info(request, _("You were signed out due to inactivity."))
                else:
                    messages.info(request, _("Your session has ended. Please sign in again."))
                return self._login_redirect(request)

            if started is None:
                session[SESSION_STARTED_KEY] = now
            if not path.startswith(BACKGROUND_POLL_PATHS):
                session[LAST_ACTIVITY_KEY] = now
        return self.get_response(request)

    @staticmethod
    def _login_redirect(request):
        login_url = reverse("login")
        if request.headers.get("HX-Request"):
            # A plain 302 would be followed inside the XHR and the login page
            # swapped into a tiny badge; HX-Redirect makes HTMX navigate the tab.
            response = HttpResponse(status=200)
            response["HX-Redirect"] = login_url
            return response
        return redirect(login_url)

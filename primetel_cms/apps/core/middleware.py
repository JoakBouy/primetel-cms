"""
Primetel CMS — Core middleware.

- AuditLogMiddleware: logs every authenticated request to AuditLog.
- IdleTimeoutMiddleware: forces re-authentication after a period of inactivity,
  separate from the absolute SESSION_COOKIE_AGE. Defaults to 15 minutes; tune
  via settings.IDLE_SESSION_SECONDS.
"""
import re
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _

from .models import AuditLog


# URL patterns that trigger specific audit entries
PATIENT_CHART_PATTERN = re.compile(r"^/patients/([0-9a-f-]+)/?$", re.IGNORECASE)


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

        # Skip static/media/admin/healthz requests AND noisy background polls.
        # The bell badge polls every 30s per user and would otherwise generate
        # an audit row per poll, bloating the table without adding signal.
        path = request.path
        if any(
            path.startswith(prefix)
            for prefix in [
                "/static/", "/media/", "/healthz/", "/health/", "/favicon",
                "/sw.js", "/api/notifications/badge/",
            ]
        ):
            return response

        try:
            self._log_request(request, response)
        except Exception:
            # Never let audit logging break the application
            pass

        return response

    def _log_request(self, request, response):
        """Create an audit log entry based on the request."""
        ip = self._get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        # Determine action based on HTTP method
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
        match = PATIENT_CHART_PATTERN.match(request.path)
        if match:
            entity_type = "Patient"
            try:
                import uuid as uuid_mod
                entity_id = uuid_mod.UUID(match.group(1))
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
            ip_address=ip,
            user_agent=user_agent[:500],  # Truncate long user agents
        )

    def _get_client_ip(self, request):
        """Extract client IP, accounting for proxies."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")


# Paths exempted from idle-timeout (authentication itself, language, health).
_IDLE_EXEMPT = ("/login/", "/logout/", "/healthz/", "/i18n/", "/static/", "/media/", "/sw.js")


class IdleSessionTimeoutMiddleware:
    """
    Logs the user out after `settings.IDLE_SESSION_SECONDS` of inactivity.
    The SESSION_COOKIE_AGE remains the absolute upper bound; this is the
    *idle* bound for shared clinical workstations.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.idle_seconds = int(getattr(settings, "IDLE_SESSION_SECONDS", 15 * 60))

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and not any(request.path.startswith(p) for p in _IDLE_EXEMPT)
        ):
            now = int(time.time())
            last = request.session.get("_last_activity")
            if last is not None and now - int(last) > self.idle_seconds:
                logout(request)
                messages.info(request, _("You were signed out due to inactivity."))
                return redirect("login")
            # Update timestamp; SESSION_SAVE_EVERY_REQUEST flushes it.
            request.session["_last_activity"] = now
        return self.get_response(request)

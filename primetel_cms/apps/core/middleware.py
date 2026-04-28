"""
Primetel CMS — Audit Log Middleware
Logs every authenticated request to AuditLog.
Patient chart views are logged as READ with entity_type='Patient'.
"""
import re

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

        # Skip static/media/admin/healthz requests
        path = request.path
        if any(
            path.startswith(prefix)
            for prefix in ["/static/", "/media/", "/healthz/", "/favicon"]
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

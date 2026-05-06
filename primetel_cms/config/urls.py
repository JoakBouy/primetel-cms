"""
Primetel CMS — Root URL Configuration
"""
from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.generic import RedirectView, TemplateView


def healthz(request):
    """Health check endpoint for Render."""
    from django.db import connection
    try:
        # Perform a simple query to ensure the database is reachable
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok", "database": "connected"})
    except Exception as e:
        return JsonResponse({"status": "error", "database": "disconnected", "details": str(e)}, status=503)


urlpatterns = [
    # Health check — no auth required (supports both /healthz/ and /health/)
    path("healthz/", healthz, name="healthz"),
    path("health/", healthz, name="health"),
    # Language switcher
    path("i18n/", include("django.conf.urls.i18n")),
    # Service worker
    path("sw.js", TemplateView.as_view(template_name="sw.js", content_type="application/javascript")),
]

urlpatterns += i18n_patterns(
    # Root redirect
    path("", RedirectView.as_view(url="/dashboard/", permanent=False)),
    # Auth
    path("", include("apps.accounts.urls")),
    # Admin
    path("admin/", admin.site.urls),
    # Apps
    path("dashboard/", include("apps.reports.urls")),
    path("patients/", include("apps.patients.urls")),
    path("appointments/", include("apps.appointments.urls")),
    path("encounters/", include("apps.encounters.urls")),
    path("pharmacy/", include("apps.pharmacy.urls")),
    path("lab/", include("apps.lab.urls")),
    path("billing/", include("apps.billing.urls")),
    path("reports/", include("apps.reports.urls_reports")),
    # API (for HTMX JSON endpoints)
    path("api/", include("apps.core.urls_api")),
    prefix_default_language=False,
)

# Serve media in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

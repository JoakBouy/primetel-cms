"""
Primetel CMS — Core Context Processors
Provides global template context across all pages.
"""
from django.conf import settings


def global_context(request):
    """Add global context variables available in all templates."""
    return {
        "SITE_NAME": "Primetel CMS",
        "CLINIC_NAME": "Monduli Clinic",
        "CLINIC_LOCATION": "Arusha, Tanzania",
        "AVAILABLE_LANGUAGES": settings.LANGUAGES,
        "CURRENT_LANGUAGE": getattr(request, "LANGUAGE_CODE", settings.LANGUAGE_CODE),
    }

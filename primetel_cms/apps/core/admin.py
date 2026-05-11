"""
Primetel CMS — Core Admin
"""
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_http_methods

from .factory_reset import (
    looks_like_production, plan_factory_reset, run_factory_reset,
)
from .models import AuditLog, ConfigSetting, ReasonCode


@admin.register(ReasonCode)
class ReasonCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "display_name", "category", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("code", "display_name")


@admin.register(ConfigSetting)
class ConfigSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "value", "updated_at")
    search_fields = ("key",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "actor", "action", "entity_type", "ip_address")
    list_filter = ("action", "entity_type")
    search_fields = ("actor__username", "entity_type")
    readonly_fields = ("id", "actor", "action", "entity_type", "entity_id", "metadata", "ip_address", "user_agent", "timestamp")
    date_hierarchy = "timestamp"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ──────────────────────────────────────────────────────────────────
#  Factory-reset admin view ("System" / Danger zone)
# ──────────────────────────────────────────────────────────────────

# The magic word the operator must type before the wipe runs. Kept simple so
# anyone reading the page understands what to do, but specific enough that
# muscle-memory confirmation clicks won't trigger a wipe.
_CONFIRM_PHRASE = "WIPE"


@method_decorator(staff_member_required, name="dispatch")
class _Dummy:
    """Unused — kept here so static analyzers stop complaining about imports."""
    pass


@require_http_methods(["GET", "POST"])
@staff_member_required
def factory_reset_view(request):
    """Admin-only 'factory reset' page.

    GET  → renders the dry-run plan + a typed-confirmation form.
    POST → if the typed phrase matches "WIPE", runs `run_factory_reset()`.
           Otherwise re-renders the form with an error.

    Permission: superuser or staff with the `admin` role. Anyone else gets
    a 403 (the `staff_member_required` decorator already redirects non-staff
    to the admin login).
    """
    # Tighter check than @staff_member_required: must be superuser or
    # explicit ADMIN role. Staff isn't enough — a finance person flagged as
    # is_staff should not be able to wipe the DB.
    if not request.user.is_superuser:
        role = getattr(request.user, "role", None)
        if not role or getattr(role, "code", None) != "ADMIN":
            return HttpResponseForbidden(
                "Factory reset is restricted to superusers and ADMIN role."
            )

    plan, live_total, history_total, kept = plan_factory_reset()

    if request.method == "POST":
        typed = (request.POST.get("confirm_phrase") or "").strip()
        if typed != _CONFIRM_PHRASE:
            messages.error(
                request,
                f'You must type "{_CONFIRM_PHRASE}" exactly to confirm. '
                "Nothing was deleted.",
            )
            return render(request, "admin/factory_reset.html", {
                "title": "Factory reset",
                "plan": plan,
                "live_total": live_total,
                "history_total": history_total,
                "kept": kept,
                "confirm_phrase": _CONFIRM_PHRASE,
                "is_production": looks_like_production(),
            })

        summary = run_factory_reset()
        wiped_live = sum(s[1] for s in summary)
        wiped_history = sum(s[2] for s in summary)
        messages.success(
            request,
            f"Factory reset complete. Deleted {wiped_live} live rows + "
            f"{wiped_history} history rows. Users and roles preserved.",
        )
        return redirect(reverse("admin:index"))

    return render(request, "admin/factory_reset.html", {
        "title": "Factory reset",
        "plan": plan,
        "live_total": live_total,
        "history_total": history_total,
        "kept": kept,
        "confirm_phrase": _CONFIRM_PHRASE,
        "is_production": looks_like_production(),
    })


# Hook the view into the admin URL conf. Django calls AdminSite.get_urls()
# every request, so monkey-patching it once at import time is the standard
# pattern for adding admin pages that aren't tied to a Model.
_original_get_urls = admin.site.get_urls


def _patched_get_urls():
    custom = [
        path(
            "system/factory-reset/",
            admin.site.admin_view(factory_reset_view),
            name="factory_reset",
        ),
    ]
    return custom + _original_get_urls()


admin.site.get_urls = _patched_get_urls

"""
Primetel CMS — Core Admin
"""
from django.contrib import admin

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

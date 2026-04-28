"""
Primetel CMS — Accounts Admin
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import Role, User


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("display_name", "code", "description")
    search_fields = ("code", "display_name")
    readonly_fields = ("id",)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "full_name", "role", "language_preference", "is_active", "is_staff")
    list_filter = ("role", "language_preference", "is_active", "is_staff")
    search_fields = ("username", "full_name", "email", "phone")
    ordering = ("full_name",)

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (_("Personal Info"), {"fields": ("full_name", "email", "phone")}),
        (_("Role & Preferences"), {"fields": ("role", "language_preference")}),
        (
            _("Permissions"),
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "full_name", "role", "password1", "password2"),
            },
        ),
    )

"""
Primetel CMS — Patient Admin
"""
from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import Patient


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = (
        "patient_number",
        "full_name",
        "age_display",
        "sex",
        "phone",
        "district",
        "is_pregnant",
        "is_deleted",
    )
    list_filter = ("sex", "is_pregnant", "is_deleted", "district", "language_preference")
    search_fields = ("full_name", "patient_number", "phone", "national_id")
    readonly_fields = ("id", "patient_number", "created_at", "updated_at", "is_minor")
    ordering = ("full_name",)

    def get_queryset(self, request):
        return Patient.objects.all_including_deleted()

    def age_display(self, obj):
        return obj.age_display
    age_display.short_description = _("Age")

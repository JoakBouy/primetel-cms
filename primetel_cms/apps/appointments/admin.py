"""Appointments Admin."""
from django.contrib import admin
from .models import Appointment, AppointmentType

@admin.register(AppointmentType)
class AppointmentTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "display_name", "duration_minutes", "is_active")

@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("patient", "clinician", "scheduled_start", "status", "is_walk_in")
    list_filter = ("status", "is_walk_in", "appointment_type")
    search_fields = ("patient__full_name",)
    date_hierarchy = "scheduled_start"

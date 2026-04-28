"""Lab Admin."""
from django.contrib import admin
from .models import LabTest, LabOrder, LabResult

@admin.register(LabTest)
class LabTestAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "specimen_type", "price_tzs", "is_active", "is_send_out")
    list_filter = ("specimen_type", "is_active")
    search_fields = ("code", "name")

@admin.register(LabOrder)
class LabOrderAdmin(admin.ModelAdmin):
    list_display = ("test", "encounter", "status", "ordered_by", "ordered_at")
    list_filter = ("status",)
    date_hierarchy = "ordered_at"

@admin.register(LabResult)
class LabResultAdmin(admin.ModelAdmin):
    list_display = ("lab_order", "value_numeric", "value_text", "flag", "performed_by")

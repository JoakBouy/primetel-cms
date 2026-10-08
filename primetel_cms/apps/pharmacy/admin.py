"""Pharmacy Admin."""
from django.contrib import admin
from .models import Drug, StockItem, StockMovement, Prescription, Dispense


class ReadOnlyAdminMixin:
    """Records whose save() has side effects (stock, billing) are created by
    the app's own workflows; the admin may only inspect them."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Drug)
class DrugAdmin(admin.ModelAdmin):
    list_display = ("generic_name", "strength", "form", "unit_price_tzs", "total_stock", "is_low_stock", "is_active")
    list_filter = ("form", "is_active")
    search_fields = ("generic_name", "brand_name")

@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = ("drug", "batch_number", "expiry_date", "quantity_on_hand")
    list_filter = ("drug",)

@admin.register(StockMovement)
class StockMovementAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("stock_item", "movement_type", "quantity", "reason", "performed_by", "performed_at")
    list_filter = ("movement_type",)

@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    list_display = ("drug", "encounter", "dose", "frequency", "quantity", "status")
    list_filter = ("status",)

@admin.register(Dispense)
class DispenseAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("prescription", "stock_item", "quantity_dispensed", "dispensed_by", "dispensed_at")

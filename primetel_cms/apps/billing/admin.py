"""Billing Admin."""
from django.contrib import admin
from .models import ServiceItem, Invoice, InvoiceLine, Payment

@admin.register(ServiceItem)
class ServiceItemAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "unit_price_tzs", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("code", "name")

class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 1

class PaymentInline(admin.TabularInline):
    """Payments are recorded/voided through the billing screens so totals,
    status and the audit trail stay consistent; the admin only shows them."""
    model = Payment
    extra = 0
    can_delete = False
    readonly_fields = ("method", "amount_tzs", "reference", "waiver_reason", "reverses", "received_by", "received_at")

    def has_add_permission(self, request, obj=None):
        return False

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "patient", "total_tzs", "amount_paid_tzs", "balance_tzs", "status", "prepay_type", "issued_at")
    list_filter = ("status", "prepay_type")
    search_fields = ("invoice_number", "patient__full_name")
    date_hierarchy = "issued_at"
    inlines = [InvoiceLineInline, PaymentInline]

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("invoice", "method", "amount_tzs", "reference", "received_by", "received_at")
    list_filter = ("method",)
    date_hierarchy = "received_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

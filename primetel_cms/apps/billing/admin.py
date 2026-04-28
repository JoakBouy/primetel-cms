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
    model = Payment
    extra = 0
    readonly_fields = ("received_at",)

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "patient", "total_tzs", "amount_paid_tzs", "balance_tzs", "status", "issued_at")
    list_filter = ("status",)
    search_fields = ("invoice_number", "patient__full_name")
    date_hierarchy = "issued_at"
    inlines = [InvoiceLineInline, PaymentInline]

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("invoice", "method", "amount_tzs", "reference", "received_by", "received_at")
    list_filter = ("method",)
    date_hierarchy = "received_at"

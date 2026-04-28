"""
Primetel CMS — Billing Models
Invoices, payments, receipts, service items.
"""
import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords
from apps.core.models import TimestampedModel


def generate_invoice_number():
    from django.utils import timezone
    year = timezone.now().year
    prefix = f"INV-{year}-"
    last = Invoice.objects.filter(invoice_number__startswith=prefix).order_by("-invoice_number").first()
    seq = int(last.invoice_number.split("-")[-1]) + 1 if last else 1
    return f"{prefix}{seq:06d}"


class ServiceItem(models.Model):
    """Price list for services."""
    CATEGORY_CHOICES = [
        ("CONSULT", _("Consultation")), ("PROCEDURE", _("Procedure")),
        ("LAB", _("Lab")), ("PHARMACY", _("Pharmacy")), ("OTHER", _("Other")),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES)
    unit_price_tzs = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "name"]
        verbose_name = _("Service Item")

    def __str__(self):
        return f"{self.code} — {self.name}"


class Invoice(TimestampedModel):
    """Patient invoice."""
    STATUS_CHOICES = [
        ("DRAFT", _("Draft")), ("ISSUED", _("Issued")),
        ("PAID", _("Paid")), ("PARTIALLY_PAID", _("Partially Paid")),
        ("CANCELLED", _("Cancelled")), ("WAIVED", _("Waived")),
    ]
    invoice_number = models.CharField(max_length=20, unique=True, db_index=True)
    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="invoices")
    encounter = models.ForeignKey("encounters.Encounter", on_delete=models.SET_NULL, null=True, blank=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    subtotal_tzs = models.DecimalField(max_digits=12, decimal_places=0, default=0)
    discount_tzs = models.DecimalField(max_digits=12, decimal_places=0, default=0)
    total_tzs = models.DecimalField(max_digits=12, decimal_places=0, default=0)
    amount_paid_tzs = models.DecimalField(max_digits=12, decimal_places=0, default=0)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="DRAFT", db_index=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="issued_invoices")
    history = HistoricalRecords()

    class Meta:
        ordering = ["-issued_at"]
        verbose_name = _("Invoice")

    def __str__(self):
        return f"{self.invoice_number} — {self.patient}"

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            self.invoice_number = generate_invoice_number()
        super().save(*args, **kwargs)

    @property
    def balance_tzs(self):
        return self.total_tzs - self.amount_paid_tzs

    def recalculate(self):
        """Recalculate subtotal and total from lines."""
        self.subtotal_tzs = sum(line.line_total_tzs for line in self.lines.all())
        self.total_tzs = self.subtotal_tzs - self.discount_tzs
        self.save(update_fields=["subtotal_tzs", "total_tzs"])


class InvoiceLine(models.Model):
    """Individual line item on an invoice."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    service_item = models.ForeignKey(ServiceItem, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    unit_price_tzs = models.DecimalField(max_digits=10, decimal_places=0)

    class Meta:
        verbose_name = _("Invoice Line")

    @property
    def line_total_tzs(self):
        return self.quantity * self.unit_price_tzs

    def __str__(self):
        return f"{self.description} x{self.quantity}"


class Payment(TimestampedModel):
    """Payment against an invoice."""
    METHOD_CHOICES = [
        ("CASH", _("Cash")), ("MPESA", _("M-Pesa")),
        ("CARD", _("Card")), ("NHIF", _("NHIF")), ("WAIVER", _("Waiver")),
    ]
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=10, choices=METHOD_CHOICES)
    amount_tzs = models.DecimalField(max_digits=12, decimal_places=0)
    reference = models.CharField(max_length=100, blank=True, default="")
    waiver_reason = models.ForeignKey("core.ReasonCode", on_delete=models.SET_NULL, null=True, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="received_payments")
    received_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ["-received_at"]
        verbose_name = _("Payment")

    def __str__(self):
        return f"{self.method} {self.amount_tzs} TZS for {self.invoice}"

    def save(self, *args, **kwargs):
        with transaction.atomic():
            super().save(*args, **kwargs)
            inv = self.invoice
            inv.amount_paid_tzs = sum(p.amount_tzs for p in inv.payments.all())
            if inv.amount_paid_tzs >= inv.total_tzs:
                inv.status = "PAID"
            elif inv.amount_paid_tzs > 0:
                inv.status = "PARTIALLY_PAID"
            inv.save(update_fields=["amount_paid_tzs", "status"])

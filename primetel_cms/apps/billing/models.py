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
    year = timezone.localdate().year
    prefix = f"INV-{year}-"
    last = (
        Invoice.objects.filter(invoice_number__regex=rf"^INV-{year}-[0-9]{{6}}$")
        .order_by("-invoice_number")
        .first()
    )
    seq = int(last.invoice_number.split("-")[-1]) + 1 if last else 1
    return f"{prefix}{seq:06d}"


# Statuses that accept no further payments or charges.
CLOSED_STATUSES = ("CANCELLED", "WAIVED")


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

    # Set on consultation invoices paid up front, before the encounter exists.
    # auto_charge only attaches an encounter to a prepay of the matching kind,
    # so a pharmacy-only or lab-only invoice is never mistaken for a consult.
    PREPAY_CHOICES = [
        ("", _("Not a prepaid consultation")),
        ("GENERAL", _("General consultation")),
        ("MENTAL_HEALTH", _("Mental health consultation")),
    ]
    prepay_type = models.CharField(max_length=20, choices=PREPAY_CHOICES, blank=True, default="", db_index=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ["-issued_at"]
        verbose_name = _("Invoice")

    def __str__(self):
        return f"{self.invoice_number} — {self.patient}"

    def save(self, *args, **kwargs):
        if self.invoice_number:
            super().save(*args, **kwargs)
            return
        # Number generation is "last + 1"; if two invoices are created at the
        # same moment the unique constraint rejects one — retry with the next.
        from django.db import IntegrityError
        for attempt in range(5):
            self.invoice_number = generate_invoice_number()
            try:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                return
            except IntegrityError:
                if attempt == 4:
                    raise
                self.invoice_number = ""

    @property
    def balance_tzs(self):
        return self.total_tzs - self.amount_paid_tzs

    @property
    def is_closed(self):
        return self.status in CLOSED_STATUSES

    def sync_status(self):
        """Derive the payment status from totals (no save)."""
        if self.status in CLOSED_STATUSES:
            return
        if self.total_tzs > 0 and self.amount_paid_tzs >= self.total_tzs:
            self.status = "PAID"
        elif self.amount_paid_tzs > 0:
            self.status = "PARTIALLY_PAID"
        elif self.status in ("PAID", "PARTIALLY_PAID"):
            # Everything paid was reversed — back to an open, unpaid invoice.
            self.status = "ISSUED"

    def recalculate(self):
        """Recalculate subtotal and total from lines and refresh the status."""
        self.subtotal_tzs = sum(line.line_total_tzs for line in self.lines.all())
        self.total_tzs = self.subtotal_tzs - self.discount_tzs
        self.sync_status()
        self.save(update_fields=["subtotal_tzs", "total_tzs", "status"])


class InvoiceLine(models.Model):
    """Individual line item on an invoice."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    service_item = models.ForeignKey(ServiceItem, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    unit_price_tzs = models.DecimalField(max_digits=10, decimal_places=0)
    # Source of an automatically billed line, so cancelling/editing the
    # prescription or lab order can adjust the charge.
    prescription = models.ForeignKey(
        "pharmacy.Prescription", on_delete=models.SET_NULL, null=True, blank=True, related_name="invoice_lines",
    )
    lab_order = models.ForeignKey(
        "lab.LabOrder", on_delete=models.SET_NULL, null=True, blank=True, related_name="invoice_lines",
    )

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
    # A void is recorded as a negative payment pointing at the original.
    reverses = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversals",
    )
    history = HistoricalRecords()

    class Meta:
        ordering = ["-received_at"]
        verbose_name = _("Payment")

    def __str__(self):
        return f"{self.method} {self.amount_tzs} TZS for {self.invoice}"

    @property
    def is_voided(self):
        return self.reversals.exists()

    def save(self, *args, **kwargs):
        with transaction.atomic():
            # Lock the parent invoice so concurrent payments tally consistently.
            inv = Invoice.objects.select_for_update().get(pk=self.invoice_id)
            if inv.status in CLOSED_STATUSES:
                raise ValidationError(_("Cannot record a payment against a closed invoice."))
            super().save(*args, **kwargs)
            inv.amount_paid_tzs = sum(p.amount_tzs for p in inv.payments.all())
            inv.sync_status()
            inv.save(update_fields=["amount_paid_tzs", "status"])

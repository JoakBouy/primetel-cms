"""
Primetel CMS — Pharmacy Models
Drug formulary, stock management, prescriptions, dispensing.
"""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import TimestampedModel


class Drug(TimestampedModel):
    FORM_CHOICES = [
        ("TABLET", _("Tablet")), ("CAPSULE", _("Capsule")),
        ("SYRUP", _("Syrup")), ("INJECTION", _("Injection")),
        ("CREAM", _("Cream")), ("DROPS", _("Drops")), ("OTHER", _("Other")),
    ]
    generic_name = models.CharField(max_length=255)
    brand_name = models.CharField(max_length=255, blank=True, default="")
    strength = models.CharField(max_length=50)
    form = models.CharField(max_length=10, choices=FORM_CHOICES)
    pack_size = models.PositiveIntegerField(default=1)
    unit_price_tzs = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    low_stock_threshold = models.PositiveIntegerField(default=10)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["generic_name"]
        verbose_name = _("Drug")
        verbose_name_plural = _("Drugs")

    def __str__(self):
        return f"{self.generic_name} {self.strength}"

    @property
    def total_stock(self):
        return sum(s.quantity_on_hand for s in self.stock_items.all())

    @property
    def is_low_stock(self):
        return self.total_stock <= self.low_stock_threshold


class StockItem(TimestampedModel):
    drug = models.ForeignKey(Drug, on_delete=models.CASCADE, related_name="stock_items")
    batch_number = models.CharField(max_length=50)
    expiry_date = models.DateField()
    quantity_on_hand = models.IntegerField(default=0)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["expiry_date"]

    def __str__(self):
        return f"{self.drug.generic_name} Batch {self.batch_number}"

    @property
    def is_expired(self):
        from datetime import date
        return self.expiry_date < date.today()

    @property
    def is_near_expiry(self):
        from datetime import date, timedelta
        return self.expiry_date <= date.today() + timedelta(days=90)


class StockMovement(models.Model):
    MOVEMENT_TYPES = [
        ("RECEIVE", _("Receive")), ("DISPENSE", _("Dispense")),
        ("ADJUST", _("Adjust")), ("WRITE_OFF", _("Write Off")), ("TRANSFER", _("Transfer")),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    stock_item = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name="movements")
    movement_type = models.CharField(max_length=10, choices=MOVEMENT_TYPES)
    quantity = models.IntegerField()
    reason = models.ForeignKey("core.ReasonCode", on_delete=models.SET_NULL, null=True, blank=True)
    reference_id = models.UUIDField(null=True, blank=True)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    performed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-performed_at"]


class Prescription(TimestampedModel):
    STATUS_CHOICES = [
        ("PRESCRIBED", _("Prescribed")), ("DISPENSED", _("Dispensed")),
        ("PARTIALLY_DISPENSED", _("Partially Dispensed")), ("CANCELLED", _("Cancelled")),
    ]
    encounter = models.ForeignKey("encounters.Encounter", on_delete=models.CASCADE, related_name="prescriptions")
    drug = models.ForeignKey(Drug, on_delete=models.PROTECT, related_name="prescriptions")
    dose = models.CharField(max_length=100)
    frequency = models.CharField(max_length=100)
    duration_days = models.PositiveSmallIntegerField()
    quantity = models.PositiveIntegerField()
    instructions = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PRESCRIBED", db_index=True)
    prescribed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="prescriptions")
    prescribed_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ["-prescribed_at"]

    def __str__(self):
        return f"{self.drug} x{self.quantity}"


class Dispense(TimestampedModel):
    """Dispense record — atomic stock deduction on save."""
    prescription = models.ForeignKey(Prescription, on_delete=models.CASCADE, related_name="dispenses")
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="dispenses")
    quantity_dispensed = models.PositiveIntegerField()
    dispensed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="dispenses")
    dispensed_at = models.DateTimeField(auto_now_add=True)
    patient_counselled = models.BooleanField(default=False)
    history = HistoricalRecords()

    class Meta:
        ordering = ["-dispensed_at"]

    def save(self, *args, **kwargs):
        with transaction.atomic():
            stock = StockItem.objects.select_for_update().get(pk=self.stock_item_id)
            if stock.quantity_on_hand < self.quantity_dispensed:
                raise ValidationError(
                    _("Cannot dispense %(qty)s — only %(avail)s in stock.") %
                    {"qty": self.quantity_dispensed, "avail": stock.quantity_on_hand}
                )
            stock.quantity_on_hand -= self.quantity_dispensed
            stock.save(update_fields=["quantity_on_hand"])
            super().save(*args, **kwargs)
            StockMovement.objects.create(
                stock_item=stock, movement_type="DISPENSE",
                quantity=-self.quantity_dispensed, reference_id=self.pk,
                performed_by=self.dispensed_by,
            )
            total = sum(d.quantity_dispensed for d in self.prescription.dispenses.all())
            self.prescription.status = "DISPENSED" if total >= self.prescription.quantity else "PARTIALLY_DISPENSED"
            self.prescription.save(update_fields=["status"])

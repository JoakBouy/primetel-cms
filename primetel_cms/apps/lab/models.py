"""
Primetel CMS — Lab Models
Test catalogue, orders, results.
"""
import uuid
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords
from apps.core.models import TimestampedModel


class LabTest(models.Model):
    """Lab test catalogue — seedable."""
    SPECIMEN_CHOICES = [
        ("BLOOD", _("Blood")), ("URINE", _("Urine")),
        ("STOOL", _("Stool")), ("SWAB", _("Swab")), ("OTHER", _("Other")),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=255)
    specimen_type = models.CharField(max_length=10, choices=SPECIMEN_CHOICES)
    reference_range_min = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    reference_range_max = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    reference_unit = models.CharField(max_length=30, blank=True, default="")
    price_tzs = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    is_active = models.BooleanField(default=True)
    is_send_out = models.BooleanField(default=False, help_text="Performed externally")

    class Meta:
        ordering = ["name"]
        verbose_name = _("Lab Test")
        verbose_name_plural = _("Lab Tests")

    def __str__(self):
        return f"{self.code} — {self.name}"


class LabOrder(TimestampedModel):
    """Lab order linked to an encounter."""
    STATUS_CHOICES = [
        ("ORDERED", _("Ordered")), ("COLLECTED", _("Collected")),
        ("RESULTED", _("Resulted")), ("REVIEWED", _("Reviewed")),
        ("CANCELLED", _("Cancelled")),
    ]
    encounter = models.ForeignKey("encounters.Encounter", on_delete=models.CASCADE, related_name="lab_orders")
    test = models.ForeignKey(LabTest, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="ORDERED", db_index=True)
    ordered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="lab_orders")
    ordered_at = models.DateTimeField(auto_now_add=True)
    collected_at = models.DateTimeField(null=True, blank=True)
    resulted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    external_reference = models.CharField(max_length=100, blank=True, default="")
    history = HistoricalRecords()

    class Meta:
        ordering = ["-ordered_at"]
        verbose_name = _("Lab Order")

    def __str__(self):
        return f"{self.test.code} for {self.encounter.patient}"


class LabResult(TimestampedModel):
    """Result for a lab order."""
    FLAG_CHOICES = [
        ("NORMAL", _("Normal")), ("LOW", _("Low")),
        ("HIGH", _("High")), ("CRITICAL", _("Critical")),
    ]
    lab_order = models.OneToOneField(LabOrder, on_delete=models.CASCADE, related_name="result")
    value_numeric = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    value_text = models.TextField(blank=True, default="")
    flag = models.CharField(max_length=10, choices=FLAG_CHOICES, null=True, blank=True)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    notes = models.TextField(blank=True, default="")
    history = HistoricalRecords()

    class Meta:
        verbose_name = _("Lab Result")

    def __str__(self):
        val = self.value_numeric if self.value_numeric else self.value_text
        return f"{self.lab_order.test.code}: {val}"

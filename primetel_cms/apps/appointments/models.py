"""
Primetel CMS — Appointments Models
Calendar, queue, check-in/out, appointment types.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import TimestampedModel


class AppointmentType(models.Model):
    """Seedable config table for appointment types."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=30, unique=True)
    display_name = models.CharField(max_length=100)
    duration_minutes = models.PositiveSmallIntegerField(default=15)
    color = models.CharField(max_length=7, default="#2B6CB0", help_text="Hex color for calendar display")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_name"]
        verbose_name = _("Appointment Type")
        verbose_name_plural = _("Appointment Types")

    def __str__(self):
        return self.display_name


class Appointment(TimestampedModel):
    """Scheduled and walk-in appointments."""

    STATUS_CHOICES = [
        ("SCHEDULED", _("Scheduled")),
        ("CHECKED_IN", _("Checked In")),
        ("IN_CONSULT", _("In Consultation")),
        ("COMPLETED", _("Completed")),
        ("CANCELLED", _("Cancelled")),
        ("NO_SHOW", _("No Show")),
    ]

    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.CASCADE, related_name="appointments"
    )
    clinician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clinician_appointments",
    )
    appointment_type = models.ForeignKey(
        AppointmentType, on_delete=models.PROTECT, null=True, blank=True
    )
    scheduled_start = models.DateTimeField(_("Scheduled Start"))
    scheduled_end = models.DateTimeField(_("Scheduled End"))
    status = models.CharField(
        max_length=15, choices=STATUS_CHOICES, default="SCHEDULED", db_index=True
    )
    is_walk_in = models.BooleanField(default=False)
    check_in_at = models.DateTimeField(null=True, blank=True)
    check_out_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.ForeignKey(
        "core.ReasonCode",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_appointments",
    )
    notes = models.TextField(blank=True, default="")

    history = HistoricalRecords()

    class Meta:
        ordering = ["scheduled_start"]
        verbose_name = _("Appointment")
        verbose_name_plural = _("Appointments")
        indexes = [
            models.Index(fields=["status", "scheduled_start"]),
            models.Index(fields=["patient", "scheduled_start"]),
        ]

    def __str__(self):
        return f"{self.patient} — {self.scheduled_start:%d/%m/%Y %H:%M}"

    def check_in(self, user=None):
        from django.utils import timezone
        self.status = "CHECKED_IN"
        self.check_in_at = timezone.now()
        self.updated_by = user
        self.save(update_fields=["status", "check_in_at", "updated_by", "updated_at"])

    def check_out(self, user=None):
        from django.utils import timezone
        self.status = "COMPLETED"
        self.check_out_at = timezone.now()
        self.updated_by = user
        self.save(update_fields=["status", "check_out_at", "updated_by", "updated_at"])

    def cancel(self, reason=None, user=None):
        self.status = "CANCELLED"
        self.cancellation_reason = reason
        self.updated_by = user
        self.save(update_fields=["status", "cancellation_reason", "updated_by", "updated_at"])

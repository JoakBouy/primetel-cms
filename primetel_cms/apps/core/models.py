"""
Primetel CMS — Core Models
Shared base models, audit trail, system configuration.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class TimestampedModel(models.Model):
    """
    Abstract base model providing:
    - UUID primary key
    - created_at / updated_at timestamps
    - created_by / updated_by user tracking
    All clinical and financial models inherit from this.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(app_label)s_%(class)s_created",
        verbose_name=_("Created By"),
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(app_label)s_%(class)s_updated",
        verbose_name=_("Updated By"),
    )

    class Meta:
        abstract = True


class ReasonCode(TimestampedModel):
    """
    Polymorphic reason codes used across multiple apps:
    cancellations, no-shows, stock adjustments, waivers, etc.
    """

    CATEGORY_CHOICES = [
        ("CANCELLATION", _("Cancellation")),
        ("NO_SHOW", _("No Show")),
        ("STOCK_ADJUSTMENT", _("Stock Adjustment")),
        ("WAIVER", _("Waiver")),
        ("OTHER", _("Other")),
    ]

    code = models.CharField(max_length=50, unique=True)
    display_name = models.CharField(max_length=100)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, db_index=True)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "display_name"]
        verbose_name = _("Reason Code")
        verbose_name_plural = _("Reason Codes")

    def __str__(self):
        return f"[{self.category}] {self.display_name}"


class ConfigSetting(models.Model):
    """
    Key-value store for runtime configuration.
    E.g. clinic name, currency, timezone, VAT rate.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField()
    description = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]
        verbose_name = _("Config Setting")
        verbose_name_plural = _("Config Settings")

    def __str__(self):
        return f"{self.key} = {self.value[:50]}"

    @classmethod
    def get(cls, key, default=None):
        """Get a config value by key."""
        try:
            return cls.objects.get(key=key).value
        except cls.DoesNotExist:
            return default


class Notification(models.Model):
    """In-app notification. One row per recipient — fan-out happens at write time
    so unread counts and per-user state stay simple.

    `kind` is a stable string the UI uses to pick an icon/colour. Keep the list
    short and meaningful; new kinds should be added intentionally.
    """

    KIND_CHOICES = [
        ("PAYMENT_RECEIVED", _("Payment received")),
        ("PAYMENT_REQUIRED", _("Payment required")),
        ("LAB_RESULT_READY", _("Lab result ready")),
        ("LAB_CRITICAL", _("Critical lab result")),
        ("RX_READY", _("Prescription ready to dispense")),
        ("RX_DISPENSED", _("Prescription dispensed")),
        ("ENCOUNTER_AMENDED", _("Encounter amended")),
        ("INFO", _("Info")),
    ]

    LEVEL_CHOICES = [
        ("INFO", _("Info")),
        ("SUCCESS", _("Success")),
        ("WARNING", _("Warning")),
        ("CRITICAL", _("Critical")),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    kind = models.CharField(max_length=30, choices=KIND_CHOICES, db_index=True)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default="INFO")
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, default="")
    # Where the notification leads when clicked. Free-form so we don't have to
    # add a column every time a new target type appears.
    url = models.CharField(max_length=500, blank=True, default="")
    # Optional pointer back to the entity that triggered this — handy for
    # de-duplication and for the actor view ("don't notify me about my own
    # action").
    entity_type = models.CharField(max_length=100, blank=True, default="", db_index=True)
    entity_id = models.UUIDField(null=True, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"]),
        ]

    def __str__(self):
        return f"[{self.kind}] {self.title} -> {self.recipient}"


class AuditLog(models.Model):
    """
    Comprehensive audit log capturing reads, writes, exports, and auth events.
    Supplements django-simple-history (which only captures writes).
    """

    ACTION_CHOICES = [
        ("READ", _("Read")),
        ("CREATE", _("Create")),
        ("UPDATE", _("Update")),
        ("DELETE", _("Delete")),
        ("EXPORT", _("Export")),
        ("LOGIN", _("Login")),
        ("LOGIN_FAILED", _("Login Failed")),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, db_index=True)
    entity_type = models.CharField(max_length=100, db_index=True)
    entity_id = models.UUIDField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = _("Audit Log")
        verbose_name_plural = _("Audit Logs")
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["actor", "timestamp"]),
        ]

    def __str__(self):
        return f"{self.action} {self.entity_type} by {self.actor} at {self.timestamp}"

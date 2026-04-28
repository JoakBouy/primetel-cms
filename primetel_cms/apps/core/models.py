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

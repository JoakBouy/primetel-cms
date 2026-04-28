"""
Primetel CMS — Accounts Models
Custom User model with UUID PK, Role-based access control.
"""
import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class Role(models.Model):
    """
    Role model for RBAC. Roles are seeded via migration and should not
    be modified through the UI except by administrators.
    """

    CLINICIAN = "CLINICIAN"
    NURSE = "NURSE"
    COUNSELLOR = "COUNSELLOR"
    RECEPTIONIST = "RECEPTIONIST"
    PHARMACY = "PHARMACY"
    LAB = "LAB"
    ADMIN = "ADMIN"
    FINANCE = "FINANCE"

    ROLE_CHOICES = [
        (CLINICIAN, _("Clinician")),
        (NURSE, _("Nurse")),
        (COUNSELLOR, _("Counsellor")),
        (RECEPTIONIST, _("Receptionist")),
        (PHARMACY, _("Pharmacy")),
        (LAB, _("Lab")),
        (ADMIN, _("Admin")),
        (FINANCE, _("Finance")),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=20, unique=True, choices=ROLE_CHOICES)
    display_name = models.CharField(max_length=50)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_name"]
        verbose_name = _("Role")
        verbose_name_plural = _("Roles")

    def __str__(self):
        return self.display_name


class User(AbstractUser):
    """
    Custom User model for Primetel CMS.
    Uses UUID primary key. Role-based access enforced server-side.
    """

    LANGUAGE_CHOICES = [
        ("sw", "Kiswahili"),
        ("en", "English"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    full_name = models.CharField(_("Full Name"), max_length=255)
    phone = models.CharField(
        _("Phone Number"),
        max_length=20,
        blank=True,
        default="",
        help_text=_("E.164 format, e.g. +255712345678"),
    )
    role = models.ForeignKey(
        Role,
        on_delete=models.PROTECT,
        related_name="users",
        verbose_name=_("Role"),
        null=True,
        blank=True,
    )
    language_preference = models.CharField(
        _("Language Preference"),
        max_length=5,
        choices=LANGUAGE_CHOICES,
        default="sw",
    )

    class Meta:
        verbose_name = _("User")
        verbose_name_plural = _("Users")
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name or self.username

    @property
    def role_code(self):
        """Convenience accessor for the role code string."""
        return self.role.code if self.role else None

    def has_role(self, *role_codes):
        """Check if user has any of the given role codes."""
        if self.is_superuser:
            return True
        return self.role_code in role_codes

    def save(self, *args, **kwargs):
        # Auto-populate full_name from first/last if not set
        if not self.full_name and (self.first_name or self.last_name):
            self.full_name = f"{self.first_name} {self.last_name}".strip()
        super().save(*args, **kwargs)

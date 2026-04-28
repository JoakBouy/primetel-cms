"""
Primetel CMS — Patients Models
Patient registration, demographics, soft-delete, fuzzy search.
"""
import uuid
from datetime import date

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import TimestampedModel


class PatientManager(models.Manager):
    """Default manager — excludes soft-deleted patients."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)

    def all_including_deleted(self):
        return super().get_queryset()

    def search(self, query):
        """
        Fuzzy search across full_name, phone, patient_number, national_id.
        Falls back to icontains for SQLite (dev); uses trigram for Postgres (prod).
        """
        if not query:
            return self.get_queryset()
        qs = self.get_queryset()
        from django.db import connection
        if connection.vendor == "postgresql":
            from django.contrib.postgres.search import TrigramSimilarity
            return (
                qs.annotate(
                    name_sim=TrigramSimilarity("full_name", query),
                    phone_sim=TrigramSimilarity("phone", query),
                )
                .filter(
                    models.Q(name_sim__gt=0.2)
                    | models.Q(phone_sim__gt=0.3)
                    | models.Q(patient_number__icontains=query)
                    | models.Q(national_id__icontains=query)
                )
                .order_by("-name_sim")
            )
        else:
            return qs.filter(
                models.Q(full_name__icontains=query)
                | models.Q(phone__icontains=query)
                | models.Q(patient_number__icontains=query)
                | models.Q(national_id__icontains=query)
            )


def generate_patient_number():
    """Auto-generate patient number in format PT-YYYY-NNNNNN."""
    from django.utils import timezone
    year = timezone.now().year
    prefix = f"PT-{year}-"
    last = (
        Patient.objects.all_including_deleted()
        .filter(patient_number__startswith=prefix)
        .order_by("-patient_number")
        .first()
    )
    if last:
        try:
            seq = int(last.patient_number.split("-")[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:06d}"


class Patient(TimestampedModel):
    """
    Patient — core clinical entity.
    All clinical identifiers are soft-deleted, never hard-deleted.
    """

    SEX_CHOICES = [
        ("M", _("Male")),
        ("F", _("Female")),
        ("O", _("Other")),
    ]

    LANGUAGE_CHOICES = [
        ("sw", "Kiswahili"),
        ("en", "English"),
    ]

    # Identity
    patient_number = models.CharField(
        _("Patient Number"), max_length=20, unique=True, db_index=True
    )
    full_name = models.CharField(_("Full Name"), max_length=255)
    date_of_birth = models.DateField(_("Date of Birth"), null=True, blank=True)
    estimated_age = models.PositiveSmallIntegerField(
        _("Estimated Age (years)"),
        null=True,
        blank=True,
        help_text=_("Used when date of birth is unknown"),
    )
    sex = models.CharField(_("Sex"), max_length=1, choices=SEX_CHOICES)
    national_id = models.CharField(
        _("National ID"), max_length=50, null=True, blank=True, unique=True
    )
    photo = models.ImageField(
        _("Photo"),
        upload_to="patient_photos/%Y/%m/",
        null=True,
        blank=True,
    )

    # Contact
    phone = models.CharField(
        _("Phone"),
        max_length=20,
        blank=True,
        default="",
        db_index=True,
        help_text=_("E.164 format, e.g. +255712345678"),
    )
    village = models.CharField(_("Village"), max_length=100, blank=True, default="")
    ward = models.CharField(_("Ward"), max_length=100, blank=True, default="")
    district = models.CharField(_("District"), max_length=100, default="Monduli")
    region = models.CharField(_("Region"), max_length=100, default="Arusha")

    # Next of kin
    next_of_kin_name = models.CharField(
        _("Next of Kin Name"), max_length=255, blank=True, default=""
    )
    next_of_kin_phone = models.CharField(
        _("Next of Kin Phone"), max_length=20, blank=True, default=""
    )
    next_of_kin_relationship = models.CharField(
        _("Relationship"), max_length=100, blank=True, default=""
    )

    # Clinical flags
    is_pregnant = models.BooleanField(_("Pregnant"), default=False)
    chronic_conditions = models.TextField(
        _("Chronic Conditions"), blank=True, default=""
    )
    allergies = models.TextField(_("Allergies"), blank=True, default="")
    notes = models.TextField(_("Notes"), blank=True, default="")

    # Preferences
    language_preference = models.CharField(
        _("Language Preference"),
        max_length=5,
        choices=LANGUAGE_CHOICES,
        default="sw",
    )

    # Soft delete
    is_deleted = models.BooleanField(_("Deleted"), default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deleted_patients",
    )

    # Audit trail
    history = HistoricalRecords()

    # Managers
    objects = PatientManager()

    class Meta:
        ordering = ["full_name"]
        verbose_name = _("Patient")
        verbose_name_plural = _("Patients")
        indexes = [
            models.Index(fields=["patient_number"]),
            models.Index(fields=["phone"]),
            models.Index(fields=["national_id"]),
            models.Index(fields=["full_name"]),
        ]

    def __str__(self):
        return f"{self.patient_number} — {self.full_name}"

    def save(self, *args, **kwargs):
        if not self.patient_number:
            self.patient_number = generate_patient_number()
        super().save(*args, **kwargs)

    def soft_delete(self, user=None):
        from django.utils import timezone
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.deleted_by = user
        self.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])

    # ── Computed properties ──────────────────────
    @property
    def age(self):
        """Current age in years."""
        if self.date_of_birth:
            today = date.today()
            dob = self.date_of_birth
            return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        return self.estimated_age

    @property
    def is_minor(self):
        """True if patient is under 18."""
        age = self.age
        return age is not None and age < 18

    @property
    def age_display(self):
        """Human-readable age string."""
        age = self.age
        if age is None:
            return _("Unknown age")
        suffix = _("yrs")
        return f"{age} {suffix}"

    @property
    def sex_display(self):
        return dict(self.SEX_CHOICES).get(self.sex, self.sex)

    @property
    def has_allergies(self):
        return bool(self.allergies.strip())

    @property
    def chronic_conditions_list(self):
        """Return chronic conditions as a list."""
        if not self.chronic_conditions:
            return []
        return [c.strip() for c in self.chronic_conditions.split(",") if c.strip()]

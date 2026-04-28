"""
Primetel CMS — Encounters Models
SOAP notes, vitals, diagnoses, mental health assessments, attachments.
"""
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import TimestampedModel


# ═══════════════════════════════════════════════════
#  Mental Health Access Restriction (spec §3.2)
# ═══════════════════════════════════════════════════
class EncounterManager(models.Manager):
    """Default manager — filters out MENTAL_HEALTH encounters for non-authorised users."""

    def for_user(self, user):
        """
        Returns queryset scoped to the user's access level.
        Counsellors and Admins can see all; clinicians can see their own MH encounters;
        everyone else only sees non-MH encounters.
        """
        qs = self.get_queryset()
        if user.is_superuser or user.has_role("ADMIN", "COUNSELLOR"):
            return qs
        # Clinicians can see their own MH encounters
        if user.has_role("CLINICIAN"):
            return qs.exclude(
                models.Q(encounter_type="MENTAL_HEALTH") & ~models.Q(clinician=user)
            )
        # Everyone else: no MH encounters
        return qs.exclude(encounter_type="MENTAL_HEALTH")


class MentalHealthEncounterManager(models.Manager):
    """Manager that ONLY returns mental health encounters for authorised users."""

    def for_user(self, user):
        qs = self.get_queryset().filter(encounter_type="MENTAL_HEALTH")
        if user.is_superuser or user.has_role("ADMIN", "COUNSELLOR"):
            return qs
        if user.has_role("CLINICIAN"):
            return qs.filter(clinician=user)
        return qs.none()


class Encounter(TimestampedModel):
    """Clinical encounter — the main working record for clinicians."""

    TYPE_CHOICES = [
        ("GENERAL", _("General")),
        ("MENTAL_HEALTH", _("Mental Health")),
        ("FOLLOW_UP", _("Follow Up")),
        ("ANC", _("Antenatal")),
        ("PEDIATRIC", _("Pediatric")),
    ]

    STATUS_CHOICES = [
        ("DRAFT", _("Draft")),
        ("FINALISED", _("Finalised")),
        ("AMENDED", _("Amended")),
    ]

    LANGUAGE_CHOICES = [
        ("sw", "Kiswahili"),
        ("en", "English"),
        ("mixed", "Mixed"),
    ]

    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.CASCADE, related_name="encounters"
    )
    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="encounters",
    )
    clinician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="encounters",
    )
    encounter_type = models.CharField(
        max_length=15, choices=TYPE_CHOICES, default="GENERAL", db_index=True
    )
    started_at = models.DateTimeField(_("Started At"), auto_now_add=True)
    ended_at = models.DateTimeField(_("Ended At"), null=True, blank=True)

    # SOAP note
    chief_complaint = models.TextField(_("Chief Complaint"), blank=True, default="")
    history_of_presenting_illness = models.TextField(
        _("History of Presenting Illness"), blank=True, default=""
    )
    subjective = models.TextField(_("Subjective"), blank=True, default="")
    objective = models.TextField(_("Objective"), blank=True, default="")
    assessment = models.TextField(_("Assessment"), blank=True, default="")
    plan = models.TextField(_("Plan"), blank=True, default="")

    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="DRAFT", db_index=True
    )
    finalised_at = models.DateTimeField(null=True, blank=True)
    language_used = models.CharField(
        max_length=5, choices=LANGUAGE_CHOICES, default="mixed"
    )

    history = HistoricalRecords()

    # Managers
    objects = EncounterManager()
    mental_health = MentalHealthEncounterManager()

    class Meta:
        ordering = ["-started_at"]
        verbose_name = _("Encounter")
        verbose_name_plural = _("Encounters")
        indexes = [
            models.Index(fields=["patient", "-started_at"]),
            models.Index(fields=["clinician", "-started_at"]),
            models.Index(fields=["encounter_type", "status"]),
        ]

    def __str__(self):
        return f"{self.patient} — {self.encounter_type} ({self.get_status_display()})"

    def finalise(self, user=None):
        """Lock the encounter. After this, only amend is allowed."""
        from django.utils import timezone
        with transaction.atomic():
            locked = Encounter.objects.select_for_update().get(pk=self.pk)
            if locked.status == "FINALISED":
                raise ValidationError(_("Encounter is already finalised."))
            if not locked.chief_complaint:
                raise ValidationError(_("Chief complaint is required before finalising."))
            if not locked.assessment:
                raise ValidationError(_("Assessment (SOAP-A) is required before finalising."))
            locked.status = "FINALISED"
            locked.finalised_at = timezone.now()
            locked.ended_at = timezone.now()
            locked.updated_by = user
            locked.save()
            # Refresh self to reflect committed state
            self.status = locked.status
            self.finalised_at = locked.finalised_at
            self.ended_at = locked.ended_at

    def amend(self, user=None):
        """Reopen a finalised encounter — creates a history entry."""
        with transaction.atomic():
            locked = Encounter.objects.select_for_update().get(pk=self.pk)
            if locked.status != "FINALISED":
                raise ValidationError(_("Only finalised encounters can be amended."))
            locked.status = "AMENDED"
            locked.updated_by = user
            locked.save()
            self.status = locked.status


class Vitals(TimestampedModel):
    """Vital signs — one-to-many with Encounter (rechecks create new rows)."""

    encounter = models.ForeignKey(
        Encounter, on_delete=models.CASCADE, related_name="vitals"
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="recorded_vitals"
    )
    blood_pressure_systolic = models.PositiveSmallIntegerField(
        _("BP Systolic (mmHg)"), null=True, blank=True
    )
    blood_pressure_diastolic = models.PositiveSmallIntegerField(
        _("BP Diastolic (mmHg)"), null=True, blank=True
    )
    temperature = models.DecimalField(
        _("Temperature (°C)"), max_digits=4, decimal_places=1, null=True, blank=True
    )
    pulse = models.PositiveSmallIntegerField(
        _("Pulse (bpm)"), null=True, blank=True
    )
    respiratory_rate = models.PositiveSmallIntegerField(
        _("Respiratory Rate"), null=True, blank=True
    )
    spo2 = models.PositiveSmallIntegerField(
        _("SpO₂ (%)"), null=True, blank=True
    )
    weight_kg = models.DecimalField(
        _("Weight (kg)"), max_digits=5, decimal_places=1, null=True, blank=True
    )
    height_cm = models.DecimalField(
        _("Height (cm)"), max_digits=5, decimal_places=1, null=True, blank=True
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["-recorded_at"]
        verbose_name = _("Vitals")
        verbose_name_plural = _("Vitals")

    def __str__(self):
        return f"Vitals for {self.encounter} at {self.recorded_at}"

    @property
    def bmi(self):
        """BMI computed from weight and height."""
        if self.weight_kg and self.height_cm and self.height_cm > 0:
            height_m = Decimal(str(self.height_cm)) / Decimal("100")
            return round(Decimal(str(self.weight_kg)) / (height_m ** 2), 1)
        return None

    @property
    def bp_display(self):
        if self.blood_pressure_systolic and self.blood_pressure_diastolic:
            return f"{self.blood_pressure_systolic}/{self.blood_pressure_diastolic}"
        return "—"


class ICD10Code(models.Model):
    """Lookup table of ICD-10 codes. Seeded with top 500 outpatient codes."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=10, unique=True, db_index=True)
    description = models.CharField(max_length=255)

    class Meta:
        ordering = ["code"]
        verbose_name = "ICD-10 Code"
        verbose_name_plural = "ICD-10 Codes"

    def __str__(self):
        return f"{self.code} — {self.description}"


class Diagnosis(TimestampedModel):
    """Diagnosis linked to an encounter."""

    encounter = models.ForeignKey(
        Encounter, on_delete=models.CASCADE, related_name="diagnoses"
    )
    icd10_code = models.CharField(
        _("ICD-10 Code"), max_length=10, blank=True, default=""
    )
    description = models.CharField(
        _("Description"), max_length=255, help_text=_("Free text allowed if no ICD code")
    )
    is_primary = models.BooleanField(_("Primary Diagnosis"), default=False)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-is_primary", "description"]
        verbose_name = _("Diagnosis")
        verbose_name_plural = _("Diagnoses")

    def __str__(self):
        prefix = "★ " if self.is_primary else ""
        code = f"[{self.icd10_code}] " if self.icd10_code else ""
        return f"{prefix}{code}{self.description}"


class EncounterAttachment(TimestampedModel):
    """File attachment for an encounter (max 10MB)."""

    encounter = models.ForeignKey(
        Encounter, on_delete=models.CASCADE, related_name="attachments"
    )
    file = models.FileField(
        upload_to="encounter_attachments/%Y/%m/",
        help_text=_("Max 10MB. Allowed: .jpg, .jpeg, .png, .pdf"),
    )
    label = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = _("Attachment")
        verbose_name_plural = _("Attachments")

    def __str__(self):
        return self.label


class MentalHealthAssessment(TimestampedModel):
    """PHQ-9 or GAD-7 assessment, only on MENTAL_HEALTH encounters."""

    INSTRUMENT_CHOICES = [
        ("PHQ9", "PHQ-9 (Depression)"),
        ("GAD7", "GAD-7 (Anxiety)"),
    ]

    PHQ9_BANDS = [
        (0, 4, "Minimal"),
        (5, 9, "Mild"),
        (10, 14, "Moderate"),
        (15, 19, "Moderately Severe"),
        (20, 27, "Severe"),
    ]

    GAD7_BANDS = [
        (0, 4, "Minimal"),
        (5, 9, "Mild"),
        (10, 14, "Moderate"),
        (15, 21, "Severe"),
    ]

    encounter = models.OneToOneField(
        Encounter, on_delete=models.CASCADE, related_name="mental_health_assessment"
    )
    instrument = models.CharField(max_length=5, choices=INSTRUMENT_CHOICES)
    responses = models.JSONField(
        default=list, help_text="Array of integers (0-3), one per question"
    )

    class Meta:
        verbose_name = _("Mental Health Assessment")
        verbose_name_plural = _("Mental Health Assessments")

    def __str__(self):
        return f"{self.instrument} — Score: {self.total_score}"

    @property
    def total_score(self):
        if isinstance(self.responses, list):
            return sum(self.responses)
        return 0

    @property
    def severity_band(self):
        score = self.total_score
        bands = self.PHQ9_BANDS if self.instrument == "PHQ9" else self.GAD7_BANDS
        for low, high, label in bands:
            if low <= score <= high:
                return label
        return "Unknown"

    def clean(self):
        if self.encounter.encounter_type != "MENTAL_HEALTH":
            raise ValidationError(
                _("Mental health assessments can only be attached to MENTAL_HEALTH encounters.")
            )

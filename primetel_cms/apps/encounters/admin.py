"""Encounters Admin."""
from django.contrib import admin
from .models import Encounter, Vitals, Diagnosis, ICD10Code, EncounterAttachment, MentalHealthAssessment

@admin.register(Encounter)
class EncounterAdmin(admin.ModelAdmin):
    list_display = ("patient", "clinician", "encounter_type", "status", "started_at")
    list_filter = ("encounter_type", "status")
    search_fields = ("patient__full_name", "clinician__full_name")
    date_hierarchy = "started_at"

@admin.register(Vitals)
class VitalsAdmin(admin.ModelAdmin):
    list_display = ("encounter", "recorded_by", "recorded_at", "bp_display", "temperature", "pulse")

@admin.register(Diagnosis)
class DiagnosisAdmin(admin.ModelAdmin):
    list_display = ("encounter", "icd10_code", "description", "is_primary")

@admin.register(ICD10Code)
class ICD10CodeAdmin(admin.ModelAdmin):
    list_display = ("code", "description")
    search_fields = ("code", "description")

@admin.register(EncounterAttachment)
class EncounterAttachmentAdmin(admin.ModelAdmin):
    list_display = ("encounter", "label", "uploaded_by", "uploaded_at")

@admin.register(MentalHealthAssessment)
class MentalHealthAssessmentAdmin(admin.ModelAdmin):
    list_display = ("encounter", "instrument", "total_score", "severity_band")

"""
Primetel CMS — Encounters Views
SOAP notes, vitals, diagnoses, prescriptions, lab orders, finalise/amend.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts.decorators import requires_role
from apps.patients.models import Patient

from .models import Encounter, Vitals, Diagnosis


@requires_role("CLINICIAN", "ADMIN")
def encounter_new(request):
    """Start a new encounter for a patient."""
    patient_pk = request.GET.get("patient")
    patient = get_object_or_404(Patient, pk=patient_pk)
    if request.method == "POST":
        encounter = Encounter.objects.create(
            patient=patient,
            clinician=request.user,
            encounter_type=request.POST.get("encounter_type", "GENERAL"),
            chief_complaint=request.POST.get("chief_complaint", ""),
            created_by=request.user,
        )
        return redirect("encounters:detail", pk=encounter.pk)
    return render(request, "encounters/new.html", {
        "page_title": _("New Encounter"),
        "patient": patient,
    })


@requires_role("COUNSELLOR", "ADMIN")
def encounter_new_mh(request):
    """Start a new mental health encounter."""
    patient_pk = request.GET.get("patient")
    patient = get_object_or_404(Patient, pk=patient_pk)
    if request.method == "POST":
        encounter = Encounter.objects.create(
            patient=patient, clinician=request.user,
            encounter_type="MENTAL_HEALTH",
            chief_complaint=request.POST.get("chief_complaint", ""),
            created_by=request.user,
        )
        return redirect("encounters:detail", pk=encounter.pk)
    return render(request, "encounters/new.html", {
        "page_title": _("New Mental Health Encounter"),
        "patient": patient,
        "is_mental_health": True,
    })


@login_required
def encounter_detail(request, pk):
    """Encounter detail — the main clinician screen (SOAP form)."""
    encounter = get_object_or_404(Encounter, pk=pk)
    # Access restriction for mental health encounters
    if encounter.encounter_type == "MENTAL_HEALTH":
        if not request.user.has_role("COUNSELLOR", "ADMIN") and encounter.clinician != request.user:
            raise PermissionDenied
    return render(request, "encounters/detail.html", {
        "page_title": f"{_('Encounter')} — {encounter.patient.full_name}",
        "encounter": encounter,
        "patient": encounter.patient,
        "vitals": encounter.vitals.all(),
        "diagnoses": encounter.diagnoses.all(),
        "prescriptions": encounter.prescriptions.all() if hasattr(encounter, 'prescriptions') else [],
        "lab_orders": encounter.lab_orders.all() if hasattr(encounter, 'lab_orders') else [],
    })


@require_POST
@requires_role("CLINICIAN", "COUNSELLOR", "ADMIN")
def encounter_save_draft(request, pk):
    """Autosave SOAP note — HTMX endpoint."""
    encounter = get_object_or_404(Encounter, pk=pk)
    if encounter.status == "FINALISED":
        return JsonResponse({"error": "Encounter is finalised"}, status=400)
    for field in ["chief_complaint", "history_of_presenting_illness", "subjective", "objective", "assessment", "plan"]:
        val = request.POST.get(field)
        if val is not None:
            setattr(encounter, field, val)
    encounter.updated_by = request.user
    encounter.save()
    if request.headers.get("HX-Request"):
        return render(request, "encounters/partials/save_indicator.html", {"saved_at": timezone.now()})
    return JsonResponse({"status": "saved"})


@require_POST
@requires_role("NURSE", "CLINICIAN", "ADMIN")
def encounter_add_vitals(request, pk):
    """Add vitals to an encounter."""
    encounter = get_object_or_404(Encounter, pk=pk)
    vitals = Vitals.objects.create(
        encounter=encounter, recorded_by=request.user, created_by=request.user,
        blood_pressure_systolic=request.POST.get("bp_systolic") or None,
        blood_pressure_diastolic=request.POST.get("bp_diastolic") or None,
        temperature=request.POST.get("temperature") or None,
        pulse=request.POST.get("pulse") or None,
        respiratory_rate=request.POST.get("respiratory_rate") or None,
        spo2=request.POST.get("spo2") or None,
        weight_kg=request.POST.get("weight_kg") or None,
        height_cm=request.POST.get("height_cm") or None,
    )
    if request.headers.get("HX-Request"):
        return render(request, "encounters/partials/vitals_list.html", {"vitals": encounter.vitals.all()})
    return redirect("encounters:detail", pk=pk)


@require_POST
@requires_role("CLINICIAN", "ADMIN")
def encounter_add_diagnosis(request, pk):
    """Add a diagnosis to an encounter."""
    encounter = get_object_or_404(Encounter, pk=pk)
    Diagnosis.objects.create(
        encounter=encounter, created_by=request.user,
        icd10_code=request.POST.get("icd10_code", ""),
        description=request.POST.get("description", ""),
        is_primary=request.POST.get("is_primary") == "on",
    )
    if request.headers.get("HX-Request"):
        return render(request, "encounters/partials/diagnosis_list.html", {"diagnoses": encounter.diagnoses.all()})
    return redirect("encounters:detail", pk=pk)


@require_POST
@requires_role("CLINICIAN", "ADMIN")
def encounter_finalise(request, pk):
    """Finalise an encounter — lock it."""
    encounter = get_object_or_404(Encounter, pk=pk)
    try:
        encounter.finalise(user=request.user)
        messages.success(request, _("Encounter finalised successfully."))
    except Exception as e:
        messages.error(request, str(e))
    return redirect("encounters:detail", pk=pk)


@require_POST
@requires_role("CLINICIAN", "ADMIN")
def encounter_amend(request, pk):
    """Amend a finalised encounter."""
    encounter = get_object_or_404(Encounter, pk=pk)
    try:
        encounter.amend(user=request.user)
        messages.success(request, _("Encounter reopened for amendment."))
    except Exception as e:
        messages.error(request, str(e))
    return redirect("encounters:detail", pk=pk)

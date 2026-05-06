"""
Primetel CMS — Patient Views
List/search, registration, chart, edit, flag toggle, photo upload.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts.decorators import requires_any_role, requires_role

from .forms import PatientRegistrationForm, PatientSearchForm
from .models import Patient


@never_cache
@login_required
def patient_list(request):
    """Patient list with live fuzzy search via HTMX. Never cached so newly
    registered patients appear immediately."""
    query = request.GET.get("q", "").strip()
    form = PatientSearchForm(initial={"q": query})
    patients = Patient.objects.search(query) if query else Patient.objects.order_by("-created_at")[:50]

    # HTMX partial response — just the table rows
    if request.headers.get("HX-Request"):
        return render(request, "patients/partials/patient_rows.html", {"patients": patients, "query": query})

    return render(request, "patients/list.html", {
        "page_title": _("Patients"),
        "form": form,
        "patients": patients,
        "query": query,
    })


@requires_role("RECEPTIONIST", "NURSE", "CLINICIAN", "ADMIN")
def patient_register(request):
    """Register a new patient."""
    if request.method == "POST":
        form = PatientRegistrationForm(request.POST, request.FILES)
        if form.is_valid():
            patient = form.save(commit=False)
            patient.created_by = request.user
            # If age_only toggle was set, clear DOB
            if form.cleaned_data.get("age_only"):
                patient.date_of_birth = None
            patient.save()
            messages.success(request, _("Patient registered successfully."))
            return redirect("patients:chart", pk=patient.pk)
    else:
        form = PatientRegistrationForm()

    return render(request, "patients/register.html", {
        "page_title": _("Register Patient"),
        "form": form,
    })


@login_required
def patient_chart(request, pk):
    """Patient chart — tabbed view with timeline, encounters, prescriptions, labs, invoices."""
    from apps.encounters.models import Encounter
    from apps.pharmacy.models import Prescription
    from apps.lab.models import LabOrder
    from apps.billing.models import Invoice

    patient = get_object_or_404(Patient, pk=pk)

    # The middleware handles AuditLog READ entry automatically.
    active_tab = request.GET.get("tab", "timeline")

    TABS = [
        ("timeline",      _("Timeline"),      '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>'),
        ("encounters",    _("Encounters"),    '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>'),
        ("prescriptions", _("Prescriptions"), '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"/>'),
        ("labs",          _("Labs"),          '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"/>'),
        ("invoices",      _("Invoices"),      '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z"/>'),
        ("documents",     _("Documents"),     '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"/>'),
    ]

    encounters = (
        Encounter.objects.for_user(request.user)
        .filter(patient=patient)
        .select_related("clinician")
        .order_by("-started_at")
    )
    open_draft = encounters.filter(status="DRAFT").first()
    prescriptions = (
        Prescription.objects.filter(encounter__patient=patient)
        .select_related("drug", "encounter", "prescribed_by")
        .order_by("-prescribed_at")
    )
    lab_orders = (
        LabOrder.objects.filter(encounter__patient=patient)
        .select_related("test", "encounter")
        .order_by("-ordered_at")
    )
    invoices = (
        Invoice.objects.filter(patient=patient)
        .order_by("-issued_at")
    )

    return render(request, "patients/chart.html", {
        "page_title": f"{patient.full_name} — {_('Chart')}",
        "patient": patient,
        "active_tab": active_tab,
        "tabs": TABS,
        "encounters": encounters,
        "open_draft": open_draft,
        "prescriptions": prescriptions,
        "lab_orders": lab_orders,
        "invoices": invoices,
    })


@requires_role("RECEPTIONIST", "NURSE", "CLINICIAN", "ADMIN")
def patient_edit(request, pk):
    """Edit patient demographics."""
    patient = get_object_or_404(Patient, pk=pk)
    if request.method == "POST":
        form = PatientRegistrationForm(request.POST, request.FILES, instance=patient)
        if form.is_valid():
            p = form.save(commit=False)
            p.updated_by = request.user
            p.save()
            messages.success(request, _("Patient record updated."))
            return redirect("patients:chart", pk=patient.pk)
    else:
        form = PatientRegistrationForm(instance=patient)

    return render(request, "patients/edit.html", {
        "page_title": _("Edit Patient"),
        "patient": patient,
        "form": form,
    })


@require_POST
@requires_role("NURSE", "CLINICIAN", "ADMIN")
def patient_flag(request, pk):
    """Toggle is_pregnant flag or update chronic conditions — HTMX endpoint."""
    patient = get_object_or_404(Patient, pk=pk)
    flag = request.POST.get("flag")

    if flag == "pregnant":
        patient.is_pregnant = not patient.is_pregnant
        patient.updated_by = request.user
        patient.save(update_fields=["is_pregnant", "updated_by"])
    elif flag == "chronic":
        new_condition = request.POST.get("condition", "").strip()
        if new_condition:
            existing = patient.chronic_conditions_list
            if new_condition not in existing:
                existing.append(new_condition)
                patient.chronic_conditions = ", ".join(existing)
                patient.updated_by = request.user
                patient.save(update_fields=["chronic_conditions", "updated_by"])

    if request.headers.get("HX-Request"):
        return render(request, "patients/partials/flags.html", {"patient": patient})

    return redirect("patients:chart", pk=patient.pk)


@require_POST
@requires_role("RECEPTIONIST", "NURSE", "CLINICIAN", "ADMIN")
def patient_photo(request, pk):
    """Upload or replace patient photo — HTMX endpoint."""
    patient = get_object_or_404(Patient, pk=pk)
    photo = request.FILES.get("photo")
    if photo:
        # Validate extension
        from django.conf import settings as django_settings
        import os
        ext = os.path.splitext(photo.name)[1].lower()
        if ext not in [".jpg", ".jpeg", ".png"]:
            return JsonResponse({"error": _("Only JPG and PNG images are allowed.")}, status=400)
        patient.photo = photo
        patient.updated_by = request.user
        patient.save(update_fields=["photo", "updated_by"])

    if request.headers.get("HX-Request"):
        return render(request, "patients/partials/photo.html", {"patient": patient})

    return redirect("patients:chart", pk=patient.pk)

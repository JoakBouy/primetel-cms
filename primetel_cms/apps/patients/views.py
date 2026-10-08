"""
Primetel CMS — Patient Views
List/search, registration, chart, edit, flag toggle, photo upload.
"""
import os
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, F, Max, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts.decorators import requires_any_role, requires_role

from .forms import PatientRegistrationForm, PatientSearchForm
from .models import Patient

PATIENTS_PER_PAGE = 25

# Roles allowed to open a chart. Receptionists register patients and take
# payments but don't see clinical records.
CHART_ROLES = ("NURSE", "CLINICIAN", "COUNSELLOR", "ADMIN", "PHARMACY", "LAB", "FINANCE")
CLINICAL_ROLES = ("NURSE", "CLINICIAN", "COUNSELLOR", "ADMIN")

# Chart tabs each non-clinical role may see (clinical roles see all).
_ROLE_TABS = {
    "PHARMACY": ("prescriptions",),
    "LAB": ("labs",),
    "FINANCE": ("invoices",),
}

SORT_OPTIONS = {
    "recent": ("-created_at", _("Recently registered")),
    "name": ("full_name", _("Name (A–Z)")),
    "number": ("patient_number", _("Patient number")),
    "last_visit": ("-last_visit", _("Most recent visit")),
}


def _visit_filter(user):
    """Encounters counted in the list: hide mental-health visits from users
    who may not see them (counts would reveal they exist)."""
    if user.is_superuser or user.has_role("ADMIN", "COUNSELLOR"):
        return Q()
    if user.has_role("CLINICIAN"):
        return ~Q(encounters__encounter_type="MENTAL_HEALTH") | Q(encounters__clinician=user)
    return ~Q(encounters__encounter_type="MENTAL_HEALTH")


def _filtered_patients(request):
    """Apply search, filters and sort from the query string."""
    query = request.GET.get("q", "").strip()
    qs = Patient.objects.search(query) if query else Patient.objects.all()

    sex = request.GET.get("sex", "")
    if sex in dict(Patient.SEX_CHOICES):
        qs = qs.filter(sex=sex)
    district = request.GET.get("district", "").strip()
    if district:
        qs = qs.filter(district__iexact=district)
    flag = request.GET.get("flag", "")
    today = timezone.localdate()
    if flag == "pregnant":
        qs = qs.filter(is_pregnant=True)
    elif flag == "allergy":
        qs = qs.exclude(allergies="")
    elif flag == "chronic":
        qs = qs.exclude(chronic_conditions="")
    elif flag == "minor":
        try:
            eighteen_years_ago = today.replace(year=today.year - 18)
        except ValueError:  # 29 February
            eighteen_years_ago = today.replace(year=today.year - 18, day=28)
        qs = qs.filter(
            Q(date_of_birth__gt=eighteen_years_ago)
            | Q(date_of_birth__isnull=True, estimated_age__lt=18)
        )

    visit_q = _visit_filter(request.user)
    qs = qs.annotate(
        visit_count=Count("encounters", filter=visit_q, distinct=True),
        last_visit=Max("encounters__started_at", filter=visit_q),
    )

    visited = request.GET.get("visited", "")
    if visited == "never":
        qs = qs.filter(visit_count=0)
    elif visited == "30d":
        qs = qs.filter(last_visit__gte=timezone.now() - timedelta(days=30))
    elif visited == "inactive":
        # Seen before, but not in the last 6 months — follow-up candidates.
        qs = qs.filter(visit_count__gt=0, last_visit__lt=timezone.now() - timedelta(days=182))

    sort = request.GET.get("sort", "")
    if sort not in SORT_OPTIONS:
        sort = "relevance" if query else "recent"
    if sort != "relevance":
        field = SORT_OPTIONS[sort][0]
        if field == "-last_visit":
            qs = qs.order_by(F("last_visit").desc(nulls_last=True), "full_name")
        else:
            qs = qs.order_by(field, "pk")
    return qs, query, sort


@never_cache
@requires_any_role
def patient_list(request):
    """All patients — searchable, filterable, paginated (HTMX live search).
    Never cached so newly registered patients appear immediately."""
    qs, query, sort = _filtered_patients(request)
    page = Paginator(qs, PATIENTS_PER_PAGE).get_page(request.GET.get("page"))
    context = {
        "page_title": _("Patients"),
        "form": PatientSearchForm(initial={"q": query}),
        "patients": page.object_list,
        "page_obj": page,
        "total_count": page.paginator.count,
        "query": query,
        "sort": sort,
        "sort_options": [(key, label) for key, (_f, label) in SORT_OPTIONS.items()],
        "districts": (
            Patient.objects.exclude(district="").order_by("district")
            .values_list("district", flat=True).distinct()
        ),
        "sex_choices": Patient.SEX_CHOICES,
        "hx_target": "#patient-results",
    }
    # HTMX partial response — just the table rows + pagination
    if request.headers.get("HX-Request"):
        return render(request, "patients/partials/patient_rows.html", context)
    return render(request, "patients/list.html", context)


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
            if request.user.has_role("RECEPTIONIST") and not request.user.is_superuser:
                return redirect(f"/billing/invoices/new/?patient={patient.pk}")
            return redirect("patients:chart", pk=patient.pk)
    else:
        form = PatientRegistrationForm()

    return render(request, "patients/register.html", {
        "page_title": _("Register Patient"),
        "form": form,
    })


def _chart_capabilities(user):
    """What the chart sidebar may offer this user (mirrors view permissions)."""
    return {
        "start_consult": user.has_role("NURSE", "CLINICIAN", "ADMIN"),
        "start_mh": user.has_role("COUNSELLOR", "ADMIN"),
        "book_appointment": user.has_role("RECEPTIONIST", "NURSE", "ADMIN"),
        "invoices": user.has_role("RECEPTIONIST", "FINANCE", "ADMIN"),
        "edit": user.has_role("NURSE", "CLINICIAN", "ADMIN"),
        "refer_mh": user.has_role("RECEPTIONIST", "NURSE", "CLINICIAN", "ADMIN"),
        "clinical": user.has_role(*CLINICAL_ROLES),
    }


@requires_role(*CHART_ROLES)
def patient_chart(request, pk):
    """Patient chart — tabbed view with timeline, encounters, prescriptions,
    labs, invoices and documents. Clinical tabs are limited to clinical roles;
    pharmacy, lab and finance staff see only their own tab. Mental-health
    records follow the same visibility rules everywhere on the chart."""
    from apps.encounters.models import Encounter, EncounterAttachment
    from apps.pharmacy.models import Prescription
    from apps.lab.models import LabOrder
    from apps.billing.models import Invoice

    patient = get_object_or_404(Patient, pk=pk)
    user = request.user
    can = _chart_capabilities(user)

    # The middleware handles AuditLog READ entry automatically.
    ALL_TABS = [
        ("timeline",      _("Timeline"),      '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>'),
        ("encounters",    _("Encounters"),    '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>'),
        ("prescriptions", _("Prescriptions"), '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"/>'),
        ("labs",          _("Labs"),          '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"/>'),
        ("invoices",      _("Invoices"),      '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z"/>'),
        ("documents",     _("Documents"),     '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"/>'),
    ]
    if can["clinical"]:
        allowed_tabs = [t[0] for t in ALL_TABS]
    else:
        allowed_tabs = list(_ROLE_TABS.get(user.role_code, ()))
    tabs = [t for t in ALL_TABS if t[0] in allowed_tabs]
    active_tab = request.GET.get("tab") or (allowed_tabs[0] if allowed_tabs else "")
    if active_tab not in allowed_tabs:
        active_tab = allowed_tabs[0] if allowed_tabs else ""

    visible_encounters = Encounter.objects.for_user(user).filter(patient=patient)
    context = {
        "page_title": f"{patient.full_name} — {_('Chart')}",
        "patient": patient,
        "active_tab": active_tab,
        "tabs": tabs,
        "can": can,
        "encounters": [],
        "open_draft": None,
        "prescriptions": [],
        "lab_orders": [],
        "invoices": [],
        "documents": [],
    }

    if can["clinical"]:
        encounters = visible_encounters.select_related("clinician").order_by("-started_at")
        context["encounters"] = encounters
        context["open_draft"] = encounters.filter(status="DRAFT").first()
        context["documents"] = (
            EncounterAttachment.objects.filter(encounter__in=visible_encounters)
            .select_related("encounter", "uploaded_by")
            .order_by("-uploaded_at")
        )
    if "prescriptions" in allowed_tabs:
        context["prescriptions"] = (
            Prescription.objects.filter(encounter__in=visible_encounters)
            .select_related("drug", "encounter", "prescribed_by")
            .order_by("-prescribed_at")
        )
    if "labs" in allowed_tabs:
        context["lab_orders"] = (
            LabOrder.objects.filter(encounter__in=visible_encounters)
            .select_related("test", "encounter")
            .order_by("-ordered_at")
        )
    if "invoices" in allowed_tabs:
        context["invoices"] = Invoice.objects.filter(patient=patient).order_by("-issued_at")

    return render(request, "patients/chart.html", context)


@requires_role("NURSE", "CLINICIAN", "ADMIN")
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
@requires_role("NURSE", "CLINICIAN", "ADMIN")
def patient_photo(request, pk):
    """Upload or replace patient photo — HTMX endpoint."""
    patient = get_object_or_404(Patient, pk=pk)
    photo = request.FILES.get("photo")
    if photo:
        ext = os.path.splitext(photo.name)[1].lower()
        if ext not in [".jpg", ".jpeg", ".png"]:
            return JsonResponse({"error": _("Only JPG and PNG images are allowed.")}, status=400)
        if photo.size > 10 * 1024 * 1024:
            return JsonResponse({"error": _("Photo is larger than 10 MB.")}, status=400)
        from PIL import Image
        try:
            Image.open(photo).verify()
        except Exception:
            return JsonResponse({"error": _("The image file could not be read.")}, status=400)
        photo.seek(0)
        patient.photo = photo
        patient.updated_by = request.user
        patient.save(update_fields=["photo", "updated_by"])

    if request.headers.get("HX-Request"):
        return render(request, "patients/partials/photo.html", {"patient": patient})

    return redirect("patients:chart", pk=patient.pk)

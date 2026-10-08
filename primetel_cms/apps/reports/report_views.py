"""
Reports section — clinic summary for a day / week / month / quarter / year /
custom range (with comparison to the previous period), and the visit register
listing every visit across all patients. Both export to CSV.
"""
import csv

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.decorators import requires_role
from apps.core.context_processors import _nav_visibility
from apps.core.utils import audit, parse_uuid
from apps.encounters.models import Encounter

from .metrics import (
    clinical_metrics, financial_metrics, headline, lab_metrics, pct_change, pharmacy_metrics,
)
from .periods import PERIOD_CHOICES, period_from_request

REPORT_ROLES = ("ADMIN", "FINANCE", "RECEPTIONIST", "CLINICIAN", "COUNSELLOR")
VISIT_REGISTER_ROLES = ("ADMIN", "CLINICIAN", "COUNSELLOR")


def report_sections(user):
    """Which report sections a user may see (mirrors the nav permissions)."""
    nav = _nav_visibility(user)
    sections = []
    if nav.get("encounters"):
        sections.append("clinical")
    if nav.get("billing"):
        sections.append("financial")
    if nav.get("pharmacy"):
        sections.append("pharmacy")
    if nav.get("lab"):
        sections.append("lab")
    return sections


def _csv_response(filename, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("﻿")  # BOM so Excel opens UTF-8 (Swahili names) correctly
    writer = csv.writer(response)
    for row in rows:
        writer.writerow(row)
    return response


def _summary_csv_rows(period, data):
    rows = [["Section", "Metric", "Value"], ["Period", "Range", period.label]]
    clinical = data.get("clinical")
    if clinical:
        for key in ("visits", "unique_patients", "new_patients_seen", "returning_patients",
                    "new_registrations", "open_drafts"):
            rows.append(["Clinical", key.replace("_", " "), clinical[key]])
        for row in clinical["by_type"]:
            rows.append(["Visits by type", row["label"], row["n"]])
        for row in clinical["by_clinician"]:
            rows.append(["Visits by clinician", row["label"], row["n"]])
        for row in clinical["top_diagnoses"]:
            label = " ".join(filter(None, [row["icd10_code"], row["description"]]))
            rows.append(["Top diagnoses", label, row["n"]])
        sexes = clinical["demographics"]["sexes"]
        for row in clinical["demographics"]["rows"]:
            for sex, count in zip(sexes, row["counts"]):
                rows.append(["Patients seen by age/sex", f"{row['band']} {sex}", count])
    financial = data.get("financial")
    if financial:
        for key in ("collected", "reversed", "waived", "billed", "invoices_issued",
                    "outstanding_total", "outstanding_count"):
            rows.append(["Financial", key.replace("_", " "), financial[key]])
        for row in financial["by_method"]:
            rows.append(["Payments by method", row["label"], row["total"]])
        for row in financial["billed_by_category"]:
            rows.append(["Billed by category", row["label"], row["total"]])
    pharmacy = data.get("pharmacy")
    if pharmacy:
        for key in ("rx_written", "rx_cancelled", "rx_dispensed", "units_dispensed", "stock_losses_units"):
            rows.append(["Pharmacy", key.replace("_", " "), pharmacy[key]])
        for row in pharmacy["top_drugs"]:
            label = f"{row['prescription__drug__generic_name']} {row['prescription__drug__strength']}"
            rows.append(["Top drugs dispensed (units)", label, row["units"]])
    lab = data.get("lab")
    if lab:
        for key in ("orders", "resulted", "pending", "cancelled", "abnormal", "critical", "tat_median_hours"):
            value = lab[key]
            rows.append(["Lab", key.replace("_", " "), "" if value is None else value])
        for row in lab["top_tests"]:
            rows.append(["Top lab tests", f"{row['test__code']} {row['test__name']}", row["n"]])
    return rows


@requires_role(*REPORT_ROLES)
def reports_index(request):
    """Clinic reports for a chosen period with comparison to the previous one.

    ?period=day|week|month|quarter|year (&date=YYYY-MM-DD, any day inside it)
    ?period=custom&start=YYYY-MM-DD&end=YYYY-MM-DD
    ?export=csv downloads the same figures.
    """
    period = period_from_request(request, default="month")
    sections = report_sections(request.user)

    data = {}
    if "clinical" in sections:
        data["clinical"] = clinical_metrics(request.user, period)
    if "financial" in sections:
        data["financial"] = financial_metrics(period)
    if "pharmacy" in sections:
        data["pharmacy"] = pharmacy_metrics(period)
    if "lab" in sections:
        data["lab"] = lab_metrics(request.user, period)

    if request.GET.get("export") == "csv":
        audit(request, "EXPORT", entity_type="Report", report="clinic_summary", period=period.label)
        return _csv_response(
            f"primetel-report-{period.start.isoformat()}-to-{period.end.isoformat()}.csv",
            _summary_csv_rows(period, data),
        )

    current = headline(request.user, period, sections)
    previous = headline(request.user, period.previous, sections)
    kpi_labels = {
        "visits": _("Visits"),
        "unique_patients": _("Patients seen"),
        "new_registrations": _("New registrations"),
        "collected": _("Collected (TZS)"),
    }
    kpis = [
        {
            "key": key,
            "label": label,
            "value": current[key],
            "previous": previous.get(key),
            "change": pct_change(current[key], previous.get(key)),
            "money": key == "collected",
        }
        for key, label in kpi_labels.items()
        if key in current
    ]

    trend = None
    if "clinical" in data or "financial" in data:
        source = data.get("clinical") or data.get("financial")
        trend = {
            "labels": source["trend"]["labels"],
            "visits": data["clinical"]["trend"]["data"] if "clinical" in data else None,
            "revenue": data["financial"]["trend"]["data"] if "financial" in data else None,
        }

    return render(request, "reports/index.html", {
        "page_title": _("Reports"),
        "period": period,
        "period_choices": PERIOD_CHOICES,
        "sections": sections,
        "kpis": kpis,
        "trend": trend,
        "can_view_register": request.user.has_role(*VISIT_REGISTER_ROLES),
        **data,
    })


def _visit_register_queryset(request, period):
    qs = (
        Encounter.objects.for_user(request.user)
        .filter(started_at__gte=period.start_dt, started_at__lt=period.end_dt)
        .select_related("patient", "clinician")
        .prefetch_related("diagnoses")
        .order_by("-started_at")
    )
    query = request.GET.get("q", "").strip()
    if query:
        matching = Encounter.objects.filter(
            Q(patient__full_name__icontains=query)
            | Q(patient__patient_number__icontains=query)
            | Q(patient__phone__icontains=query)
            | Q(diagnoses__description__icontains=query)
            | Q(diagnoses__icd10_code__icontains=query)
        ).values("pk")
        qs = qs.filter(pk__in=matching)
    encounter_type = request.GET.get("type", "")
    if encounter_type in dict(Encounter.TYPE_CHOICES):
        qs = qs.filter(encounter_type=encounter_type)
    status = request.GET.get("status", "")
    if status in dict(Encounter.STATUS_CHOICES):
        qs = qs.filter(status=status)
    clinician_pk = parse_uuid(request.GET.get("clinician"))
    if clinician_pk:
        qs = qs.filter(clinician_id=clinician_pk)
    return qs, query


@requires_role(*VISIT_REGISTER_ROLES)
def visit_register(request):
    """Every visit (encounter) across all patients for a period — searchable,
    filterable, paginated and exportable. Mental-health visits only appear
    for users allowed to see them."""
    period = period_from_request(request, default="month")
    qs, query = _visit_register_queryset(request, period)

    if request.GET.get("export") == "csv":
        audit(
            request, "EXPORT", entity_type="Report", report="visit_register", period=period.label,
            filters={k: v for k, v in request.GET.items() if k != "export"},
        )
        rows = [[
            "Date", "Patient number", "Patient", "Sex", "Age", "Visit type", "Status",
            "Clinician", "Chief complaint", "Diagnoses",
        ]]
        for enc in qs:
            patient = enc.patient
            rows.append([
                timezone.localtime(enc.started_at).strftime("%Y-%m-%d %H:%M"),
                patient.patient_number,
                patient.full_name,
                patient.get_sex_display(),
                "" if patient.age is None else patient.age,
                enc.get_encounter_type_display(),
                enc.get_status_display(),
                (enc.clinician.full_name or enc.clinician.username) if enc.clinician_id else "",
                enc.chief_complaint,
                "; ".join(str(d) for d in enc.diagnoses.all()),
            ])
        return _csv_response(
            f"visit-register-{period.start.isoformat()}-to-{period.end.isoformat()}.csv", rows,
        )

    clinicians = (
        get_user_model().objects.filter(encounters__isnull=False).distinct().order_by("full_name", "username")
    )
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "reports/visit_register.html", {
        "page_title": _("Visit register"),
        "period": period,
        "period_choices": PERIOD_CHOICES,
        "encounters": page.object_list,
        "page_obj": page,
        "total_count": page.paginator.count,
        "query": query,
        "type_choices": Encounter.TYPE_CHOICES,
        "status_choices": Encounter.STATUS_CHOICES,
        "clinicians": clinicians,
    })

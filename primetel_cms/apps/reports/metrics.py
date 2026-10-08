"""
Report calculations for a Period.

Each function returns plain dicts/lists ready for templates and CSV export.
Clinical figures always go through Encounter.objects.for_user(), so
mental-health visits are only counted for users allowed to see them.
Volumes at a single clinic are small, so trend bucketing happens in Python
(timezone-correct and identical on SQLite and Postgres).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, F, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.billing.models import Invoice, InvoiceLine, Payment, ServiceItem
from apps.encounters.models import Diagnosis, Encounter
from apps.lab.models import LabResult
from apps.patients.models import Patient
from apps.pharmacy.models import Dispense, Drug, Prescription, StockItem, StockMovement

ZERO = Decimal("0")

AGE_BANDS = [
    (0, 4, _("Under 5")),
    (5, 17, _("5–17")),
    (18, 59, _("18–59")),
    (60, 200, _("60+")),
]


def _in_period(field, period):
    return {f"{field}__gte": period.start_dt, f"{field}__lt": period.end_dt}


def _trend(period, moments, values=None):
    """Sum `values` (or count) per bucket → (labels, data)."""
    totals = defaultdict(lambda: ZERO if values is not None else 0)
    if values is None:
        for m in moments:
            totals[period.bucket_key(m)] += 1
    else:
        for m, v in zip(moments, values):
            totals[period.bucket_key(m)] += v or ZERO
    labels, data = [], []
    for key, label in period.buckets():
        labels.append(label)
        val = totals.get(key, 0)
        data.append(float(val) if isinstance(val, Decimal) else val)
    return labels, data


def pct_change(current, previous):
    if not previous:
        return None
    return round((float(current) - float(previous)) / float(previous) * 100, 1)


def _age_at(patient, on_date):
    if patient.date_of_birth:
        dob = patient.date_of_birth
        return on_date.year - dob.year - ((on_date.month, on_date.day) < (dob.month, dob.day))
    return patient.estimated_age


# ─── Clinical ────────────────────────────────────────────────────

def clinical_metrics(user, period):
    visible = Encounter.objects.for_user(user)
    encs = visible.filter(**_in_period("started_at", period))

    visits = encs.count()
    patient_ids = encs.values("patient_id")
    unique_patients = encs.values("patient_id").distinct().count()
    returning = (
        visible.filter(patient_id__in=patient_ids, started_at__lt=period.start_dt)
        .values("patient_id").distinct().count()
    )
    new_registrations = Patient.objects.filter(**_in_period("created_at", period)).count()

    type_labels = dict(Encounter.TYPE_CHOICES)
    by_type = [
        {"label": type_labels.get(row["encounter_type"], row["encounter_type"]), "n": row["n"]}
        for row in encs.values("encounter_type").annotate(n=Count("id")).order_by("-n")
    ]
    by_clinician = [
        {"label": row["clinician__full_name"] or row["clinician__username"], "n": row["n"],
         "patients": row["patients"]}
        for row in encs.values("clinician__full_name", "clinician__username")
        .annotate(n=Count("id"), patients=Count("patient", distinct=True)).order_by("-n")[:15]
    ]
    status_counts = dict(encs.values_list("status").annotate(n=Count("id")))
    open_drafts = status_counts.get("DRAFT", 0)

    dx = Diagnosis.objects.filter(encounter__in=encs)
    top_diagnoses = list(
        dx.values("icd10_code", "description").annotate(n=Count("id")).order_by("-n", "description")[:10]
    )

    # Demographics of patients seen (age at the end of the period).
    sex_labels = dict(Patient.SEX_CHOICES)
    demo = Counter()
    for p in Patient.objects.filter(pk__in=patient_ids).only("sex", "date_of_birth", "estimated_age"):
        age = _age_at(p, period.end)
        band = next((str(lbl) for lo, hi, lbl in AGE_BANDS if age is not None and lo <= age <= hi), str(_("Unknown")))
        demo[(band, p.sex)] += 1
    bands = [str(lbl) for _lo, _hi, lbl in AGE_BANDS] + [str(_("Unknown"))]
    sexes = [code for code, _l in Patient.SEX_CHOICES]
    demographics = {
        "sexes": [sex_labels[s] for s in sexes],
        "rows": [
            {"band": band, "counts": [demo[(band, s)] for s in sexes], "total": sum(demo[(band, s)] for s in sexes)}
            for band in bands
            if any(demo[(band, s)] for s in sexes) or band != str(_("Unknown"))
        ],
        "totals": [sum(demo[(b, s)] for b in bands) for s in sexes],
    }

    trend_labels, trend_data = _trend(period, encs.values_list("started_at", flat=True))
    return {
        "visits": visits,
        "unique_patients": unique_patients,
        "new_patients_seen": unique_patients - returning,
        "returning_patients": returning,
        "new_registrations": new_registrations,
        "open_drafts": open_drafts,
        "by_type": by_type,
        "by_clinician": by_clinician,
        "top_diagnoses": top_diagnoses,
        "top_dx_max": max((d["n"] for d in top_diagnoses), default=0),
        "demographics": demographics,
        "trend": {"labels": trend_labels, "data": trend_data},
    }


# ─── Financial ───────────────────────────────────────────────────

def _line_category(line) -> str:
    if line["prescription_id"]:
        return "PHARMACY"
    if line["lab_order_id"]:
        return "LAB"
    if line["service_item__category"]:
        return line["service_item__category"]
    if line["description"].startswith("[Prepay]") or "consult" in line["description"].lower():
        return "CONSULT"
    return "OTHER"


def financial_metrics(period):
    payments = Payment.objects.filter(**_in_period("received_at", period))
    cash = payments.exclude(method="WAIVER")
    collected = cash.aggregate(s=Sum("amount_tzs"))["s"] or ZERO
    reversed_total = -(cash.filter(amount_tzs__lt=0).aggregate(s=Sum("amount_tzs"))["s"] or ZERO)
    waived = payments.filter(method="WAIVER").aggregate(s=Sum("amount_tzs"))["s"] or ZERO
    method_labels = dict(Payment.METHOD_CHOICES)
    by_method = [
        {"label": method_labels.get(row["method"], row["method"]), "total": row["total"], "n": row["n"]}
        for row in payments.values("method").annotate(total=Sum("amount_tzs"), n=Count("id")).order_by("-total")
    ]

    invoices = Invoice.objects.filter(**_in_period("issued_at", period)).exclude(status="CANCELLED")
    invoices_issued = invoices.count()
    billed = invoices.aggregate(s=Sum("total_tzs"))["s"] or ZERO

    category_labels = dict(ServiceItem.CATEGORY_CHOICES)
    by_category = defaultdict(lambda: ZERO)
    for line in InvoiceLine.objects.filter(invoice__in=invoices).values(
        "prescription_id", "lab_order_id", "service_item__category", "description", "quantity", "unit_price_tzs",
    ):
        by_category[_line_category(line)] += (line["quantity"] or ZERO) * (line["unit_price_tzs"] or ZERO)
    billed_by_category = sorted(
        ({"label": category_labels.get(k, k), "total": v} for k, v in by_category.items()),
        key=lambda r: -r["total"],
    )

    open_invoices = Invoice.objects.filter(status__in=("DRAFT", "ISSUED", "PARTIALLY_PAID"))
    outstanding = (
        open_invoices.annotate(bal=F("total_tzs") - F("amount_paid_tzs")).filter(bal__gt=0)
        .aggregate(s=Sum("bal"), n=Count("id"))
    )

    pay_rows = list(cash.values_list("received_at", "amount_tzs"))
    trend_labels, trend_data = _trend(period, [r[0] for r in pay_rows], [r[1] for r in pay_rows])
    return {
        "collected": collected,
        "reversed": reversed_total,
        "waived": waived,
        "by_method": by_method,
        "invoices_issued": invoices_issued,
        "billed": billed,
        "billed_by_category": billed_by_category,
        "billed_cat_max": max((r["total"] for r in billed_by_category), default=ZERO),
        "outstanding_total": outstanding["s"] or ZERO,
        "outstanding_count": outstanding["n"] or 0,
        "trend": {"labels": trend_labels, "data": trend_data},
    }


# ─── Pharmacy ────────────────────────────────────────────────────

def pharmacy_metrics(period):
    rx = Prescription.objects.filter(**_in_period("prescribed_at", period))
    dispenses = Dispense.objects.filter(**_in_period("dispensed_at", period))
    top_drugs = list(
        dispenses.values("prescription__drug__generic_name", "prescription__drug__strength")
        .annotate(units=Sum("quantity_dispensed"), n=Count("prescription", distinct=True))
        .order_by("-units")[:10]
    )
    losses = StockMovement.objects.filter(
        **_in_period("performed_at", period),
        movement_type__in=("ADJUST", "WRITE_OFF"),
        quantity__lt=0,
    ).exclude(notes__startswith="Void of dispensed prescription")
    today = timezone.localdate()
    return {
        "rx_written": rx.count(),
        "rx_cancelled": rx.filter(status="CANCELLED").count(),
        "rx_dispensed": dispenses.values("prescription_id").distinct().count(),
        "units_dispensed": dispenses.aggregate(s=Sum("quantity_dispensed"))["s"] or 0,
        "top_drugs": top_drugs,
        "top_drug_max": max((d["units"] for d in top_drugs), default=0),
        "stock_losses_units": -(losses.aggregate(s=Sum("quantity"))["s"] or 0),
        "stock_losses_count": losses.count(),
        "low_stock_now": sum(1 for d in Drug.objects.filter(is_active=True).prefetch_related("stock_items") if d.is_low_stock),
        "expiring_90d_now": StockItem.objects.filter(
            quantity_on_hand__gt=0, expiry_date__gte=today, expiry_date__lte=today + timedelta(days=90),
        ).count(),
    }


# ─── Lab ─────────────────────────────────────────────────────────

def lab_metrics(user, period):
    from apps.lab.views import _median, _orders_for_user

    orders = _orders_for_user(user).filter(**_in_period("ordered_at", period))
    status_counts = dict(orders.values_list("status").annotate(n=Count("id")))
    top_tests = list(
        orders.exclude(status="CANCELLED").values("test__code", "test__name")
        .annotate(n=Count("id")).order_by("-n")[:10]
    )
    results = LabResult.objects.filter(lab_order__in=orders)
    flags = dict(results.values_list("flag").annotate(n=Count("id")))
    tat = [
        (o.resulted_at - o.ordered_at).total_seconds()
        for o in orders.exclude(resulted_at__isnull=True).only("ordered_at", "resulted_at")
        if o.resulted_at >= o.ordered_at
    ]
    median = _median(tat)
    return {
        "orders": sum(status_counts.values()),
        "cancelled": status_counts.get("CANCELLED", 0),
        "pending": status_counts.get("ORDERED", 0) + status_counts.get("COLLECTED", 0),
        "resulted": status_counts.get("RESULTED", 0) + status_counts.get("REVIEWED", 0),
        "abnormal": flags.get("LOW", 0) + flags.get("HIGH", 0),
        "critical": flags.get("CRITICAL", 0),
        "top_tests": top_tests,
        "top_test_max": max((t["n"] for t in top_tests), default=0),
        "tat_median_hours": round(median / 3600, 1) if median is not None else None,
    }


def headline(user, period, sections):
    """Key numbers for the KPI strip (used for current and previous period)."""
    out = {}
    if "clinical" in sections:
        encs = Encounter.objects.for_user(user).filter(**_in_period("started_at", period))
        out["visits"] = encs.count()
        out["unique_patients"] = encs.values("patient_id").distinct().count()
        out["new_registrations"] = Patient.objects.filter(**_in_period("created_at", period)).count()
    if "financial" in sections:
        out["collected"] = (
            Payment.objects.filter(**_in_period("received_at", period)).exclude(method="WAIVER")
            .aggregate(s=Sum("amount_tzs"))["s"] or ZERO
        )
    return out

